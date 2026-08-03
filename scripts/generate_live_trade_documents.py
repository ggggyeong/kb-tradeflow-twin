from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

from pypdf import PdfReader
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.pdfbase.pdfmetrics import stringWidth
from reportlab.pdfgen.canvas import Canvas

PROJECT_ROOT = Path(__file__).resolve().parents[1]
OUTPUT_DIR = PROJECT_ROOT / "data" / "fixtures" / "live_trade_documents"
MANIFEST_PATH = OUTPUT_DIR / "manifest.json"

NAVY = colors.HexColor("#082F49")
TEAL = colors.HexColor("#0F766E")
PALE = colors.HexColor("#ECFDF5")
INK = colors.HexColor("#172033")
MUTED = colors.HexColor("#64748B")
LINE = colors.HexColor("#CBD5E1")

DocumentKind = Literal[
    "BOOKING_CONFIRMATION",
    "COMMERCIAL_INVOICE",
    "BILL_OF_LADING",
]


@dataclass(frozen=True)
class TradeFixture:
    reference: str
    finance_transaction_id: str
    company_id: str
    buyer: str
    amount: str
    payment_terms: str
    planned_departure_date: str
    actual_anchor_date: str
    booking_no: str
    bl_no: str
    invoice_no: str
    seller: str
    vessel: str
    voyage: str
    port_of_loading: str
    port_of_discharge: str
    commodity: str
    booking_date: str
    issue_place_date: str
    on_board_date: str | None


FIXTURES = (
    TradeFixture(
        reference="TRD-001",
        finance_transaction_id="TXN001",
        company_id="A",
        buyer="GLOBAL AUTO PARTS LLC",
        amount="45,000.00",
        payment_terms="T/T 30 DAYS AFTER B/L DATE",
        planned_departure_date="20Aug26",
        actual_anchor_date="Aug 20, 2026",
        booking_no="BK-TRD-001",
        bl_no="BL-TRD-001",
        invoice_no="INV-TXN001",
        seller="HANBIT PRECISION CO., LTD.",
        vessel="KB HORIZON",
        voyage="101W",
        port_of_loading="BUSAN",
        port_of_discharge="LOS ANGELES",
        commodity="AUTOMOTIVE TRANSMISSION PARTS",
        booking_date="01Aug26",
        issue_place_date="AUG 21 2026 BUSAN",
        on_board_date="Aug 20, 2026",
    ),
    TradeFixture(
        reference="TRD-002",
        finance_transaction_id="TXN002",
        company_id="A",
        buyer="NORDIC MACHINERY AB",
        amount="80,000.00",
        payment_terms="USANCE L/C 60 DAYS AFTER B/L DATE",
        planned_departure_date="25Aug26",
        actual_anchor_date="Sep 03, 2026",
        booking_no="BK-TRD-002",
        bl_no="BL-TRD-002",
        invoice_no="INV-TXN002",
        seller="HANBIT PRECISION CO., LTD.",
        vessel="KB NORDIC",
        voyage="202E",
        port_of_loading="BUSAN",
        port_of_discharge="GOTHENBURG",
        commodity="PRECISION MACHINERY COMPONENTS",
        booking_date="04Aug26",
        issue_place_date="SEP 04 2026 BUSAN",
        on_board_date=None,
    ),
    TradeFixture(
        reference="TRD-003",
        finance_transaction_id="TXN003",
        company_id="A",
        buyer="SAKURA ELECTRONICS CO., LTD.",
        amount="60,000.00",
        payment_terms="T/T 45 DAYS AFTER B/L DATE",
        planned_departure_date="15Aug26",
        actual_anchor_date="Aug 24, 2026",
        booking_no="BK-TRD-003",
        bl_no="BL-TRD-003",
        invoice_no="INV-TXN003",
        seller="HANBIT PRECISION CO., LTD.",
        vessel="KB SAKURA",
        voyage="303E",
        port_of_loading="BUSAN",
        port_of_discharge="YOKOHAMA",
        commodity="ELECTRONIC CONTROL MODULES",
        booking_date="28Jul26",
        issue_place_date="AUG 25 2026 BUSAN",
        on_board_date=None,
    ),
)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _canvas(path: Path, title: str, reference: str) -> Canvas:
    canvas = Canvas(
        str(path),
        pagesize=A4,
        pageCompression=1,
        invariant=1,
    )
    canvas.setAuthor("KB TradeFlow Twin")
    canvas.setCreator("scripts/generate_live_trade_documents.py")
    canvas.setSubject(f"Synthetic live ingestion fixture {reference}")
    canvas.setTitle(f"{reference} - {title}")
    width, height = A4
    canvas.setFillColor(NAVY)
    canvas.rect(0, height - 92, width, 92, fill=1, stroke=0)
    canvas.setFillColor(colors.white)
    canvas.setFont("Helvetica-Bold", 18)
    canvas.drawString(42, height - 48, title)
    canvas.setFont("Helvetica", 9)
    canvas.drawString(42, height - 68, "SYNTHETIC TRADE DOCUMENT - LIVE PIPELINE FIXTURE")
    canvas.setFillColor(TEAL)
    canvas.roundRect(width - 132, height - 67, 90, 28, 7, fill=1, stroke=0)
    canvas.setFillColor(colors.white)
    canvas.setFont("Helvetica-Bold", 11)
    label_width = stringWidth(reference, "Helvetica-Bold", 11)
    canvas.drawString(width - 87 - label_width / 2, height - 56, reference)
    return canvas


def _section(canvas: Canvas, y: float, title: str) -> float:
    canvas.setFillColor(PALE)
    canvas.roundRect(42, y - 20, A4[0] - 84, 24, 4, fill=1, stroke=0)
    canvas.setFillColor(TEAL)
    canvas.setFont("Helvetica-Bold", 9)
    canvas.drawString(51, y - 12, title.upper())
    return y - 34


def _line(canvas: Canvas, y: float, text: str, *, bold: bool = False) -> float:
    canvas.setFillColor(INK)
    canvas.setFont("Helvetica-Bold" if bold else "Helvetica", 9.5)
    canvas.drawString(51, y, text)
    canvas.setStrokeColor(LINE)
    canvas.setLineWidth(0.35)
    canvas.line(51, y - 5, A4[0] - 51, y - 5)
    return y - 22


def _footer(canvas: Canvas) -> None:
    width, _ = A4
    canvas.setFillColor(MUTED)
    canvas.setFont("Helvetica", 7.5)
    canvas.drawString(42, 28, "Synthetic data only. Not a carrier or banking instrument.")
    canvas.drawRightString(width - 42, 28, "Page 1 of 1")
    canvas.save()


def _references(canvas: Canvas, y: float, fixture: TradeFixture) -> float:
    y = _section(canvas, y, "Source references")
    y = _line(canvas, y, f"Transaction Reference : {fixture.reference}", bold=True)
    y = _line(
        canvas,
        y,
        f"Finance Transaction ID : {fixture.finance_transaction_id}",
    )
    return _line(canvas, y, f"Company ID : {fixture.company_id}")


def _booking(path: Path, fixture: TradeFixture) -> None:
    canvas = _canvas(path, "BOOKING RECEIPT NOTICE", fixture.reference)
    y = _references(canvas, A4[1] - 116, fixture)
    y = _section(canvas, y, "Booking and parties")
    y = _line(canvas, y, f"Booking No : {fixture.booking_no}", bold=True)
    y = _line(canvas, y, f"Booking Ref. No. : REF-{fixture.reference}")
    y = _line(canvas, y, f"Booking Date : {fixture.booking_date}")
    y = _line(canvas, y, f"Shipper : {fixture.seller}")
    y = _line(canvas, y, f"B/L No. : {fixture.bl_no}")
    y = _section(canvas, y, "Voyage and cargo")
    y = _line(canvas, y, f"Trunk Vessel : {fixture.vessel} {fixture.voyage}(KBT)")
    y = _line(
        canvas,
        y,
        f"Port of Loading : {fixture.port_of_loading}  Terminal : KB DEMO TERMINAL",
    )
    y = _line(
        canvas,
        y,
        f"Port of Discharging : {fixture.port_of_discharge}  Terminal : DEMO TERMINAL",
    )
    y = _line(canvas, y, f"Proforma 1st vessel ETD : {fixture.planned_departure_date}")
    y = _line(canvas, y, f"Commodity : {fixture.commodity}  Estimated Weight : 18,000 KGS")
    y = _line(canvas, y, "Equipment Type/Q'ty : 40'DRY HC.-1")
    y = _line(canvas, y, "Remarks 1 : SYNTHETIC LIVE INGESTION FIXTURE")
    _line(canvas, y, "Remarks 2 : Schedule subject to carrier confirmation.")
    _footer(canvas)


def _invoice(path: Path, fixture: TradeFixture) -> None:
    canvas = _canvas(path, "COMMERCIAL INVOICE", fixture.reference)
    y = _references(canvas, A4[1] - 116, fixture)
    y = _section(canvas, y, "Parties and invoice")
    y = _line(canvas, y, "Shipper/Seller", bold=True)
    y = _line(canvas, y, fixture.seller)
    y = _line(canvas, y, "Invoice No. and date", bold=True)
    y = _line(canvas, y, f"{fixture.invoice_no} AUG. 01. 2026")
    y = _line(canvas, y, "Buyer(if other than consignee)", bold=True)
    y = _line(canvas, y, fixture.buyer)
    y = _section(canvas, y, "Terms and goods")
    y = _line(canvas, y, "Terms of delivery and payment", bold=True)
    y = _line(canvas, y, "F.O.B BUSAN")
    y = _line(canvas, y, fixture.payment_terms)
    y = _line(canvas, y, "Shipping Marks", bold=True)
    y = _line(canvas, y, f"{fixture.reference}/{fixture.finance_transaction_id}")
    y = _line(canvas, y, "Goods description", bold=True)
    y = _line(canvas, y, fixture.commodity)
    y = _line(canvas, y, "Quantity 1,000 PCS")
    y = _line(canvas, y, f"Amount US${fixture.amount}", bold=True)
    y = _line(canvas, y, "Signed by")
    _line(canvas, y, fixture.seller)
    _footer(canvas)


def _bill_of_lading(path: Path, fixture: TradeFixture) -> None:
    canvas = _canvas(path, "BILL OF LADING", fixture.reference)
    y = _references(canvas, A4[1] - 116, fixture)
    y = _section(canvas, y, "Bill and parties")
    y = _line(canvas, y, f"B/L No. : {fixture.bl_no}", bold=True)
    y = _line(canvas, y, "Shipper/Exporter", bold=True)
    y = _line(canvas, y, fixture.seller)
    y = _line(canvas, y, "Consignee", bold=True)
    y = _line(canvas, y, fixture.buyer)
    y = _section(canvas, y, "Voyage")
    y = _line(canvas, y, "Ocean Vessel  Voyage No.", bold=True)
    y = _line(canvas, y, f"{fixture.vessel}  {fixture.voyage}")
    y = _line(
        canvas,
        y,
        "Port of Loading  Port of Discharge  Place of Delivery",
        bold=True,
    )
    y = _line(
        canvas,
        y,
        (f"{fixture.port_of_loading}  {fixture.port_of_discharge}  {fixture.port_of_discharge}"),
    )
    y = _line(canvas, y, f"Description of Goods : {fixture.commodity}")
    y = _section(canvas, y, "Issue and shipment evidence")
    y = _line(canvas, y, "Place and Date of Issue", bold=True)
    y = _line(canvas, y, fixture.issue_place_date)
    y = _line(canvas, y, "Laden on board vessel", bold=True)
    y = _line(canvas, y, "Date", bold=True)
    if fixture.on_board_date is not None:
        _line(canvas, y, fixture.on_board_date)
    else:
        canvas.setStrokeColor(TEAL)
        canvas.setLineWidth(1)
        canvas.line(51, y - 2, 250, y - 2)
        canvas.setFillColor(MUTED)
        canvas.setFont("Helvetica-Oblique", 8)
        canvas.drawString(260, y - 5, "Blank in source document")
    _footer(canvas)


def _document_entry(
    path: Path,
    fixture: TradeFixture,
    doc_type: DocumentKind,
    expected_missing_core_fields: list[str],
) -> dict[str, Any]:
    reader = PdfReader(path)
    text = "\n".join(page.extract_text() or "" for page in reader.pages)
    if len(reader.pages) != 1 or not text.strip():
        raise RuntimeError(f"Unreadable generated PDF: {path.name}")
    if fixture.reference not in text or fixture.finance_transaction_id not in text:
        raise RuntimeError(f"Source references missing from generated PDF: {path.name}")
    return {
        "file_name": path.name,
        "doc_type": doc_type,
        "transaction_reference": fixture.reference,
        "finance_transaction_id": fixture.finance_transaction_id,
        "company_id": fixture.company_id,
        "bytes": path.stat().st_size,
        "pages": len(reader.pages),
        "sha256": _sha256(path),
        "expected_missing_core_fields": expected_missing_core_fields,
    }


def generate() -> dict[str, Any]:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    generated: list[dict[str, Any]] = []
    by_ref = {fixture.reference: fixture for fixture in FIXTURES}
    plan: tuple[
        tuple[str, DocumentKind, str, Any, list[str]],
        ...,
    ] = (
        ("01_TRD-001_booking.pdf", "BOOKING_CONFIRMATION", "TRD-001", _booking, []),
        ("02_TRD-001_invoice.pdf", "COMMERCIAL_INVOICE", "TRD-001", _invoice, []),
        ("03_TRD-001_bill_of_lading.pdf", "BILL_OF_LADING", "TRD-001", _bill_of_lading, []),
        ("04_TRD-002_booking.pdf", "BOOKING_CONFIRMATION", "TRD-002", _booking, []),
        ("05_TRD-002_invoice.pdf", "COMMERCIAL_INVOICE", "TRD-002", _invoice, []),
        (
            "06_TRD-002_bill_of_lading_missing_on_board_date.pdf",
            "BILL_OF_LADING",
            "TRD-002",
            _bill_of_lading,
            ["on_board_date"],
        ),
        ("07_TRD-003_booking.pdf", "BOOKING_CONFIRMATION", "TRD-003", _booking, []),
        ("08_TRD-003_invoice.pdf", "COMMERCIAL_INVOICE", "TRD-003", _invoice, []),
    )
    for file_name, doc_type, reference, writer, missing in plan:
        fixture = by_ref[reference]
        path = OUTPUT_DIR / file_name
        writer(path, fixture)
        generated.append(_document_entry(path, fixture, doc_type, missing))

    manifest: dict[str, Any] = {
        "manifest_version": "live-8pdf-v1",
        "synthetic": True,
        "generator": "scripts/generate_live_trade_documents.py",
        "document_count": len(generated),
        "documents": generated,
        "transactions": [
            {
                "transaction_reference": fixture.reference,
                "finance_transaction_id": fixture.finance_transaction_id,
                "company_id": fixture.company_id,
                "expected_documents": (
                    [
                        "BOOKING_CONFIRMATION",
                        "COMMERCIAL_INVOICE",
                        "BILL_OF_LADING",
                    ]
                    if fixture.reference != "TRD-003"
                    else ["BOOKING_CONFIRMATION", "COMMERCIAL_INVOICE"]
                ),
                "expected_missing_documents": (
                    [] if fixture.reference != "TRD-003" else ["BILL_OF_LADING"]
                ),
                "expected_missing_core_fields": (
                    ["BILL_OF_LADING.on_board_date"] if fixture.reference == "TRD-002" else []
                ),
                "manual_override_value": (
                    fixture.actual_anchor_date if fixture.reference == "TRD-002" else None
                ),
            }
            for fixture in FIXTURES
        ],
    }
    MANIFEST_PATH.write_text(
        json.dumps(manifest, ensure_ascii=True, indent=2) + "\n",
        encoding="utf-8",
    )
    return manifest


def main() -> None:
    manifest = generate()
    print(MANIFEST_PATH)
    print(f"generated={manifest['document_count']}")


if __name__ == "__main__":
    main()

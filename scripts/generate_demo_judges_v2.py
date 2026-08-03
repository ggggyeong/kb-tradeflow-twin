from __future__ import annotations

import hashlib
import json
import sys
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

from pypdf import PdfReader
from reportlab.lib import colors  # type: ignore[import-untyped]
from reportlab.lib.pagesizes import A4  # type: ignore[import-untyped]
from reportlab.pdfbase.pdfmetrics import stringWidth  # type: ignore[import-untyped]
from reportlab.pdfgen.canvas import Canvas  # type: ignore[import-untyped]

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.domain_inputs.providers import field_registry  # noqa: E402
from app.schemas.field_contract import DocumentType  # noqa: E402
from app.services.ingestion.fixed_extractors import extract_fixed_document  # noqa: E402

OUTPUT_DIR = PROJECT_ROOT / "data" / "demo_judges_v2"
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
class DemoTradeFixture:
    reference: str
    finance_transaction_id: str
    company_id: str
    buyer: str
    amount: str
    payment_terms: str
    planned_departure_date: str
    planned_departure_iso: str
    actual_anchor_date: str
    actual_anchor_iso: str
    booking_no: str
    bl_no: str
    invoice_no: str
    invoice_date: str
    seller: str
    vessel: str
    voyage: str
    port_of_loading: str
    port_of_discharge: str
    commodity: str
    booking_date: str
    issue_place_date: str
    on_board_date: str | None


DEMO_FIXTURES = (
    DemoTradeFixture(
        reference="TRD-DEMO-001",
        finance_transaction_id="TXN-DEMO-001",
        company_id="DEMO-A",
        buyer="GLOBAL AUTO PARTS LLC",
        amount="45,000.00",
        payment_terms="T/T 30 DAYS AFTER B/L DATE",
        planned_departure_date="15Jun26",
        planned_departure_iso="2026-06-15",
        actual_anchor_date="Jun 25, 2026",
        actual_anchor_iso="2026-06-25",
        booking_no="BK-DEMO-001",
        bl_no="BL-DEMO-001",
        invoice_no="INV-DEMO-001",
        invoice_date="JUN. 01. 2026",
        seller="HANBIT PRECISION CO., LTD.",
        vessel="KB HORIZON",
        voyage="101W",
        port_of_loading="BUSAN",
        port_of_discharge="LOS ANGELES",
        commodity="AUTOMOTIVE TRANSMISSION PARTS",
        booking_date="01Jun26",
        issue_place_date="JUN 26 2026 BUSAN",
        on_board_date="Jun 25, 2026",
    ),
    DemoTradeFixture(
        reference="TRD-DEMO-002",
        finance_transaction_id="TXN-DEMO-002",
        company_id="DEMO-A",
        buyer="NORDIC MACHINERY AB",
        amount="80,000.00",
        payment_terms="T/T 60 DAYS AFTER B/L DATE",
        planned_departure_date="20Jun26",
        planned_departure_iso="2026-06-20",
        actual_anchor_date="Jun 28, 2026",
        actual_anchor_iso="2026-06-28",
        booking_no="BK-DEMO-002",
        bl_no="BL-DEMO-002",
        invoice_no="INV-DEMO-002",
        invoice_date="JUN. 05. 2026",
        seller="HANBIT PRECISION CO., LTD.",
        vessel="KB NORDIC",
        voyage="202E",
        port_of_loading="BUSAN",
        port_of_discharge="GOTHENBURG",
        commodity="PRECISION MACHINERY COMPONENTS",
        booking_date="05Jun26",
        issue_place_date="JUN 29 2026 BUSAN",
        on_board_date=None,
    ),
    DemoTradeFixture(
        reference="TRD-DEMO-003",
        finance_transaction_id="TXN-DEMO-003",
        company_id="DEMO-A",
        buyer="SAKURA ELECTRONICS CO., LTD.",
        amount="60,000.00",
        payment_terms="T/T 45 DAYS AFTER B/L DATE",
        planned_departure_date="30Jun26",
        planned_departure_iso="2026-06-30",
        actual_anchor_date="Jul 09, 2026",
        actual_anchor_iso="2026-07-09",
        booking_no="BK-DEMO-003",
        bl_no="BL-DEMO-003",
        invoice_no="INV-DEMO-003",
        invoice_date="JUN. 10. 2026",
        seller="HANBIT PRECISION CO., LTD.",
        vessel="KB SAKURA",
        voyage="303E",
        port_of_loading="BUSAN",
        port_of_discharge="YOKOHAMA",
        commodity="ELECTRONIC CONTROL MODULES",
        booking_date="10Jun26",
        issue_place_date="JUL 10 2026 BUSAN",
        on_board_date=None,
    ),
)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _canvas(path: Path, title: str, fixture: DemoTradeFixture) -> Canvas:
    canvas = Canvas(
        str(path),
        pagesize=A4,
        pageCompression=1,
        invariant=1,
    )
    canvas.setAuthor("KB TradeFlow Twin")
    canvas.setCreator("scripts/generate_demo_judges_v2.py")
    canvas.setSubject(f"Synthetic judge demo v2 fixture {fixture.reference}")
    canvas.setTitle(f"{fixture.reference} - {title}")
    width, height = A4
    canvas.setFillColor(NAVY)
    canvas.rect(0, height - 92, width, 92, fill=1, stroke=0)
    canvas.setFillColor(colors.white)
    canvas.setFont("Helvetica-Bold", 18)
    canvas.drawString(42, height - 48, title)
    canvas.setFont("Helvetica", 9)
    canvas.drawString(42, height - 68, "SYNTHETIC JUDGE DEMO V2 - FIXED TEMPLATE")

    badge_width = max(
        128,
        stringWidth(fixture.reference, "Helvetica-Bold", 10.5) + 28,
    )
    badge_x = width - 42 - badge_width
    canvas.setFillColor(TEAL)
    canvas.roundRect(badge_x, height - 67, badge_width, 28, 7, fill=1, stroke=0)
    canvas.setFillColor(colors.white)
    canvas.setFont("Helvetica-Bold", 10.5)
    label_width = stringWidth(fixture.reference, "Helvetica-Bold", 10.5)
    canvas.drawString(badge_x + (badge_width - label_width) / 2, height - 56, fixture.reference)
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
    canvas.drawRightString(width - 42, 28, "Judge Demo V2 | Page 1 of 1")
    canvas.save()


def _references(canvas: Canvas, y: float, fixture: DemoTradeFixture) -> float:
    y = _section(canvas, y, "Source references")
    y = _line(canvas, y, f"Transaction Reference : {fixture.reference}", bold=True)
    y = _line(canvas, y, f"Finance Transaction ID : {fixture.finance_transaction_id}")
    return _line(canvas, y, f"Company ID : {fixture.company_id}")


def _booking(path: Path, fixture: DemoTradeFixture) -> None:
    canvas = _canvas(path, "BOOKING RECEIPT NOTICE", fixture)
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
    y = _line(canvas, y, "Remarks 1 : SYNTHETIC JUDGE DEMO V2")
    _line(canvas, y, "Remarks 2 : Schedule subject to carrier confirmation.")
    _footer(canvas)


def _invoice(path: Path, fixture: DemoTradeFixture) -> None:
    canvas = _canvas(path, "COMMERCIAL INVOICE", fixture)
    y = _references(canvas, A4[1] - 116, fixture)
    y = _section(canvas, y, "Parties and invoice")
    y = _line(canvas, y, "Shipper/Seller", bold=True)
    y = _line(canvas, y, fixture.seller)
    y = _line(canvas, y, "Invoice No. and date", bold=True)
    y = _line(canvas, y, f"{fixture.invoice_no} {fixture.invoice_date}")
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


def _bill_of_lading(path: Path, fixture: DemoTradeFixture) -> None:
    canvas = _canvas(path, "BILL OF LADING", fixture)
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
    y = _line(canvas, y, "Port of Loading  Port of Discharge  Place of Delivery", bold=True)
    y = _line(
        canvas,
        y,
        f"{fixture.port_of_loading}  {fixture.port_of_discharge}  {fixture.port_of_discharge}",
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
    fixture: DemoTradeFixture,
    doc_type: DocumentKind,
    expected_missing_core_fields: list[str],
) -> dict[str, Any]:
    reader = PdfReader(path)
    text = "\n".join(page.extract_text() or "" for page in reader.pages)
    if len(reader.pages) != 1 or not text.strip():
        raise RuntimeError(f"Unreadable generated PDF: {path.name}")

    extraction = extract_fixed_document(path)
    expected_type = DocumentType(doc_type)
    expected_references = {
        "transaction_reference": fixture.reference,
        "finance_transaction_id": fixture.finance_transaction_id,
        "company_id": fixture.company_id,
    }
    if extraction.classification.status != "AUTO_CONFIRMED":
        raise RuntimeError(f"Generated PDF did not auto-classify: {path.name}")
    if extraction.classification.doc_type is not expected_type:
        raise RuntimeError(f"Generated PDF classified as the wrong type: {path.name}")
    if extraction.source_references != expected_references:
        raise RuntimeError(f"Generated PDF source references differ: {path.name}")
    if extraction.missing_core_fields != expected_missing_core_fields:
        raise RuntimeError(
            f"Generated PDF missing fields differ: {path.name}: {extraction.missing_core_fields}"
        )

    registry = field_registry()
    core_keys = registry.core_keys(expected_type)
    extracted_core_fields = sorted(core_keys & set(extraction.fields))
    if set(extracted_core_fields) != core_keys - set(expected_missing_core_fields):
        raise RuntimeError(f"Generated PDF core coverage differs: {path.name}")

    return {
        "file_name": path.name,
        "doc_type": doc_type,
        **expected_references,
        "bytes": path.stat().st_size,
        "pages": len(reader.pages),
        "sha256": _sha256(path),
        "extracted_core_fields": extracted_core_fields,
        "expected_missing_core_fields": expected_missing_core_fields,
    }


def generate() -> dict[str, Any]:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    generated: list[dict[str, Any]] = []
    by_reference = {fixture.reference: fixture for fixture in DEMO_FIXTURES}
    plan: tuple[
        tuple[
            str,
            DocumentKind,
            str,
            Callable[[Path, DemoTradeFixture], None],
            list[str],
        ],
        ...,
    ] = (
        (
            "01_TRD-DEMO-001_booking.pdf",
            "BOOKING_CONFIRMATION",
            "TRD-DEMO-001",
            _booking,
            [],
        ),
        (
            "02_TRD-DEMO-001_invoice.pdf",
            "COMMERCIAL_INVOICE",
            "TRD-DEMO-001",
            _invoice,
            [],
        ),
        (
            "03_TRD-DEMO-001_bill_of_lading.pdf",
            "BILL_OF_LADING",
            "TRD-DEMO-001",
            _bill_of_lading,
            [],
        ),
        (
            "04_TRD-DEMO-002_booking.pdf",
            "BOOKING_CONFIRMATION",
            "TRD-DEMO-002",
            _booking,
            [],
        ),
        (
            "05_TRD-DEMO-002_invoice.pdf",
            "COMMERCIAL_INVOICE",
            "TRD-DEMO-002",
            _invoice,
            [],
        ),
        (
            "06_TRD-DEMO-002_bill_of_lading_missing_on_board_date.pdf",
            "BILL_OF_LADING",
            "TRD-DEMO-002",
            _bill_of_lading,
            ["on_board_date"],
        ),
        (
            "07_TRD-DEMO-003_booking.pdf",
            "BOOKING_CONFIRMATION",
            "TRD-DEMO-003",
            _booking,
            [],
        ),
        (
            "08_TRD-DEMO-003_invoice.pdf",
            "COMMERCIAL_INVOICE",
            "TRD-DEMO-003",
            _invoice,
            [],
        ),
    )

    for file_name, doc_type, reference, writer, missing in plan:
        fixture = by_reference[reference]
        path = OUTPUT_DIR / file_name
        writer(path, fixture)
        generated.append(_document_entry(path, fixture, doc_type, missing))

    manifest: dict[str, Any] = {
        "manifest_version": "demo-judges-v2",
        "dataset_id": "demo_judges_v2",
        "batch_id": "DEMO-DOCS-20260710",
        "synthetic": True,
        "generator": "scripts/generate_demo_judges_v2.py",
        "document_count": len(generated),
        "documents": generated,
        "transactions": [
            {
                "transaction_reference": fixture.reference,
                "finance_transaction_id": fixture.finance_transaction_id,
                "company_id": fixture.company_id,
                "planned_departure_date": fixture.planned_departure_iso,
                "reviewed_actual_anchor_date": fixture.actual_anchor_iso,
                "payment_terms": fixture.payment_terms,
                "expected_documents": (
                    [
                        "BOOKING_CONFIRMATION",
                        "COMMERCIAL_INVOICE",
                        "BILL_OF_LADING",
                    ]
                    if fixture.reference != "TRD-DEMO-003"
                    else ["BOOKING_CONFIRMATION", "COMMERCIAL_INVOICE"]
                ),
                "expected_missing_documents": (
                    [] if fixture.reference != "TRD-DEMO-003" else ["BILL_OF_LADING"]
                ),
                "expected_missing_core_fields": (
                    ["BILL_OF_LADING.on_board_date"] if fixture.reference == "TRD-DEMO-002" else []
                ),
                "manual_override_value": (
                    fixture.actual_anchor_date if fixture.reference == "TRD-DEMO-002" else None
                ),
                "manual_override_normalized": (
                    fixture.actual_anchor_iso if fixture.reference == "TRD-DEMO-002" else None
                ),
            }
            for fixture in DEMO_FIXTURES
        ],
    }
    MANIFEST_PATH.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return manifest


def main() -> None:
    manifest = generate()
    print(MANIFEST_PATH)
    print(f"generated={manifest['document_count']}")


if __name__ == "__main__":
    main()

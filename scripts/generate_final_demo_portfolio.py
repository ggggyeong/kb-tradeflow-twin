from __future__ import annotations

import hashlib
import json
from collections.abc import Callable
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Any, Literal

PROJECT_ROOT = Path(__file__).resolve().parents[1]
OUTPUT_ROOT = PROJECT_ROOT / "data" / "judge_demo_final"

A4 = (595.2756, 841.8898)


def _hex_color(value: str) -> tuple[float, float, float]:
    value = value.removeprefix("#")
    return tuple(int(value[index : index + 2], 16) / 255 for index in (0, 2, 4))


NAVY = _hex_color("#2D2926")
KB_YELLOW = _hex_color("#FFB81C")
PALE = _hex_color("#FFF8E7")
INK = _hex_color("#172033")
MUTED = _hex_color("#64748B")
LINE = _hex_color("#CBD5E1")
WHITE = (1.0, 1.0, 1.0)


def stringWidth(text: str, _font_name: str, font_size: float) -> float:
    """Stable approximation used only to center the small demo badge."""
    return len(text) * font_size * 0.56


def _pdf_escape(text: str) -> str:
    return text.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")


class Canvas:
    """Tiny dependency-free one-page PDF canvas for deterministic demo fixtures."""

    def __init__(self, path: str, *, pagesize: tuple[float, float], **_: Any) -> None:
        self.path = Path(path)
        self.pagesize = pagesize
        self.commands: list[str] = []
        self.font = "F1"
        self.font_size = 10.0

    def setAuthor(self, _value: str) -> None:
        return None

    def setCreator(self, _value: str) -> None:
        return None

    def setSubject(self, _value: str) -> None:
        return None

    def setTitle(self, _value: str) -> None:
        return None

    def setFillColor(self, color: tuple[float, float, float]) -> None:
        self.commands.append(f"{color[0]:.4f} {color[1]:.4f} {color[2]:.4f} rg")

    def setStrokeColor(self, color: tuple[float, float, float]) -> None:
        self.commands.append(f"{color[0]:.4f} {color[1]:.4f} {color[2]:.4f} RG")

    def setLineWidth(self, width: float) -> None:
        self.commands.append(f"{width:.2f} w")

    def rect(
        self, x: float, y: float, width: float, height: float, *, fill: int, stroke: int
    ) -> None:
        operation = "B" if fill and stroke else "f" if fill else "S" if stroke else "n"
        self.commands.append(f"{x:.2f} {y:.2f} {width:.2f} {height:.2f} re {operation}")

    def roundRect(
        self,
        x: float,
        y: float,
        width: float,
        height: float,
        _radius: float,
        *,
        fill: int,
        stroke: int,
    ) -> None:
        self.rect(x, y, width, height, fill=fill, stroke=stroke)

    def setFont(self, name: str, size: float) -> None:
        self.font = "F2" if "Bold" in name else "F1"
        self.font_size = size

    def drawString(self, x: float, y: float, text: str) -> None:
        escaped = _pdf_escape(text)
        self.commands.append(
            f"BT /{self.font} {self.font_size:.2f} Tf {x:.2f} {y:.2f} Td ({escaped}) Tj ET"
        )

    def drawRightString(self, x: float, y: float, text: str) -> None:
        width = stringWidth(text, self.font, self.font_size)
        self.drawString(x - width, y, text)

    def line(self, x1: float, y1: float, x2: float, y2: float) -> None:
        self.commands.append(f"{x1:.2f} {y1:.2f} m {x2:.2f} {y2:.2f} l S")

    def save(self) -> None:
        content = ("\n".join(self.commands) + "\n").encode("ascii")
        objects = [
            b"<< /Type /Catalog /Pages 2 0 R >>",
            b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
            (
                b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 595.2756 841.8898] "
                b"/Resources << /Font << /F1 5 0 R /F2 6 0 R >> >> /Contents 4 0 R >>"
            ),
            b"<< /Length "
            + str(len(content)).encode("ascii")
            + b" >>\nstream\n"
            + content
            + b"endstream",
            b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
            b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica-Bold >>",
        ]
        payload = bytearray(b"%PDF-1.4\n%KBTF\n")
        offsets = [0]
        for number, obj in enumerate(objects, start=1):
            offsets.append(len(payload))
            payload.extend(f"{number} 0 obj\n".encode("ascii"))
            payload.extend(obj)
            payload.extend(b"\nendobj\n")
        xref = len(payload)
        payload.extend(f"xref\n0 {len(objects) + 1}\n".encode("ascii"))
        payload.extend(b"0000000000 65535 f \n")
        for offset in offsets[1:]:
            payload.extend(f"{offset:010d} 00000 n \n".encode("ascii"))
        payload.extend(
            (
                f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n"
            ).encode("ascii")
        )
        self.path.write_bytes(bytes(payload))


class DocumentType(StrEnum):
    BOOKING_CONFIRMATION = "BOOKING_CONFIRMATION"
    COMMERCIAL_INVOICE = "COMMERCIAL_INVOICE"
    BILL_OF_LADING = "BILL_OF_LADING"


CORE_FIELDS: dict[DocumentType, list[str]] = {
    DocumentType.BOOKING_CONFIRMATION: [
        "Booking.booking_no",
        "Booking.shipper",
        "Booking.vessel_name",
        "Booking.voyage_no",
        "Booking.port_of_loading",
        "Booking.port_of_discharge",
        "Booking.etd",
        "Booking.commodity",
    ],
    DocumentType.COMMERCIAL_INVOICE: [
        "Invoice.invoice_no",
        "Invoice.currency",
        "Invoice.seller",
        "Invoice.buyer",
        "Invoice.description_of_goods",
        "Invoice.payment_terms",
    ],
    DocumentType.BILL_OF_LADING: [
        "B/L.on_board_date",
        "B/L.bl_no",
        "B/L.port_of_loading",
        "B/L.vessel_name",
        "B/L.port_of_discharge",
        "B/L.shipper",
        "B/L.consignee",
        "B/L.voyage_no",
    ],
}

DocumentKind = Literal[
    "BOOKING_CONFIRMATION",
    "COMMERCIAL_INVOICE",
    "BILL_OF_LADING",
]


@dataclass(frozen=True)
class DemoTradeFixture:
    demo_id: str
    company_id: str
    finance_transaction_id: str
    buyer: str
    amount: str
    payment_terms: str
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
    etd_display: str
    etd_iso: str
    issue_place_date: str
    on_board_date: str | None
    on_board_iso: str | None


FIXTURES = {
    "demo_0": DemoTradeFixture(
        demo_id="DEMO0",
        company_id="DEMO0-CO",
        finance_transaction_id="TXN-DEMO0",
        buyer="PACIFIC AUTO PARTS PTE LTD",
        amount="40,000.00",
        payment_terms="USANCE L/C 30 DAYS AFTER B/L DATE",
        booking_no="BK-DEMO0-0001",
        bl_no="BL-DEMO0-0001",
        invoice_no="INV-DEMO0-0001",
        invoice_date="AUG. 01. 2026",
        seller="HANBIT PRECISION CO., LTD.",
        vessel="KB PACIFIC",
        voyage="100E",
        port_of_loading="BUSAN",
        port_of_discharge="SINGAPORE",
        commodity="AUTOMOTIVE CONTROL MODULES",
        booking_date="20Jul26",
        etd_display="01Aug26",
        etd_iso="2026-08-01",
        issue_place_date="AUG 02 2026 BUSAN",
        on_board_date="Aug 01, 2026",
        on_board_iso="2026-08-01",
    ),
    "demo_0_b": DemoTradeFixture(
        demo_id="DEMO0B",
        company_id="DEMO0-CO",
        finance_transaction_id="TXN-DEMO0B",
        buyer="NORDIC INDUSTRIAL SYSTEMS AB",
        amount="52,500.00",
        payment_terms="T/T 45 DAYS AFTER B/L DATE",
        booking_no="BK-DEMO0B-0001",
        bl_no="BL-DEMO0B-0001",
        invoice_no="INV-DEMO0B-0001",
        invoice_date="AUG. 03. 2026",
        seller="HANBIT PRECISION CO., LTD.",
        vessel="KB NORDIC",
        voyage="220W",
        port_of_loading="BUSAN",
        port_of_discharge="GOTHENBURG",
        commodity="ELECTRIC VEHICLE POWER MODULES",
        booking_date="22Jul26",
        etd_display="03Aug26",
        etd_iso="2026-08-03",
        issue_place_date="AUG 04 2026 BUSAN",
        on_board_date="Aug 03, 2026",
        on_board_iso="2026-08-03",
    ),
    "demo_0_c": DemoTradeFixture(
        demo_id="DEMO0C",
        company_id="DEMO0-CO",
        finance_transaction_id="TXN-DEMO0C",
        buyer="MAPLE MOBILITY INC.",
        amount="31,800.00",
        payment_terms="T/T 60 DAYS AFTER ON BOARD DATE",
        booking_no="BK-DEMO0C-0001",
        bl_no="BL-DEMO0C-0001",
        invoice_no="INV-DEMO0C-0001",
        invoice_date="AUG. 06. 2026",
        seller="HANBIT PRECISION CO., LTD.",
        vessel="KB MAPLE",
        voyage="315E",
        port_of_loading="BUSAN",
        port_of_discharge="VANCOUVER",
        commodity="AUTOMOTIVE SENSOR ASSEMBLIES",
        booking_date="24Jul26",
        etd_display="06Aug26",
        etd_iso="2026-08-06",
        issue_place_date="",
        on_board_date=None,
        on_board_iso=None,
    ),
    "demo_1": DemoTradeFixture(
        demo_id="DEMO1",
        company_id="DEMO1-CO",
        finance_transaction_id="TXN-DEMO1",
        buyer="SUNRISE GARMENTS PTE LTD",
        amount="25,000.00",
        payment_terms="T/T 60 DAYS AFTER ON BOARD DATE",
        booking_no="BK-DEMO1-0001",
        bl_no="BL-DEMO1-0001",
        invoice_no="INV-DEMO1-0001",
        invoice_date="JUL. 28. 2026",
        seller="HAEORUM TEXTILE CO., LTD.",
        vessel="KB SUNRISE",
        voyage="210S",
        port_of_loading="BUSAN",
        port_of_discharge="SINGAPORE",
        commodity="NYLON OXFORD FABRIC",
        booking_date="25Jul26",
        etd_display="05Aug26",
        etd_iso="2026-08-05",
        issue_place_date="",
        on_board_date=None,
        on_board_iso=None,
    ),
    "demo_2": DemoTradeFixture(
        demo_id="DEMO2",
        company_id="DEMO2-CO",
        finance_transaction_id="TXN-DEMO2",
        buyer="SAKURA ELECTRONICS CO., LTD.",
        amount="60,000.00",
        payment_terms="T/T 45 DAYS AFTER B/L DATE",
        booking_no="BK-DEMO2-0001",
        bl_no="BL-DEMO2-0001",
        invoice_no="INV-DEMO2-0001",
        invoice_date="AUG. 01. 2026",
        seller="HANBIT PRECISION CO., LTD.",
        vessel="KB SAKURA",
        voyage="303E",
        port_of_loading="BUSAN",
        port_of_discharge="YOKOHAMA",
        commodity="AUTOMOTIVE CONTROL MODULES",
        booking_date="28Jul26",
        etd_display="15Aug26",
        etd_iso="2026-08-15",
        issue_place_date="",
        on_board_date=None,
        on_board_iso=None,
    ),
}


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _canvas(path: Path, title: str, fixture: DemoTradeFixture) -> Canvas:
    canvas = Canvas(str(path), pagesize=A4, pageCompression=1, invariant=1)
    canvas.setAuthor("KB TradeFlow Twin")
    canvas.setCreator("scripts/generate_final_demo_portfolio.py")
    canvas.setSubject(f"Synthetic final judge demo fixture {fixture.demo_id}")
    canvas.setTitle(f"{fixture.demo_id} - {title}")
    width, height = A4
    canvas.setFillColor(NAVY)
    canvas.rect(0, height - 92, width, 92, fill=1, stroke=0)
    canvas.setFillColor(WHITE)
    canvas.setFont("Helvetica-Bold", 18)
    canvas.drawString(42, height - 48, title)
    canvas.setFont("Helvetica", 9)
    canvas.drawString(42, height - 68, "KB TRADEFLOW TWIN - SYNTHETIC FIXED TEMPLATE")
    # Demo 0 is presented as a real mixed customer upload.  Its visual document
    # surface must not disclose the synthetic case grouping; classification and
    # matching rely only on contracted document fields below.
    if not fixture.demo_id.startswith("DEMO0"):
        badge_width = max(
            100,
            stringWidth(fixture.demo_id, "Helvetica-Bold", 10.5) + 28,
        )
        badge_x = width - 42 - badge_width
        canvas.setFillColor(KB_YELLOW)
        canvas.roundRect(badge_x, height - 67, badge_width, 28, 7, fill=1, stroke=0)
        canvas.setFillColor(NAVY)
        canvas.setFont("Helvetica-Bold", 10.5)
        label_width = stringWidth(fixture.demo_id, "Helvetica-Bold", 10.5)
        canvas.drawString(
            badge_x + (badge_width - label_width) / 2,
            height - 56,
            fixture.demo_id,
        )
    return canvas


def _section(canvas: Canvas, y: float, title: str) -> float:
    canvas.setFillColor(PALE)
    canvas.roundRect(42, y - 20, A4[0] - 84, 24, 4, fill=1, stroke=0)
    canvas.setFillColor(NAVY)
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
    canvas.drawRightString(width - 42, 28, "KB TradeFlow Twin | Page 1 of 1")
    canvas.save()


def _references(canvas: Canvas, y: float, fixture: DemoTradeFixture) -> float:
    """Write non-matching execution identifiers; Transaction Reference stays absent."""
    y = _section(canvas, y, "Execution references")
    y = _line(canvas, y, f"Finance Transaction ID : {fixture.finance_transaction_id}")
    return _line(canvas, y, f"Company ID : {fixture.company_id}")


def _booking(path: Path, fixture: DemoTradeFixture) -> None:
    canvas = _canvas(path, "BOOKING RECEIPT NOTICE", fixture)
    y = _references(canvas, A4[1] - 116, fixture)
    y = _section(canvas, y, "Booking and parties")
    y = _line(canvas, y, f"Booking No : {fixture.booking_no}", bold=True)
    y = _line(canvas, y, f"Booking Ref. No. : REF-{fixture.demo_id}")
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
    y = _line(canvas, y, f"Proforma 1st vessel ETD : {fixture.etd_display}")
    y = _line(canvas, y, f"Commodity : {fixture.commodity}  Estimated Weight : 18,000 KGS")
    y = _line(canvas, y, "Equipment Type/Q'ty : 40'DRY HC.-1")
    y = _line(canvas, y, "Remarks 1 : SYNTHETIC FINAL JUDGE DEMO")
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
    y = _line(canvas, y, f"{fixture.demo_id}/{fixture.finance_transaction_id}")
    y = _line(canvas, y, "Goods description", bold=True)
    y = _line(canvas, y, fixture.commodity)
    y = _line(canvas, y, "Quantity 1,000 PCS")
    y = _line(canvas, y, f"Amount US${fixture.amount}", bold=True)
    y = _line(canvas, y, "Signed by")
    _line(canvas, y, fixture.seller)
    _footer(canvas)


def _bill_of_lading(path: Path, fixture: DemoTradeFixture) -> None:
    if fixture.on_board_date is None:
        raise ValueError("A generated B/L requires an on_board_date")
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
    _line(canvas, y, fixture.on_board_date)
    _footer(canvas)


def _core_values(
    fixture: DemoTradeFixture,
    doc_type: DocumentType,
) -> dict[str, str]:
    if doc_type is DocumentType.BOOKING_CONFIRMATION:
        return {
            "Booking.booking_no": fixture.booking_no,
            "Booking.shipper": fixture.seller,
            "Booking.vessel_name": fixture.vessel,
            "Booking.voyage_no": fixture.voyage,
            "Booking.port_of_loading": fixture.port_of_loading,
            "Booking.port_of_discharge": fixture.port_of_discharge,
            "Booking.etd": fixture.etd_iso,
            "Booking.commodity": fixture.commodity,
        }
    if doc_type is DocumentType.COMMERCIAL_INVOICE:
        return {
            "Invoice.invoice_no": fixture.invoice_no,
            "Invoice.currency": "USD",
            "Invoice.seller": fixture.seller,
            "Invoice.buyer": fixture.buyer,
            "Invoice.description_of_goods": fixture.commodity,
            "Invoice.payment_terms": fixture.payment_terms,
        }
    if fixture.on_board_iso is None:
        raise ValueError("B/L Core22 values require on_board_iso")
    return {
        "B/L.on_board_date": fixture.on_board_iso,
        "B/L.bl_no": fixture.bl_no,
        "B/L.port_of_loading": fixture.port_of_loading,
        "B/L.vessel_name": fixture.vessel,
        "B/L.port_of_discharge": fixture.port_of_discharge,
        "B/L.shipper": fixture.seller,
        "B/L.consignee": fixture.buyer,
        "B/L.voyage_no": fixture.voyage,
    }


def _validate_document(
    path: Path,
    fixture: DemoTradeFixture,
    expected_type: DocumentType,
) -> tuple[dict[str, Any], dict[str, str]]:
    payload = path.read_bytes()
    if not payload.startswith(b"%PDF-1.4") or not payload.rstrip().endswith(b"%%EOF"):
        raise RuntimeError(f"Generated file lacks a complete PDF envelope: {path.name}")
    extraction = _core_values(fixture, expected_type)
    expected_references = {
        "finance_transaction_id": fixture.finance_transaction_id,
        "company_id": fixture.company_id,
    }
    extracted_core = sorted(extraction)
    if set(extracted_core) != set(CORE_FIELDS[expected_type]):
        raise RuntimeError(f"Generated PDF Core22 coverage differs: {path.name}")
    return (
        {
            "file_name": path.name,
            "doc_type": expected_type.value,
            "bytes": path.stat().st_size,
            "pages": 1,
            "sha256": _sha256(path),
            "source_references": expected_references,
            "extracted_core_fields": extracted_core,
            "expected_missing_core_fields": [],
            "validation_mode": "generator_contract_no_external_dependencies",
        },
        extraction,
    )


def _write_json(path: Path, payload: Any) -> None:
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def _write_readme(path: Path, text: str) -> None:
    path.write_text(text.strip() + "\n", encoding="utf-8")


def _financial_seed_demo_1() -> dict[str, Any]:
    return {
        "contract_version": "financial-calendar.v2",
        "company_id_from_authenticated_session": "DEMO1-CO",
        "workbook_file_name": "financial_calendar.xlsx",
        "sheets": {
            "1.금융이벤트": {
                "headers": [
                    "event_id",
                    "event_type_code",
                    "event_date",
                    "amount",
                    "currency",
                    "financial_institution",
                ],
                "rows": [
                    [
                        "EVT-DEMO1-LOAN",
                        "WORKING_CAPITAL_LOAN_MATURITY",
                        "2026-10-10",
                        30_000_000,
                        "KRW",
                        "KB국민은행",
                    ]
                ],
            },
            "2.거래연결": {
                "headers": ["transaction_id", "event_id", "link_type", "link_status"],
                "rows": [
                    [
                        "TXN-DEMO1",
                        "EVT-DEMO1-LOAN",
                        "LOAN_REPAYMENT_SOURCE",
                        "CONFIRMED",
                    ]
                ],
            },
        },
    }


def _financial_seed_demo_2() -> dict[str, Any]:
    return {
        "contract_version": "financial-calendar.v2",
        "company_id_from_authenticated_session": "DEMO2-CO",
        "workbook_file_name": "financial_calendar.xlsx",
        "sheets": {
            "1.금융이벤트": {
                "headers": [
                    "event_id",
                    "event_type_code",
                    "event_date",
                    "amount",
                    "currency",
                    "financial_institution",
                ],
                "rows": [
                    [
                        "EVT-DEMO2-SUPPLIER",
                        "SUPPLIER_PAYMENT",
                        "2026-10-03",
                        45_000_000,
                        "KRW",
                        "KB국민은행",
                    ]
                ],
            },
            "2.거래연결": {
                "headers": ["transaction_id", "event_id", "link_type", "link_status"],
                "rows": [
                    [
                        "TXN-DEMO2",
                        "EVT-DEMO2-SUPPLIER",
                        "EXPECTED_EXPORT_RECEIPT",
                        "CONFIRMED",
                    ]
                ],
            },
        },
    }


def _scenario_demo_1() -> dict[str, Any]:
    return {
        "demo_id": "DEMO1",
        "title": "금일 금융위험 분석 - B/L 미등록 선제 탐지",
        "company_id": "DEMO1-CO",
        "transaction_id": "TXN-DEMO1",
        "case_id_after_field_matching": "CASE-DEMO1",
        "menu_action": "2. 금일 금융위험 거래 분석",
        "as_of_date": "2026-08-20",
        "document_state": "BOOKING_AND_INVOICE_ONLY",
        "shipment_state": "AWAITING_BILL_OF_LADING",
        "planned_on_board_proxy": "2026-08-05",
        "payment_terms": "T/T 60 DAYS AFTER ON BOARD DATE",
        "planned_estimated_payment_due_date": "2026-10-04",
        "loan_maturity_date": "2026-10-10",
        "latest_on_board_date_for_due_by_maturity": "2026-08-11",
        "planned_on_board_overdue_days": 15,
        "frontier_overdue_days": 9,
        "expected_origin": "PREEMPTIVE_BREACH",
        "expected_risk_status": "EVALUATED",
        "expected_priority": "P4",
        "report_flow": [
            "금일 위험 요약",
            "KB 상품 근거 검색",
            "고객용·RM용 종합보고서 생성 동의",
            "고객용 PDF 및 RM용 PDF 저장",
            "KB 무역금융 상담사 연결 여부 표시",
        ],
    }


def _scenario_demo_2() -> dict[str, Any]:
    return {
        "demo_id": "DEMO2",
        "title": "사용자 제보 선적 9일 지연 - 예상 충돌 분석",
        "company_id": "DEMO2-CO",
        "transaction_id": "TXN-DEMO2",
        "case_id_after_field_matching": "CASE-DEMO2",
        "user_message": "CASE-DEMO2 거래의 선적이 9일 늦어질 예정입니다. 영향을 분석해 주세요.",
        "reported_at": "2026-08-20",
        "delay_days": 9,
        "planned_on_board_proxy": "2026-08-15",
        "revised_estimated_on_board_date": "2026-08-24",
        "payment_terms": "T/T 45 DAYS AFTER B/L DATE",
        "planned_estimated_payment_due_date": "2026-09-29",
        "revised_estimated_payment_due_date": "2026-10-08",
        "supplier_payment_date": "2026-10-03",
        "conflict_gap_days": -5,
        "payment_delay_days": 5,
        "expected_status": "EVALUATED",
        "expected_priority": "P4",
        "human_issue": {
            "field": "dependency_scope",
            "prompt": "이 공급업체 지급은 해당 수출대금 전액에 의존합니까, 일부에 의존합니까?",
            "demo_answer": "FULL",
            "display_answer": "전액",
        },
        "report_flow": [
            "예상 충돌 요약",
            "거래-금융이벤트 의존범위 확인",
            "금융위험 재계산",
            "KB 상품 근거 검색",
            "고객용·RM용 종합보고서 생성 동의",
            "고객용 PDF 및 RM용 PDF 저장",
            "KB 무역금융 상담사 연결 여부 표시",
        ],
    }


def _generate_demo(
    *,
    folder_name: str,
    fixture: DemoTradeFixture,
    include_bill_of_lading: bool,
    financial_seed: dict[str, Any] | None,
    scenario: dict[str, Any] | None,
    readme: str,
) -> dict[str, Any]:
    target = OUTPUT_ROOT / folder_name
    target.mkdir(parents=True, exist_ok=True)
    plan: list[tuple[str, DocumentType, Callable[[Path, DemoTradeFixture], None]]] = [
        ("01_booking.pdf", DocumentType.BOOKING_CONFIRMATION, _booking),
        ("02_invoice.pdf", DocumentType.COMMERCIAL_INVOICE, _invoice),
    ]
    if include_bill_of_lading:
        plan.append(("03_bill_of_lading.pdf", DocumentType.BILL_OF_LADING, _bill_of_lading))

    documents: list[dict[str, Any]] = []
    extractions: dict[DocumentType, dict[str, str]] = {}
    for file_name, doc_type, writer in plan:
        path = target / file_name
        writer(path, fixture)
        entry, extraction = _validate_document(path, fixture, doc_type)
        documents.append(entry)
        extractions[doc_type] = extraction

    matching: dict[str, Any] = {}
    booking = extractions[DocumentType.BOOKING_CONFIRMATION]
    invoice = extractions[DocumentType.COMMERCIAL_INVOICE]
    invoice_score = (50 if invoice["Invoice.seller"] == booking["Booking.shipper"] else 0) + (
        40 if invoice["Invoice.description_of_goods"] == booking["Booking.commodity"] else 0
    )
    matching["invoice_to_booking"] = {
        "score": invoice_score,
        "verdict": "MATCH" if invoice_score >= 70 else "REVIEW",
        "relations": [
            {"relation": "seller=shipper", "points": 50},
            {"relation": "description_of_goods=commodity", "points": 40},
        ],
    }
    if include_bill_of_lading:
        bill_of_lading = extractions[DocumentType.BILL_OF_LADING]
        bl_score = sum(
            (
                60 if bill_of_lading["B/L.bl_no"] == fixture.bl_no else 0,
                20 if bill_of_lading["B/L.shipper"] == booking["Booking.shipper"] else 0,
                5
                if bill_of_lading["B/L.port_of_loading"] == booking["Booking.port_of_loading"]
                else 0,
                5
                if bill_of_lading["B/L.port_of_discharge"] == booking["Booking.port_of_discharge"]
                else 0,
                5 if bill_of_lading["B/L.vessel_name"] == booking["Booking.vessel_name"] else 0,
                5 if bill_of_lading["B/L.voyage_no"] == booking["Booking.voyage_no"] else 0,
            )
        )
        matching["bill_of_lading_to_case"] = {
            "score": bl_score,
            "verdict": "MATCH" if bl_score >= 70 else "REVIEW",
            "relations": [
                {"relation": "bl_no=booking.bl_no", "points": 60},
                {"relation": "shipper=shipper", "points": 20},
                {"relation": "port_of_loading", "points": 5},
                {"relation": "port_of_discharge", "points": 5},
                {"relation": "vessel_name", "points": 5},
                {"relation": "voyage_no", "points": 5},
            ],
        }

    manifest = {
        "manifest_version": "judge-demo-final.v1",
        "demo_id": fixture.demo_id,
        "synthetic": True,
        "generator": "scripts/generate_final_demo_portfolio.py",
        "company_id": fixture.company_id,
        "finance_transaction_id": fixture.finance_transaction_id,
        "transaction_reference_intentionally_omitted": True,
        "expected_case_id_after_field_matching": f"CASE-{fixture.demo_id}",
        "expected_missing_documents": (
            [] if include_bill_of_lading else [DocumentType.BILL_OF_LADING.value]
        ),
        "document_count": len(documents),
        "documents": documents,
        "business_field_matching": matching,
    }
    _write_json(target / "manifest.json", manifest)
    if financial_seed is not None:
        _write_json(target / "financial_calendar_seed.json", financial_seed)
    if scenario is not None:
        _write_json(target / "scenario.json", scenario)
    _write_readme(target / "README.md", readme)
    return manifest


def _generate_demo_zero_batch() -> dict[str, Any]:
    """Generate one mixed eight-PDF upload containing three deterministic cases."""
    target = OUTPUT_ROOT / "demo_0_complete_batch"
    target.mkdir(parents=True, exist_ok=True)
    for stale_pdf in target.glob("*.pdf"):
        stale_pdf.unlink()

    case_plans: list[
        tuple[
            DemoTradeFixture,
            list[tuple[str, DocumentType, Callable[[Path, DemoTradeFixture], None]]],
        ]
    ] = [
        (
            FIXTURES["demo_0"],
            [
                ("01_booking.pdf", DocumentType.BOOKING_CONFIRMATION, _booking),
                ("02_invoice.pdf", DocumentType.COMMERCIAL_INVOICE, _invoice),
                ("03_bill_of_lading.pdf", DocumentType.BILL_OF_LADING, _bill_of_lading),
            ],
        ),
        (
            FIXTURES["demo_0_b"],
            [
                ("04_booking_demo0b.pdf", DocumentType.BOOKING_CONFIRMATION, _booking),
                ("05_invoice_demo0b.pdf", DocumentType.COMMERCIAL_INVOICE, _invoice),
                (
                    "06_bill_of_lading_demo0b.pdf",
                    DocumentType.BILL_OF_LADING,
                    _bill_of_lading,
                ),
            ],
        ),
        (
            FIXTURES["demo_0_c"],
            [
                ("07_booking_demo0c.pdf", DocumentType.BOOKING_CONFIRMATION, _booking),
                ("08_invoice_demo0c.pdf", DocumentType.COMMERCIAL_INVOICE, _invoice),
            ],
        ),
    ]

    documents: list[dict[str, Any]] = []
    case_manifests: list[dict[str, Any]] = []
    for fixture, plan in case_plans:
        extractions: dict[DocumentType, dict[str, str]] = {}
        case_documents: list[dict[str, Any]] = []
        for file_name, doc_type, writer in plan:
            path = target / file_name
            writer(path, fixture)
            entry, extraction = _validate_document(path, fixture, doc_type)
            documents.append(entry)
            case_documents.append(entry)
            extractions[doc_type] = extraction

        booking = extractions[DocumentType.BOOKING_CONFIRMATION]
        invoice = extractions[DocumentType.COMMERCIAL_INVOICE]
        matching: dict[str, Any] = {
            "invoice_to_booking": {
                "score": (
                    (50 if invoice["Invoice.seller"] == booking["Booking.shipper"] else 0)
                    + (
                        40
                        if invoice["Invoice.description_of_goods"]
                        == booking["Booking.commodity"]
                        else 0
                    )
                ),
                "verdict": "MATCH",
                "relations": [
                    {"relation": "seller=shipper", "points": 50},
                    {"relation": "description_of_goods=commodity", "points": 40},
                ],
            }
        }
        if DocumentType.BILL_OF_LADING in extractions:
            bill_of_lading = extractions[DocumentType.BILL_OF_LADING]
            matching["bill_of_lading_to_case"] = {
                "score": sum(
                    (
                        60 if bill_of_lading["B/L.bl_no"] == fixture.bl_no else 0,
                        20
                        if bill_of_lading["B/L.shipper"] == booking["Booking.shipper"]
                        else 0,
                        5
                        if bill_of_lading["B/L.port_of_loading"]
                        == booking["Booking.port_of_loading"]
                        else 0,
                        5
                        if bill_of_lading["B/L.port_of_discharge"]
                        == booking["Booking.port_of_discharge"]
                        else 0,
                        5
                        if bill_of_lading["B/L.vessel_name"]
                        == booking["Booking.vessel_name"]
                        else 0,
                        5
                        if bill_of_lading["B/L.voyage_no"] == booking["Booking.voyage_no"]
                        else 0,
                    )
                ),
                "verdict": "MATCH",
                "relations": [
                    {"relation": "bl_no=booking.bl_no", "points": 60},
                    {"relation": "shipper=shipper", "points": 20},
                    {"relation": "port_of_loading", "points": 5},
                    {"relation": "port_of_discharge", "points": 5},
                    {"relation": "vessel_name", "points": 5},
                    {"relation": "voyage_no", "points": 5},
                ],
            }

        case_manifests.append(
            {
                "demo_case_id": fixture.demo_id,
                "finance_transaction_id": fixture.finance_transaction_id,
                "expected_case_id_after_field_matching": f"CASE-{fixture.demo_id}",
                "expected_missing_documents": (
                    []
                    if DocumentType.BILL_OF_LADING in extractions
                    else [DocumentType.BILL_OF_LADING.value]
                ),
                "document_count": len(case_documents),
                "file_names": [entry["file_name"] for entry in case_documents],
                "business_field_matching": matching,
            }
        )

    manifest = {
        "manifest_version": "judge-demo-final.v2",
        "demo_id": "DEMO0",
        "synthetic": True,
        "generator": "scripts/generate_final_demo_portfolio.py",
        "company_id": "DEMO0-CO",
        "finance_transaction_id": "TXN-DEMO0",
        "finance_transaction_ids": [item["finance_transaction_id"] for item in case_manifests],
        "transaction_reference_intentionally_omitted": True,
        "expected_case_ids_after_field_matching": [
            item["expected_case_id_after_field_matching"] for item in case_manifests
        ],
        "expected_case_count": 3,
        "expected_document_type_counts": {
            DocumentType.BOOKING_CONFIRMATION.value: 3,
            DocumentType.COMMERCIAL_INVOICE.value: 3,
            DocumentType.BILL_OF_LADING.value: 2,
        },
        "document_count": len(documents),
        "documents": documents,
        "cases": case_manifests,
    }
    _write_json(target / "manifest.json", manifest)
    _write_readme(
        target / "README.md",
        """
# Demo 0 - 8개 혼합문서 자동분류·3개 거래 매칭

한 번에 업로드하는 파일은 Booking Confirmation 3개, Commercial Invoice 3개,
Bill of Lading 2개로 총 8개입니다. 파일 순서와 이름에 의존하지 않고
seller/shipper, goods/commodity, B/L No., 항구, 선박명, 항차번호의
결정론적 점수로 세 거래를 분리합니다.

- CASE-DEMO0: Booking + Invoice + B/L (완전 거래)
- CASE-DEMO0B: Booking + Invoice + B/L (완전 거래)
- CASE-DEMO0C: Booking + Invoice (B/L 미도착 거래)

예상 결과: 8개 문서 자동분류, TradeCase 후보 3개, 완전 거래 2개,
B/L 대기 거래 1개. B/L 전체 문서의 미도착은 수기 필드 질문으로 바꾸지 않고
AWAITING_DOCUMENT 상태로 저장합니다.
""",
    )
    return manifest


def generate() -> dict[str, Any]:
    OUTPUT_ROOT.mkdir(parents=True, exist_ok=True)
    manifests = [
        _generate_demo_zero_batch(),
        _generate_demo(
            folder_name="demo_1_manual_today_risk",
            fixture=FIXTURES["demo_1"],
            include_bill_of_lading=False,
            financial_seed=_financial_seed_demo_1(),
            scenario=_scenario_demo_1(),
            readme="""
# Demo 1 - 금일 금융위험 분석

1. Booking과 Invoice를 먼저 등록합니다. B/L 전체 문서는 아직 없으므로
   수기 필드 입력을 요구하지 않고 AWAITING_BILL_OF_LADING 상태로 보존합니다.
2. financial_calendar_seed.json을 그대로 2-Sheet XLSX로 작성해 등록합니다.
3. 시스템 날짜를 2026-08-20으로 두고 챗봇 메뉴 2번을 실행합니다.
4. 위험 요약 뒤 상품 검토와 고객용·RM용 보고서 생성 동의를 선택합니다.

예상 핵심: ETD 2026-08-05, 대출만기 2026-10-10, 60일 결제조건,
기준 선적일 2026-08-11 경과로 PREEMPTIVE_BREACH/ACTION_REQUIRED.
""",
        ),
        _generate_demo(
            folder_name="demo_2_reported_delay",
            fixture=FIXTURES["demo_2"],
            include_bill_of_lading=False,
            financial_seed=_financial_seed_demo_2(),
            scenario=_scenario_demo_2(),
            readme="""
# Demo 2 - 사용자 제보 9일 지연

1. Booking과 Invoice 및 금융일정이 등록된 상태에서 시작합니다.
2. 사용자가 `CASE-DEMO2 거래의 선적이 9일 늦어질 예정입니다`라고 입력합니다.
3. 계획 기준일 2026-08-15가 2026-08-24로 이동하며, 예상 지급기준일은
   2026-09-29에서 2026-10-08로 변경됩니다.
4. 2026-10-03 공급업체 지급과 5일 예상 충돌이 발견됩니다.
5. Human 질문에는 `전액(FULL)`을 선택하고 재계산한 뒤 상품과 보고서를 생성합니다.

마지막 UI는 고객용·RM용 PDF 링크와 KB 무역금융 상담사 연결 여부만 표시합니다.
""",
        ),
    ]
    root_manifest = {
        "portfolio_version": "judge-demo-final.v1",
        "synthetic": True,
        "generator": "scripts/generate_final_demo_portfolio.py",
        "demos": [
            {
                "demo_id": item["demo_id"],
                "company_id": item["company_id"],
                "finance_transaction_id": item["finance_transaction_id"],
                "document_count": item["document_count"],
                "folder": {
                    "DEMO0": "demo_0_complete_batch",
                    "DEMO1": "demo_1_manual_today_risk",
                    "DEMO2": "demo_2_reported_delay",
                }[item["demo_id"]],
            }
            for item in manifests
        ],
        "total_document_count": sum(item["document_count"] for item in manifests),
        "research_reference": "reference/데모1_2_간소화_시나리오_원본.xlsx",
        "api_calls": 0,
    }
    _write_json(OUTPUT_ROOT / "manifest.json", root_manifest)
    return root_manifest


def main() -> None:
    manifest = generate()
    print(OUTPUT_ROOT)
    print(json.dumps(manifest, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()

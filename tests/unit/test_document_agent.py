from __future__ import annotations

from pathlib import Path

import pytest

from app.schemas.document import LayoutDocument, LayoutLine, LayoutPage
from app.services.ingestion.document_analyzer import (
    analyze_core_document,
    analyze_core_documents,
)
from app.services.ingestion.ocr_backend import NativePdfLayoutBackend, PaddleOcrBackend

PROJECT_ROOT = Path(__file__).resolve().parents[2]
FIXTURE_DIR = PROJECT_ROOT / "data" / "fixtures" / "live_trade_documents"
KB_DOC_DIR = PROJECT_ROOT / "kb_doc"


def test_actual_kb_doc_three_pdf_native_fallback_regression() -> None:
    paths = [
        KB_DOC_DIR / "booking.pdf",
        next(KB_DOC_DIR.glob("*Commercial Invoice*.pdf")),
        next(KB_DOC_DIR.glob("*Bill of Lading*.pdf")),
    ]

    booking, invoice, bill = analyze_core_documents(paths, backend=NativePdfLayoutBackend())

    assert [result.status for result in (booking, invoice, bill)] == [
        "ANALYZED",
        "ANALYZED",
        "ANALYZED",
    ]
    assert booking.fields["booking_no"].value == "HANA22421900"
    assert booking.fields["etd"].value == "2020-06-12"
    assert invoice.fields["invoice_no"].value == "8905 BK 1007"
    assert invoice.fields["invoice_date"].value == "2007-05-20"
    assert invoice.fields["total_amount"].value == "60000"
    assert invoice.fields["currency"].value == "USD"
    assert bill.fields["bl_no"].value == "But 1004"
    assert bill.fields["on_board_date"].value == "2000-05-21"


def test_three_document_portfolio_projection_is_small_and_evidence_backed() -> None:
    paths = [
        FIXTURE_DIR / "01_TRD-001_booking.pdf",
        FIXTURE_DIR / "02_TRD-001_invoice.pdf",
        FIXTURE_DIR / "03_TRD-001_bill_of_lading.pdf",
    ]

    booking, invoice, bill = analyze_core_documents(paths, backend=NativePdfLayoutBackend())

    assert set(booking.fields) == {"booking_no", "etd"}
    assert set(invoice.fields) == {
        "invoice_no",
        "invoice_date",
        "total_amount",
        "currency",
    }
    assert set(bill.fields) == {"bl_no", "on_board_date"}
    assert booking.fields["etd"].value == "2026-08-20"
    assert invoice.fields["total_amount"].value == "45000.00"
    assert invoice.fields["currency"].value == "USD"
    assert bill.fields["on_board_date"].value == "2026-08-20"
    assert all(
        field.page == 1
        and len(field.bbox) == 4
        and field.bbox != [0.0, 0.0, 0.0, 0.0]
        and field.confidence > 0
        for result in (booking, invoice, bill)
        for field in result.fields.values()
    )


def test_missing_on_board_date_is_review_required_without_guessing() -> None:
    result = analyze_core_document(
        FIXTURE_DIR / "06_TRD-002_bill_of_lading_missing_on_board_date.pdf",
        backend=NativePdfLayoutBackend(),
    )

    assert result.status == "REVIEW_REQUIRED"
    assert result.missing_core_fields == ["on_board_date"]
    assert "on_board_date" not in result.fields


class StaticLayoutBackend:
    name = "test_layout"

    def __init__(self, document: LayoutDocument) -> None:
        self.document = document

    def extract(self, _path: Path) -> LayoutDocument:
        return self.document


def _line(text: str, bbox: list[float]) -> LayoutLine:
    return LayoutLine(text=text, page=1, bbox=bbox, confidence=0.94, source="test_layout")


def test_separate_label_and_value_boxes_use_right_and_below_geometry(tmp_path: Path) -> None:
    path = tmp_path / "scan.pdf"
    path.write_bytes(b"not-read-by-static-backend")
    document = LayoutDocument(
        backend="test_layout",
        pages=[
            LayoutPage(
                page=1,
                width=1000,
                height=1400,
                lines=[
                    _line("COMMERCIAL INVOICE", [0.05, 0.05, 0.35, 0.08]),
                    _line("Invoice No. and date", [0.05, 0.20, 0.28, 0.23]),
                    _line("INV-GEO-7 AUG. 28. 2026", [0.35, 0.20, 0.65, 0.23]),
                    _line("Amount", [0.05, 0.30, 0.15, 0.33]),
                    _line("EUR 12,500.00", [0.35, 0.30, 0.55, 0.33]),
                ],
            )
        ],
    )

    result = analyze_core_document(path, backend=StaticLayoutBackend(document))

    assert result.status == "ANALYZED"
    assert result.fields["invoice_no"].value == "INV-GEO-7"
    assert result.fields["invoice_date"].value == "2026-08-28"
    assert result.fields["total_amount"].value == "12500.00"
    assert result.fields["currency"].value == "EUR"
    assert result.fields["invoice_no"].bbox == [0.05, 0.2, 0.65, 0.23]


def test_paddle_backend_records_native_fallback_reason(monkeypatch: pytest.MonkeyPatch) -> None:
    backend = PaddleOcrBackend(fallback=NativePdfLayoutBackend())

    def unavailable() -> None:
        raise ModuleNotFoundError("paddleocr")

    monkeypatch.setattr(backend, "_load_engine", unavailable)
    layout = backend.extract(FIXTURE_DIR / "01_TRD-001_booking.pdf")

    assert layout.backend == "native_pdf_layout"
    assert layout.fallback_used is True
    assert any("PaddleOCR unavailable or failed" in warning for warning in layout.warnings)

from __future__ import annotations

import pytest

from app.core.config import PROJECT_ROOT
from app.schemas.field_contract import DocumentType
from app.services.ingestion.fixed_extractors import extract_fixed_document

FIXTURE_DIR = PROJECT_ROOT / "data" / "fixtures" / "live_trade_documents"


@pytest.mark.parametrize(
    ("filename", "doc_type", "transaction_reference"),
    [
        (
            "01_TRD-001_booking.pdf",
            DocumentType.BOOKING_CONFIRMATION,
            "TRD-001",
        ),
        (
            "02_TRD-001_invoice.pdf",
            DocumentType.COMMERCIAL_INVOICE,
            "TRD-001",
        ),
        (
            "03_TRD-001_bill_of_lading.pdf",
            DocumentType.BILL_OF_LADING,
            "TRD-001",
        ),
    ],
)
def test_live_pdfs_are_auto_confirmed_with_contract_coverage(
    filename: str,
    doc_type: DocumentType,
    transaction_reference: str,
) -> None:
    result = extract_fixed_document(FIXTURE_DIR / filename)

    assert result.classification.status == "AUTO_CONFIRMED"
    assert result.classification.doc_type is doc_type
    assert result.source_references["transaction_reference"] == transaction_reference
    assert result.model_calls == 0
    assert result.missing_core_fields == []


def test_live_blank_on_board_date_is_reported_without_fabrication() -> None:
    result = extract_fixed_document(
        FIXTURE_DIR / "06_TRD-002_bill_of_lading_missing_on_board_date.pdf"
    )

    assert result.classification.status == "AUTO_CONFIRMED"
    assert result.classification.doc_type is DocumentType.BILL_OF_LADING
    assert result.missing_core_fields == ["on_board_date"]
    assert "on_board_date" not in result.fields
    assert result.model_calls == 0

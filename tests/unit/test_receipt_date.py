from datetime import date
from pathlib import Path

import pytest

from app.agents.document_agent import DocumentAgent
from app.schemas.portfolio import PortfolioDocumentResult, PortfolioRunRequest
from app.services.llm_controller import RunModel
from app.services.receipt_date import resolve_receipt_date
from tests.helpers import ScriptedModel

ROOT = Path(__file__).resolve().parents[2]
REFERENCES = {"invoice_no": "INV-DEMO-001", "bl_no": "BL-DEMO-001", "booking_no": "BK-DEMO-001"}
RAW = [
    ("BOOKING_CONFIRMATION", {"booking_no": "BK-DEMO-001", "etd": "2026-09-18"}),
    (
        "COMMERCIAL_INVOICE",
        {
            "invoice_no": "INV-DEMO-001",
            "invoice_date": "2026-09-15",
            "total_amount": "50,000.00",
            "currency": "USD",
            "payment_terms": "T/T 30 CALENDAR DAYS AFTER B/L ON BOARD DATE",
        },
    ),
    ("BILL_OF_LADING", {"bl_no": "BL-DEMO-001", "on_board_date": "2026-09-21"}),
]


def extraction_submission() -> tuple[str, dict[str, object]]:
    return "validate_fields", {
        "documents": [
            {
                "document_id": f"doc-{i}",
                "document_type": kind,
                "fields": [{"field": k, "raw_value": v, "page": 1} for k, v in fields.items()],
            }
            for i, (kind, fields) in enumerate(RAW, 1)
        ]
    }


@pytest.fixture
def documents() -> list[PortfolioDocumentResult]:
    paths = [
        ROOT / "data/fixtures/tradeflow_example" / n
        for n in ["01_booking.pdf", "02_invoice.pdf", "03_bill_of_lading.pdf"]
    ]
    model = ScriptedModel(["read_pdf", extraction_submission()])
    return DocumentAgent().run(paths, model=RunModel(model, 12))


def request(**kwargs: object) -> PortfolioRunRequest:
    return PortfolioRunRequest(trade_direction="EXPORT", **kwargs)


def test_verified_invoice_and_bl_calculate_calendar_days(
    documents: list[PortfolioDocumentResult],
) -> None:
    receipt = resolve_receipt_date(request(), documents, REFERENCES)
    assert receipt.expected_receipt_date == date(2026, 10, 21)
    assert receipt.status == "CALCULATED" and receipt.document_links_verified
    assert len(receipt.evidence) == 2
    assert documents[1].fields["total_amount"] == "50000.00"


@pytest.mark.parametrize(
    "terms",
    [
        "L/C AT SIGHT",
        "NET 30",
        "T/T 30 CALENDAR DAYS AFTER B/L DATE",
        "T/T 30 BUSINESS DAYS AFTER B/L DATE",
        "50% ADVANCE / 50% 30 DAYS AFTER B/L DATE",
        "T/T 0 DAYS AFTER B/L DATE",
        "T/T 999 DAYS AFTER B/L DATE",
        "",
    ],
)
def test_ambiguous_or_unsupported_terms_require_review(
    documents: list[PortfolioDocumentResult], terms: str
) -> None:
    documents[1].fields["payment_terms"] = terms
    result = resolve_receipt_date(request(), documents, REFERENCES)
    assert result.expected_receipt_date is None and result.status == "REVIEW_REQUIRED"


def test_no_bl_never_substitutes_booking_etd(documents: list[PortfolioDocumentResult]) -> None:
    assert resolve_receipt_date(request(), documents[:2], REFERENCES).expected_receipt_date is None


def test_wrong_document_id_blocks_even_manual_date(
    documents: list[PortfolioDocumentResult],
) -> None:
    documents[1].fields["invoice_no"] = "OTHER-INVOICE"
    assert (
        resolve_receipt_date(
            request(expected_receipt_date=date(2026, 10, 21)), documents, REFERENCES
        ).expected_receipt_date
        is None
    )


def test_missing_reference_blocks_auto_calculation(
    documents: list[PortfolioDocumentResult],
) -> None:
    assert resolve_receipt_date(request(), documents, {}).status == "REVIEW_REQUIRED"


def test_manual_receipt_date_preserves_legacy_input() -> None:
    result = resolve_receipt_date(request(expected_receipt_date=date(2026, 10, 21)), [], {})
    assert result.status == "USER_PROVIDED"


def test_low_confidence_critical_field_requires_review(
    documents: list[PortfolioDocumentResult],
) -> None:
    documents[2].evidence[-1]["confidence"] = 0.5
    assert resolve_receipt_date(request(), documents, REFERENCES).expected_receipt_date is None


def test_partial_payment_terms_cannot_be_accepted_as_full_terms(
    documents: list[PortfolioDocumentResult],
) -> None:
    evidence = next(e for e in documents[1].evidence if e["field"] == "payment_terms")
    evidence["source_line"] = "Payment Terms: 50% ADVANCE; " + evidence["raw_value"]
    assert resolve_receipt_date(request(), documents, REFERENCES).expected_receipt_date is None


def test_invoice_anchor_is_supported_with_matching_evidence(
    documents: list[PortfolioDocumentResult],
) -> None:
    invoice = documents[1]
    invoice.fields["payment_terms"] = "30 DAYS AFTER INVOICE DATE"
    for evidence in invoice.evidence:
        if evidence["field"] == "payment_terms":
            evidence.update(
                raw_value=invoice.fields["payment_terms"],
                source_line="Payment Terms: " + invoice.fields["payment_terms"],
            )
    assert resolve_receipt_date(request(), documents, REFERENCES).expected_receipt_date == date(
        2026, 10, 15
    )

from __future__ import annotations

from collections import Counter

import pytest

from app.domain_inputs.providers import field_registry
from app.schemas.field_contract import PROJECTION_TARGETS, DocumentType
from app.tools.architecture import inspect_field_contract_diagnostic


def test_field_registry_has_exact_98_and_core_22() -> None:
    registry = field_registry()
    assert len(registry.entries) == 98
    assert Counter(entry.doc_type for entry in registry.entries) == {
        DocumentType.BILL_OF_LADING: 23,
        DocumentType.BOOKING_CONFIRMATION: 52,
        DocumentType.COMMERCIAL_INVOICE: 23,
    }
    assert Counter(
        entry.doc_type for entry in registry.entries if entry.core_blocking_candidate
    ) == {
        DocumentType.BILL_OF_LADING: 8,
        DocumentType.BOOKING_CONFIRMATION: 8,
        DocumentType.COMMERCIAL_INVOICE: 6,
    }
    assert registry.core_keys(DocumentType.BILL_OF_LADING) == {
        "on_board_date",
        "bl_no",
        "port_of_loading",
        "vessel_name",
        "port_of_discharge",
        "shipper",
        "consignee",
        "voyage_no",
    }
    assert registry.core_keys(DocumentType.BOOKING_CONFIRMATION) == {
        "booking_no",
        "shipper",
        "vessel_name",
        "voyage_no",
        "port_of_loading",
        "port_of_discharge",
        "etd",
        "commodity",
    }
    assert registry.core_keys(DocumentType.COMMERCIAL_INVOICE) == {
        "invoice No.",
        "currency",
        "seller",
        "buyer",
        "description_of_goods",
        "payment_terms",
    }
    assert all(key[1] in registry.core_keys(key[0]) for key in PROJECTION_TARGETS)


def test_protected_invoice_exact_keys_and_safe_keys() -> None:
    registry = field_registry()
    for exact, safe in {
        "invoice No.": "invoice_no",
        "shipping Marks": "shipping_marks",
        "signed by": "signed_by",
    }.items():
        entry = registry.by_exact(DocumentType.COMMERCIAL_INVOICE, exact)
        assert entry.exact_standard_field == exact
        assert entry.safe_key == safe


def test_inspect_field_contract_reads_the_reviewed_registry() -> None:
    result = inspect_field_contract_diagnostic(
        doc_type="BOOKING_CONFIRMATION",
        expected_field_count=52,
    )
    assert result["actual_field_count"] == 52
    assert result["core_field_count"] == 8
    assert result["contract_match"] is True
    assert result["contract_status"] == "PASS"
    assert len(result["exact_standard_fields"]) == 52
    assert len(result["core_fields"]) == 8
    assert result["contract_source_path"].endswith("document_field_dictionary.xlsx")
    assert len(result["contract_source_sha256"]) == 64
    assert not hasattr(inspect_field_contract_diagnostic, "invoke")


@pytest.mark.parametrize(
    ("alias", "expected"),
    [
        ("Booking", "BOOKING_CONFIRMATION"),
        ("Commercial Invoice", "COMMERCIAL_INVOICE"),
        ("B/L", "BILL_OF_LADING"),
        ("Bill of Landing", "BILL_OF_LADING"),
    ],
)
def test_inspect_field_contract_normalizes_reviewed_document_aliases(
    alias: str,
    expected: str,
) -> None:
    expected_count = {
        "BOOKING_CONFIRMATION": 52,
        "COMMERCIAL_INVOICE": 23,
        "BILL_OF_LADING": 23,
    }[expected]
    result = inspect_field_contract_diagnostic(
        doc_type=alias,
        expected_field_count=expected_count,
    )

    assert result["doc_type"] == expected
    assert result["contract_status"] == "PASS"


def test_inspect_field_contract_reports_a_count_mismatch() -> None:
    result = inspect_field_contract_diagnostic(
        doc_type="COMMERCIAL_INVOICE",
        expected_field_count=999,
    )
    assert result["actual_field_count"] == 23
    assert result["core_field_count"] == 6
    assert result["contract_match"] is False
    assert result["contract_status"] == "MISMATCH"


@pytest.mark.parametrize(
    ("doc_type", "expected_field_count", "message"),
    [
        ("UNKNOWN_DOCUMENT", 1, "Unsupported doc_type"),
        ("BILL_OF_LADING", -1, "must be non-negative"),
    ],
)
def test_inspect_field_contract_fails_closed_for_invalid_input(
    doc_type: str,
    expected_field_count: int,
    message: str,
) -> None:
    with pytest.raises(ValueError, match=message):
        inspect_field_contract_diagnostic(
            doc_type=doc_type,
            expected_field_count=expected_field_count,
        )

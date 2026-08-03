from __future__ import annotations

from typing import Any

from app.domain_inputs.providers import field_registry
from app.schemas.field_contract import DocumentType

_DOCUMENT_TYPE_ALIASES = {
    "BOOKING": DocumentType.BOOKING_CONFIRMATION,
    "BOOKING_CONFIRM": DocumentType.BOOKING_CONFIRMATION,
    "BOOKING_CONFIRMATION": DocumentType.BOOKING_CONFIRMATION,
    "CI": DocumentType.COMMERCIAL_INVOICE,
    "COMMERCIAL_INVOICE": DocumentType.COMMERCIAL_INVOICE,
    "INVOICE": DocumentType.COMMERCIAL_INVOICE,
    "BILL_OF_LADING": DocumentType.BILL_OF_LADING,
    "BILL_OF_LANDING": DocumentType.BILL_OF_LADING,
    "BL": DocumentType.BILL_OF_LADING,
    "BOL": DocumentType.BILL_OF_LADING,
}


def _normalize_document_type(value: str) -> DocumentType:
    normalized = value.strip().upper().replace("/", "").replace("-", "_").replace(" ", "_")
    while "__" in normalized:
        normalized = normalized.replace("__", "_")
    document_type = _DOCUMENT_TYPE_ALIASES.get(normalized)
    if document_type is not None:
        return document_type
    supported = ", ".join(item.value for item in DocumentType)
    raise ValueError(f"Unsupported doc_type: {value}. Supported values: {supported}")


def inspect_field_contract_diagnostic(
    doc_type: str,
    expected_field_count: int,
) -> dict[str, Any]:
    """Inspect the reviewed XLSX FieldRegistry without exposing an Agent Tool."""
    if expected_field_count < 0:
        raise ValueError("expected_field_count must be non-negative")
    document_type = _normalize_document_type(doc_type)

    registry = field_registry()
    entries = registry.for_document(document_type)
    core_fields = [entry.exact_standard_field for entry in entries if entry.core_blocking_candidate]
    actual_field_count = len(entries)
    contract_match = actual_field_count == expected_field_count
    return {
        "doc_type": document_type.value,
        "expected_field_count": expected_field_count,
        "actual_field_count": actual_field_count,
        "core_field_count": len(core_fields),
        "contract_match": contract_match,
        "contract_status": "PASS" if contract_match else "MISMATCH",
        "exact_standard_fields": [entry.exact_standard_field for entry in entries],
        "core_fields": core_fields,
        "contract_source_path": registry.source_path,
        "contract_source_sha256": registry.source_sha256,
    }

from __future__ import annotations

import re
from enum import StrEnum

from pydantic import BaseModel, Field


class DocumentType(StrEnum):
    """Document types supported by the reviewed fixed-template contract."""

    BILL_OF_LADING = "BILL_OF_LADING"
    BOOKING_CONFIRMATION = "BOOKING_CONFIRMATION"
    COMMERCIAL_INVOICE = "COMMERCIAL_INVOICE"


class FieldRegistryEntry(BaseModel):
    """One exact XLSX field plus its safe operational projection metadata."""

    doc_type: DocumentType
    exact_standard_field: str
    safe_key: str
    required_in_document: bool
    core_blocking_candidate: bool
    aliases: list[str] = Field(default_factory=list)
    meaning: str = ""
    example: str = ""
    normalizer: str = "text"
    projection_target: str | None = None


class FieldRegistry(BaseModel):
    """Validated 98-field registry and immutable source version."""

    entries: list[FieldRegistryEntry]
    source_sha256: str
    source_path: str

    def for_document(self, doc_type: DocumentType) -> list[FieldRegistryEntry]:
        """Return entries for one fixed document type in workbook order."""
        return [entry for entry in self.entries if entry.doc_type is doc_type]

    def exact_keys(self, doc_type: DocumentType) -> set[str]:
        """Return the exact source-contract keys for one document type."""
        return {entry.exact_standard_field for entry in self.for_document(doc_type)}

    def core_keys(self, doc_type: DocumentType) -> set[str]:
        """Return the actual core-sheet rows for one document type."""
        return {
            entry.exact_standard_field
            for entry in self.for_document(doc_type)
            if entry.core_blocking_candidate
        }

    def by_exact(self, doc_type: DocumentType, exact_standard_field: str) -> FieldRegistryEntry:
        """Resolve an exact key without aliases or case folding."""
        for entry in self.entries:
            if entry.doc_type is doc_type and entry.exact_standard_field == exact_standard_field:
                return entry
        raise KeyError(f"{doc_type.value}::{exact_standard_field}")


PROJECTION_TARGETS: dict[tuple[DocumentType, str], str] = {
    (DocumentType.BOOKING_CONFIRMATION, "booking_no"): "shipment.booking_no",
    (DocumentType.BOOKING_CONFIRMATION, "vessel_name"): "shipment.planned_vessel_name",
    (DocumentType.BOOKING_CONFIRMATION, "voyage_no"): "shipment.planned_voyage_no",
    (DocumentType.BOOKING_CONFIRMATION, "etd"): "shipment.etd",
    (DocumentType.BILL_OF_LADING, "bl_no"): "shipment.bl_no",
    (DocumentType.BILL_OF_LADING, "vessel_name"): "shipment.actual_vessel_name",
    (DocumentType.BILL_OF_LADING, "voyage_no"): "shipment.actual_voyage_no",
    (DocumentType.BILL_OF_LADING, "on_board_date"): "shipment.on_board_date",
    (DocumentType.COMMERCIAL_INVOICE, "invoice No."): "trade_case.invoice_no",
    (DocumentType.COMMERCIAL_INVOICE, "currency"): "trade_case.currency",
    (
        DocumentType.COMMERCIAL_INVOICE,
        "description_of_goods",
    ): "trade_case.goods",
    (
        DocumentType.COMMERCIAL_INVOICE,
        "payment_terms",
    ): "payment_obligation.raw_text",
}


def to_safe_key(exact_key: str) -> str:
    """Convert an exact source key to a collision-checkable Python/DB key."""
    key = re.sub(r"[^0-9A-Za-z]+", "_", exact_key.strip()).strip("_").lower()
    key = re.sub(r"_+", "_", key)
    if not key:
        raise ValueError(f"Cannot create safe key from {exact_key!r}")
    if key[0].isdigit():
        key = f"field_{key}"
    return key

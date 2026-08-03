from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class PaymentTermExample(BaseModel):
    """Ground-truth example row from the final PaymentTerms workbook."""

    group: str
    category: str
    raw_text: str
    payment_ratio: str | None
    payment_method: str
    credit_term_type: str | None
    lc_type: str | None
    anchor_type_extracted: str
    anchor_type_effective: str
    tenor_days: int | None
    day_type_extracted: str
    day_type_effective: str | None
    advance_or_deferred: str
    calculation_allowed: str
    document_stage: str
    real_world_status: str
    reason: str
    customer_question: str | None


class PaymentTermsContract(BaseModel):
    """Validated four-sheet PaymentTerms contract."""

    source_sha256: str
    source_path: str
    sheet_rows: dict[str, list[dict[str, Any]]]
    examples: list[PaymentTermExample]

    def exact_examples(self, raw_text: str) -> list[PaymentTermExample]:
        """Return all tranches for an exact example sentence."""
        normalized = " ".join(raw_text.upper().split())
        return [
            example
            for example in self.examples
            if " ".join(example.raw_text.upper().split()) == normalized
        ]


class ParsedPaymentObligation(BaseModel):
    """One parsed payment tranche with an explicit calculation gate."""

    tranche_index: int = 1
    raw_text: str
    payment_method: str
    credit_term_type: str = "UNKNOWN"
    lc_type: str | None = None
    anchor_type_extracted: str = "UNKNOWN"
    anchor_type_effective: str = "UNKNOWN"
    tenor_days: int | None = None
    day_type_extracted: str = "UNKNOWN"
    day_type_effective: str | None = None
    payment_ratio: str | None = None
    advance_or_deferred: str = "UNKNOWN"
    calculation_allowed: str
    confidence_flag: str = "NEEDS_REVIEW"
    document_stage: str = "FINAL"
    real_world_status: str = "SYNTHETIC"
    verified: bool = False
    missing_fields: list[str] = Field(default_factory=list)
    customer_question: str | None = None

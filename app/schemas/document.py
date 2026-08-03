from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field

from app.schemas.field_contract import DocumentType


class ClassificationResult(BaseModel):
    """Deterministic fixed-template classification result."""

    status: Literal["AUTO_CONFIRMED", "CONFIRM_REQUIRED", "UNSUPPORTED"]
    doc_type: DocumentType | None
    template_id: str | None
    score: float = Field(ge=0, le=1)
    signals: list[str] = Field(default_factory=list)
    model_calls: int = 0


class ExtractedField(BaseModel):
    """One exact field value with document evidence."""

    exact_standard_field: str
    safe_key: str
    raw_label: str
    raw_value: str
    normalized_value: Any
    page: int = 1
    bbox: list[float] | None = None
    confidence: float = Field(ge=0, le=1)


class DocumentExtractionResult(BaseModel):
    """Classifier and fixed extractor output for one PDF."""

    classification: ClassificationResult
    fields: dict[str, ExtractedField] = Field(default_factory=dict)
    source_references: dict[str, str] = Field(default_factory=dict)
    missing_core_fields: list[str] = Field(default_factory=list)
    field_contract_version: str
    template_source_hash: str
    model_calls: int = 0

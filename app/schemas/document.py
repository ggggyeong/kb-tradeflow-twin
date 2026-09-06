from __future__ import annotations

from enum import StrEnum
from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator, model_validator


class DocumentType(StrEnum):
    """The three trade documents used by the portfolio scenario."""

    BOOKING_CONFIRMATION = "BOOKING_CONFIRMATION"
    COMMERCIAL_INVOICE = "COMMERCIAL_INVOICE"
    BILL_OF_LADING = "BILL_OF_LADING"


class ClassificationResult(BaseModel):
    """Deterministic document classification result."""

    status: Literal["AUTO_CONFIRMED", "REVIEW_REQUIRED", "UNSUPPORTED"]
    doc_type: DocumentType | None
    score: float = Field(ge=0, le=1)
    signals: list[str] = Field(default_factory=list)


class LayoutLine(BaseModel):
    """One OCR/native-text line with normalized, top-left-origin geometry."""

    text: str = Field(min_length=1)
    page: int = Field(ge=1)
    bbox: list[float] = Field(min_length=4, max_length=4)
    confidence: float = Field(ge=0, le=1)
    source: str = Field(min_length=1)

    @field_validator("bbox")
    @classmethod
    def validate_bbox(cls, value: list[float]) -> list[float]:
        x0, y0, x1, y1 = value
        if not all(0 <= coordinate <= 1 for coordinate in value):
            raise ValueError("layout bbox coordinates must be normalized to 0..1")
        if x1 < x0 or y1 < y0:
            raise ValueError("layout bbox must satisfy x0 <= x1 and y0 <= y1")
        return value


class LayoutPage(BaseModel):
    """A PDF page normalized independently of the provider's pixel scale."""

    page: int = Field(ge=1)
    width: float = Field(gt=0)
    height: float = Field(gt=0)
    lines: list[LayoutLine] = Field(default_factory=list)

    @model_validator(mode="after")
    def lines_belong_to_page(self) -> LayoutPage:
        if any(line.page != self.page for line in self.lines):
            raise ValueError("every layout line must reference its containing page")
        return self


class LayoutDocument(BaseModel):
    """Provider-neutral OCR result used by classification and field extraction."""

    backend: str = Field(min_length=1)
    pages: list[LayoutPage] = Field(min_length=1)
    fallback_used: bool = False
    warnings: list[str] = Field(default_factory=list)

    @property
    def text(self) -> str:
        """Reading-order text for signatures only; fields use layout geometry."""
        return "\n".join(
            line.text
            for page in sorted(self.pages, key=lambda item: item.page)
            for line in sorted(page.lines, key=lambda item: (item.bbox[1], item.bbox[0]))
        )


class ExtractedField(BaseModel):
    """One portfolio field with the label and coordinates that support it."""

    value: Any
    raw_value: str
    raw_label: str
    page: int = Field(ge=1)
    bbox: list[float] = Field(min_length=4, max_length=4)
    confidence: float = Field(ge=0, le=1)

    @field_validator("bbox")
    @classmethod
    def validate_bbox(cls, value: list[float]) -> list[float]:
        x0, y0, x1, y1 = value
        if not all(0 <= coordinate <= 1 for coordinate in value):
            raise ValueError("field evidence bbox coordinates must be normalized to 0..1")
        if x1 < x0 or y1 < y0:
            raise ValueError("field evidence bbox must satisfy x0 <= x1 and y0 <= y1")
        return value


class DocumentAnalysis(BaseModel):
    """Small, database-free output consumed by the portfolio agent pipeline."""

    file_name: str
    source_sha256: str
    status: Literal["ANALYZED", "REVIEW_REQUIRED", "UNSUPPORTED"]
    doc_type: DocumentType | None
    fields: dict[str, ExtractedField] = Field(default_factory=dict)
    missing_core_fields: list[str] = Field(default_factory=list)
    ocr_backend: str
    ocr_fallback_used: bool = False
    warnings: list[str] = Field(default_factory=list)

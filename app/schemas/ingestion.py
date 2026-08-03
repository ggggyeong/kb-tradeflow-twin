from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field

from app.schemas.document import DocumentExtractionResult
from app.schemas.field_contract import DocumentType


class AnalyzedDocument(BaseModel):
    """One staged immutable upload and its deterministic analysis."""

    document_id: str
    file_name: str
    sha256: str
    object_path: str
    kind: Literal["PDF"]
    extraction: DocumentExtractionResult | None = None
    duplicate_existing: bool = False

    @property
    def doc_type(self) -> DocumentType | None:
        return self.extraction.classification.doc_type if self.extraction is not None else None


class ManualFieldOverride(BaseModel):
    """Append-only human correction that never replaces the extracted source fact."""

    override_id: str
    document_id: str
    exact_standard_field: str
    raw_input: str
    normalized_value: Any
    actor: str
    reason: str | None = None
    sequence: int = Field(ge=1)


class CaseProposal(BaseModel):
    """Atomic domain commit proposal for one matched transaction."""

    case_id: str
    document_ids: list[str]
    booking_document_id: str | None = None
    invoice_document_id: str | None = None
    bl_document_id: str | None = None
    match_scores: dict[str, float] = Field(default_factory=dict)
    missing_documents: list[DocumentType] = Field(default_factory=list)
    missing_core_fields: list[str] = Field(default_factory=list)
    status: Literal[
        "READY",
        "AWAITING_FIELD_INPUT",
        "AWAITING_DOCUMENT",
        "CONFIRMATION_REQUIRED",
        "DRAFT",
    ]


class HumanRequest(BaseModel):
    """One exact field question; whole-document gaps are intentionally excluded."""

    request_id: str
    request_type: Literal["DOCUMENT_FIELD_VALUE"] = "DOCUMENT_FIELD_VALUE"
    case_id: str
    document_id: str
    doc_type: DocumentType
    exact_standard_field: str
    field_path: str
    prompt: str
    status: Literal["OPEN", "RESOLVED"] = "OPEN"


class MissingDocumentIssue(BaseModel):
    """Informational document gap that must not fabricate a field-level question."""

    issue_id: str
    case_id: str
    doc_type: DocumentType
    severity: Literal["INFO"] = "INFO"
    message: str


class BatchSummary(BaseModel):
    """Chat/UI summary that separates missing documents from missing core fields."""

    batch_id: str
    file_count: int
    case_count: int
    cases: list[CaseProposal]
    unmatched_document_ids: list[str] = Field(default_factory=list)
    type_confirmation_document_ids: list[str] = Field(default_factory=list)
    human_requests: list[HumanRequest] = Field(default_factory=list)
    missing_document_issues: list[MissingDocumentIssue] = Field(default_factory=list)
    domain_commit_completed: int = 0
    confirmation_pending: int = 0
    duplicate_document_count: int = 0


class DocumentIntelligenceResult(BaseModel):
    """Persistable handoff from classify/extract/validate to transaction bundling."""

    batch_id: str
    documents: list[AnalyzedDocument]
    trace: list[dict[str, Any]] = Field(default_factory=list)


class BatchAnalysis(BaseModel):
    """Serializable analysis stored before any domain commit."""

    batch_id: str
    documents: list[AnalyzedDocument]
    summary: BatchSummary
    manual_overrides: list[ManualFieldOverride] = Field(default_factory=list)
    trace: list[dict[str, Any]] = Field(default_factory=list)


class CommitResult(BaseModel):
    """Atomic commit result for one batch."""

    batch_id: str
    committed_case_ids: list[str]
    pending_case_ids: list[str]
    created_document_count: int
    duplicate_document_count: int
    created_override_count: int = 0

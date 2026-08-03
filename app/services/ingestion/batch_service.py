from __future__ import annotations

import hashlib
import re
import shutil
from datetime import date, datetime
from pathlib import Path
from typing import Any, Literal

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import PROJECT_ROOT
from app.db.models import (
    Company,
    Document,
    DocumentFact,
    DocumentFieldOverride,
    IngestionBatch,
    PaymentObligation,
    Shipment,
    TradeCase,
)
from app.domain_inputs.providers import field_registry
from app.schemas.field_contract import DocumentType
from app.schemas.ingestion import (
    AnalyzedDocument,
    BatchAnalysis,
    BatchSummary,
    CaseProposal,
    CommitResult,
    DocumentIntelligenceResult,
    HumanRequest,
    ManualFieldOverride,
    MissingDocumentIssue,
)
from app.services.ingestion.fixed_extractors import extract_fixed_document
from app.services.ingestion.trade_case_matching import (
    CandidateScore,
    matching_verdict,
    score_bill_of_lading_to_case,
    score_invoice_to_booking,
    validate_matching_field_contract,
)
from app.services.payment_terms import parse_payment_terms

OBJECT_DIR = PROJECT_ROOT / "data" / "object_store" / "raw"
UPLOAD_ROOT = PROJECT_ROOT / "data" / "uploads"
SUPPORTED_UPLOAD_SUFFIXES = {".pdf", ".xlsx"}


def upload_batch_directory(batch_id: str) -> Path:
    """Return the isolated upload directory for one validated batch identifier."""
    normalized = batch_id.strip()
    if (
        not normalized
        or len(normalized) > 64
        or normalized in {".", ".."}
        or Path(normalized).name != normalized
    ):
        raise ValueError("batch_id must be a single non-empty path-safe identifier")
    return UPLOAD_ROOT / normalized


def uploaded_batch_paths(batch_id: str) -> list[Path]:
    """Resolve the supported files actually uploaded for a batch."""
    target = upload_batch_directory(batch_id)
    if not target.is_dir():
        raise FileNotFoundError(f"Uploaded batch not found: {batch_id}")
    paths = sorted(
        path
        for path in target.iterdir()
        if path.is_file() and path.suffix.lower() in SUPPORTED_UPLOAD_SUFFIXES
    )
    if not paths:
        raise ValueError(f"No supported files were uploaded for batch: {batch_id}")
    return paths


def uploaded_document_paths(batch_id: str) -> list[Path]:
    """Resolve a document-intelligence batch and reject financial workbooks."""
    paths = uploaded_batch_paths(batch_id)
    non_pdf = [path.name for path in paths if path.suffix.lower() != ".pdf"]
    if non_pdf:
        raise ValueError(
            "Trade document ingestion accepts PDF files only; register financial calendars "
            f"through Financial Calendar Agent: {non_pdf}"
        )
    return paths


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _stable_id(prefix: str, value: str) -> str:
    return f"{prefix}-{hashlib.sha256(value.encode()).hexdigest()[:16]}"


def _value(
    document: AnalyzedDocument,
    exact: str,
    overrides: list[ManualFieldOverride] | None = None,
) -> str | None:
    for override in reversed(overrides or []):
        if override.document_id == document.document_id and override.exact_standard_field == exact:
            return str(override.normalized_value).strip()
    if document.extraction is None:
        return None
    field = document.extraction.fields.get(exact)
    if field is None:
        return None
    return str(field.normalized_value).strip()


def _source_reference(document: AnalyzedDocument, key: str) -> str | None:
    if document.extraction is None:
        return None
    value = document.extraction.source_references.get(key)
    return value.strip() if value and value.strip() else None


def _effective_field_keys(
    document: AnalyzedDocument,
    overrides: list[ManualFieldOverride],
) -> set[str]:
    fields = set(document.extraction.fields) if document.extraction else set()
    fields.update(
        override.exact_standard_field
        for override in overrides
        if override.document_id == document.document_id and str(override.normalized_value).strip()
    )
    return fields


def _reference_case_id(reference: str) -> str:
    normalized = reference.strip().upper()
    if re.fullmatch(r"[A-Z0-9][A-Z0-9._-]{0,63}", normalized):
        return normalized
    return _stable_id("CASE", normalized)


def _booking_case_id(document: AnalyzedDocument) -> str:
    booking_no = _value(document, "booking_no") or document.document_id
    legacy = re.fullmatch(r"BK-([A-Z0-9]+)-\d{4}", booking_no.upper())
    if legacy:
        return f"CASE-{legacy.group(1)}"
    return _stable_id("CASE", f"BOOKING:{booking_no.upper()}")


class BatchService:
    """Stage, match, summarize, and atomically commit fixed-template trade PDFs."""

    def __init__(self, session: Session) -> None:
        self.session = session

    def classify_extract_validate(
        self,
        paths: list[Path],
    ) -> tuple[list[AnalyzedDocument], list[dict[str, Any]]]:
        """Document-intelligence phase: stage, classify, extract, and validate."""
        if not paths:
            raise ValueError("At least one file is required")
        non_pdf = [path.name for path in paths if path.suffix.lower() != ".pdf"]
        if non_pdf:
            raise ValueError(
                "Document Intelligence accepts PDF files only; use Financial Calendar Agent "
                f"for financial schedules: {non_pdf}"
            )
        OBJECT_DIR.mkdir(parents=True, exist_ok=True)
        documents: list[AnalyzedDocument] = []
        trace: list[dict[str, Any]] = []
        existing_hashes = set(self.session.scalars(select(Document.sha256)).all())
        for path in paths:
            digest = _sha256(path)
            suffix = path.suffix.lower()
            object_path = OBJECT_DIR / f"{digest}{suffix}"
            if not object_path.exists():
                shutil.copy2(path, object_path)
            extraction = extract_fixed_document(path)
            document = AnalyzedDocument(
                document_id=_stable_id("DOC", digest),
                file_name=path.name,
                sha256=digest,
                object_path=str(object_path),
                kind="PDF",
                extraction=extraction,
                duplicate_existing=digest in existing_hashes,
            )
            trace.extend(
                [
                    {
                        "name": "classify_fixed_template",
                        "document_id": document.document_id,
                        "status": extraction.classification.status,
                        "signals": extraction.classification.signals,
                        "model_calls": 0,
                    },
                    {
                        "name": "extract_fields",
                        "document_id": document.document_id,
                        "field_count": len(extraction.fields),
                        "missing_core_fields": extraction.missing_core_fields,
                        "model_calls": 0,
                    },
                ]
            )
            documents.append(document)
        return documents, trace

    def match_and_check_completeness(
        self,
        documents: list[AnalyzedDocument],
        batch_id: str,
        overrides: list[ManualFieldOverride] | None = None,
    ) -> tuple[BatchSummary, list[dict[str, Any]]]:
        """Trade-case phase: bundle arbitrary documents and evaluate completeness."""
        proposals, unmatched, type_confirmations, trace = self._match(
            documents,
            overrides,
        )
        self._merge_committed_reference_completeness(
            proposals,
            documents,
            overrides or [],
        )
        document_by_id = {document.document_id: document for document in documents}
        human_requests: list[HumanRequest] = []
        missing_document_issues: list[MissingDocumentIssue] = []
        for proposal in proposals:
            for field_path in proposal.missing_core_fields:
                doc_type_value, exact_standard_field = field_path.split(".", 1)
                doc_type = DocumentType(doc_type_value)
                document_id = next(
                    (
                        document_id
                        for document_id in proposal.document_ids
                        if document_by_id[document_id].doc_type is doc_type
                    ),
                    None,
                )
                if document_id is None:
                    continue
                human_requests.append(
                    HumanRequest(
                        request_id=_stable_id(
                            "HREQ",
                            f"{batch_id}:{proposal.case_id}:{field_path}",
                        ),
                        case_id=proposal.case_id,
                        document_id=document_id,
                        doc_type=doc_type,
                        exact_standard_field=exact_standard_field,
                        field_path=field_path,
                        prompt=(
                            f"Please provide a verified value for {field_path}; "
                            "the source document left it blank."
                        ),
                    )
                )
            for doc_type in proposal.missing_documents:
                missing_document_issues.append(
                    MissingDocumentIssue(
                        issue_id=_stable_id(
                            "MISS",
                            f"{batch_id}:{proposal.case_id}:{doc_type.value}",
                        ),
                        case_id=proposal.case_id,
                        doc_type=doc_type,
                        message=(
                            f"{doc_type.value} has not been uploaded; "
                            "the transaction shell remains available."
                        ),
                    )
                )
        summary = BatchSummary(
            batch_id=batch_id,
            file_count=len(documents),
            case_count=len(proposals),
            cases=proposals,
            unmatched_document_ids=unmatched,
            type_confirmation_document_ids=type_confirmations,
            human_requests=human_requests,
            missing_document_issues=missing_document_issues,
            confirmation_pending=sum(
                proposal.status in {"AWAITING_FIELD_INPUT", "CONFIRMATION_REQUIRED"}
                for proposal in proposals
            ),
            duplicate_document_count=sum(doc.duplicate_existing for doc in documents),
        )
        return summary, trace

    def _merge_committed_reference_completeness(
        self,
        proposals: list[CaseProposal],
        staged_documents: list[AnalyzedDocument],
        overrides: list[ManualFieldOverride],
    ) -> None:
        """Merge earlier committed facts so a later upload can complete a case."""
        staged_by_id = {document.document_id: document for document in staged_documents}
        referenced_case_ids = {
            proposal.case_id
            for proposal in proposals
            if any(
                _source_reference(staged_by_id[document_id], "transaction_reference")
                for document_id in proposal.document_ids
                if document_id in staged_by_id and staged_by_id[document_id].kind == "PDF"
            )
        }
        if not referenced_case_ids:
            return
        committed_documents = list(
            self.session.scalars(
                select(Document).where(Document.case_id.in_(referenced_case_ids))
            ).all()
        )
        committed_by_case: dict[str, list[Document]] = {}
        for document in committed_documents:
            if document.case_id:
                committed_by_case.setdefault(document.case_id, []).append(document)
        committed_document_ids = [document.document_id for document in committed_documents]
        committed_facts = (
            list(
                self.session.scalars(
                    select(DocumentFact).where(DocumentFact.document_id.in_(committed_document_ids))
                ).all()
            )
            if committed_document_ids
            else []
        )
        facts_by_document: dict[str, set[str]] = {}
        for fact in committed_facts:
            if fact.normalized_json.get("value") not in (None, ""):
                facts_by_document.setdefault(fact.document_id, set()).add(fact.exact_standard_field)
        committed_overrides = (
            list(
                self.session.scalars(
                    select(DocumentFieldOverride).where(
                        DocumentFieldOverride.document_id.in_(committed_document_ids)
                    )
                ).all()
            )
            if committed_document_ids
            else []
        )
        overrides_by_document: dict[str, set[str]] = {}
        for override in committed_overrides:
            value = override.normalized_json.get("value")
            if value not in (None, ""):
                overrides_by_document.setdefault(override.document_id, set()).add(
                    override.exact_standard_field
                )

        registry = field_registry()
        for proposal in proposals:
            if proposal.case_id not in referenced_case_ids:
                continue
            fields_by_type: dict[DocumentType, set[str]] = {}
            for document_id in proposal.document_ids:
                staged = staged_by_id.get(document_id)
                if staged is None or staged.doc_type is None:
                    continue
                fields_by_type.setdefault(staged.doc_type, set()).update(
                    _effective_field_keys(staged, overrides)
                )
            for committed in committed_by_case.get(proposal.case_id, []):
                try:
                    doc_type = DocumentType(committed.doc_type)
                except ValueError:
                    continue
                fields_by_type.setdefault(doc_type, set()).update(
                    facts_by_document.get(committed.document_id, set())
                )
                fields_by_type[doc_type].update(
                    overrides_by_document.get(committed.document_id, set())
                )

            proposal.missing_documents = [
                doc_type for doc_type in DocumentType if doc_type not in fields_by_type
            ]
            proposal.missing_core_fields = sorted(
                f"{doc_type.value}.{field}"
                for doc_type, fields in fields_by_type.items()
                for field in registry.core_keys(doc_type) - fields
            )
            proposal.status = (
                "AWAITING_FIELD_INPUT"
                if proposal.missing_core_fields
                else "AWAITING_DOCUMENT"
                if proposal.missing_documents
                else "READY"
            )

    def analyze(self, paths: list[Path], batch_id: str) -> BatchAnalysis:
        """Run both public phases without committing projected domain facts."""
        self.stage_document_intelligence(paths, batch_id)
        return self.bundle_staged_batch(batch_id)

    def stage_document_intelligence(
        self,
        paths: list[Path],
        batch_id: str,
    ) -> DocumentIntelligenceResult:
        """Persist the document-intelligence phase as a graph-safe handoff envelope."""
        documents, trace = self.classify_extract_validate(paths)
        result = DocumentIntelligenceResult(
            batch_id=batch_id,
            documents=documents,
            trace=trace,
        )
        batch = self.session.get(IngestionBatch, batch_id)
        if batch is None:
            batch = IngestionBatch(batch_id=batch_id)
            self.session.add(batch)
        batch.status = "DOCUMENTS_ANALYZED"
        batch.file_count = len(documents)
        batch.analysis_json = result.model_dump(mode="json")
        self.session.flush()
        return result

    def bundle_staged_batch(self, batch_id: str) -> BatchAnalysis:
        """Load the persisted phase-1 envelope, bundle it, and persist completeness."""
        batch = self.session.get(IngestionBatch, batch_id)
        if batch is None or not batch.analysis_json:
            raise KeyError(batch_id)
        payload = batch.analysis_json
        if "summary" in payload:
            previous = BatchAnalysis.model_validate(payload)
            documents = previous.documents
            overrides = previous.manual_overrides
            trace = previous.trace
        else:
            staged = DocumentIntelligenceResult.model_validate(payload)
            documents = staged.documents
            overrides = []
            trace = staged.trace
        summary, match_trace = self.match_and_check_completeness(
            documents,
            batch_id,
            overrides,
        )
        analysis = BatchAnalysis(
            batch_id=batch_id,
            documents=documents,
            summary=summary,
            manual_overrides=overrides,
            trace=[*trace, *match_trace],
        )
        batch.status = "ANALYZED"
        batch.file_count = len(documents)
        batch.analysis_json = analysis.model_dump(mode="json")
        self.session.flush()
        return analysis

    def _match(
        self,
        documents: list[AnalyzedDocument],
        overrides: list[ManualFieldOverride] | None = None,
    ) -> tuple[list[CaseProposal], list[str], list[str], list[dict[str, Any]]]:
        overrides = overrides or []
        validate_matching_field_contract(field_registry())

        def read_value(document: AnalyzedDocument, field: str) -> str | None:
            return _value(document, field, overrides)

        auto_documents = [
            document
            for document in documents
            if document.extraction is not None
            and document.extraction.classification.status == "AUTO_CONFIRMED"
        ]
        clusters: list[dict[str, Any]] = []
        assigned: set[str] = set()
        trace: list[dict[str, Any]] = []

        referenced_clusters: dict[str, dict[str, Any]] = {}
        slot_by_type = {
            DocumentType.BOOKING_CONFIRMATION: "booking",
            DocumentType.COMMERCIAL_INVOICE: "invoice",
            DocumentType.BILL_OF_LADING: "bl",
        }
        for document in sorted(auto_documents, key=lambda item: item.document_id):
            reference = _source_reference(document, "transaction_reference")
            if reference is None:
                continue
            normalized_reference = reference.upper()
            cluster = referenced_clusters.setdefault(
                normalized_reference,
                {
                    "case_id": _reference_case_id(reference),
                    "booking": None,
                    "invoice": None,
                    "bl": None,
                    "scores": {},
                },
            )
            slot = slot_by_type[document.doc_type]
            if cluster[slot] is not None:
                trace.append(
                    {
                        "name": "bundle_by_transaction_reference",
                        "document_id": document.document_id,
                        "transaction_reference": reference,
                        "verdict": "DUPLICATE_DOCUMENT_TYPE",
                    }
                )
                continue
            cluster[slot] = document
            cluster["scores"][document.document_id] = 100.0
            assigned.add(document.document_id)
            trace.append(
                {
                    "name": "bundle_by_transaction_reference",
                    "document_id": document.document_id,
                    "transaction_reference": reference,
                    "case_id": cluster["case_id"],
                    "verdict": "EXACT",
                }
            )
        clusters.extend(referenced_clusters.values())

        bookings = sorted(
            [
                document
                for document in auto_documents
                if document.document_id not in assigned
                and document.doc_type is DocumentType.BOOKING_CONFIRMATION
            ],
            key=lambda document: _value(document, "booking_no", overrides) or document.document_id,
        )
        legacy_clusters: list[dict[str, Any]] = []
        used_case_ids = {cluster["case_id"] for cluster in clusters}
        for booking in bookings:
            case_id = _booking_case_id(booking)
            if case_id in used_case_ids:
                case_id = _stable_id("CASE", booking.document_id)
            used_case_ids.add(case_id)
            legacy_clusters.append(
                {
                    "case_id": case_id,
                    "booking": booking,
                    "invoice": None,
                    "bl": None,
                    "scores": {},
                }
            )
            assigned.add(booking.document_id)

        invoices = [
            doc
            for doc in auto_documents
            if doc.document_id not in assigned
            if doc.doc_type is DocumentType.COMMERCIAL_INVOICE
        ]
        for invoice in invoices:
            scored: list[tuple[float, dict[str, Any], CandidateScore]] = []
            for cluster in legacy_clusters:
                booking = cluster["booking"]
                candidate = score_invoice_to_booking(invoice, booking, read_value)
                scored.append((candidate.score, cluster, candidate))
            if not scored:
                continue
            scored.sort(key=lambda item: item[0], reverse=True)
            top_score, top, top_candidate = scored[0]
            margin = top_score - scored[1][0] if len(scored) > 1 else top_score
            verdict = matching_verdict(top_score, margin)
            trace.append(
                {
                    "name": "score_case_candidates",
                    "strategy": "BUSINESS_FIELD_SCORE",
                    "document_id": invoice.document_id,
                    "document_type": DocumentType.COMMERCIAL_INVOICE.value,
                    "top_case_id": top["case_id"],
                    "score": top_score,
                    "margin": margin,
                    "verdict": verdict,
                    "relations": top_candidate.as_trace(),
                }
            )
            if verdict == "HIGH" and top["invoice"] is None:
                top["invoice"] = invoice
                top["scores"][invoice.document_id] = top_score
                assigned.add(invoice.document_id)

        if not legacy_clusters and not referenced_clusters:
            for invoice in invoices:
                if invoice.document_id in assigned:
                    continue
                legacy_clusters.append(
                    {
                        "case_id": _stable_id(
                            "CASE",
                            f"INVOICE:{_value(invoice, 'invoice No.', overrides) or invoice.document_id}",
                        ),
                        "booking": None,
                        "invoice": invoice,
                        "bl": None,
                        "scores": {invoice.document_id: 100.0},
                    }
                )
                assigned.add(invoice.document_id)

        bl_docs = [
            doc
            for doc in auto_documents
            if doc.document_id not in assigned
            if doc.doc_type is DocumentType.BILL_OF_LADING
        ]
        for bl_doc in bl_docs:
            scored: list[tuple[float, dict[str, Any], CandidateScore]] = []
            for cluster in legacy_clusters:
                booking = cluster["booking"]
                invoice = cluster["invoice"]
                candidate = score_bill_of_lading_to_case(
                    bl_doc,
                    booking,
                    invoice,
                    read_value,
                )
                scored.append((candidate.score, cluster, candidate))
            if not scored:
                continue
            scored.sort(key=lambda item: item[0], reverse=True)
            top_score, top, top_candidate = scored[0]
            margin = top_score - scored[1][0] if len(scored) > 1 else top_score
            verdict = matching_verdict(top_score, margin)
            trace.append(
                {
                    "name": "score_case_candidates",
                    "strategy": "BUSINESS_FIELD_SCORE",
                    "document_id": bl_doc.document_id,
                    "document_type": DocumentType.BILL_OF_LADING.value,
                    "top_case_id": top["case_id"],
                    "score": top_score,
                    "margin": margin,
                    "verdict": verdict,
                    "relations": top_candidate.as_trace(),
                }
            )
            if verdict == "HIGH" and top["bl"] is None:
                top["bl"] = bl_doc
                top["scores"][bl_doc.document_id] = top_score
                assigned.add(bl_doc.document_id)

        if not legacy_clusters and not referenced_clusters:
            for bl_doc in bl_docs:
                if bl_doc.document_id in assigned:
                    continue
                legacy_clusters.append(
                    {
                        "case_id": _stable_id(
                            "CASE",
                            f"BL:{_value(bl_doc, 'bl_no', overrides) or bl_doc.document_id}",
                        ),
                        "booking": None,
                        "invoice": None,
                        "bl": bl_doc,
                        "scores": {bl_doc.document_id: 100.0},
                    }
                )
                assigned.add(bl_doc.document_id)

        clusters.extend(legacy_clusters)
        proposals: list[CaseProposal] = []
        registry = field_registry()
        for cluster in clusters:
            booking = cluster["booking"]
            invoice = cluster["invoice"]
            bl_doc = cluster["bl"]
            missing_documents: list[DocumentType] = []
            if booking is None:
                missing_documents.append(DocumentType.BOOKING_CONFIRMATION)
            if invoice is None:
                missing_documents.append(DocumentType.COMMERCIAL_INVOICE)
            if bl_doc is None:
                missing_documents.append(DocumentType.BILL_OF_LADING)
            missing_core: list[str] = []
            for document in [booking, invoice, bl_doc]:
                if document and document.extraction:
                    missing_core.extend(
                        f"{document.doc_type.value}.{field}"
                        for field in sorted(
                            registry.core_keys(document.doc_type)
                            - _effective_field_keys(document, overrides)
                        )
                    )
            status: Literal[
                "READY",
                "AWAITING_FIELD_INPUT",
                "AWAITING_DOCUMENT",
                "CONFIRMATION_REQUIRED",
                "DRAFT",
            ] = (
                "AWAITING_FIELD_INPUT"
                if missing_core
                else "AWAITING_DOCUMENT"
                if missing_documents
                else "READY"
            )
            all_documents = [document for document in [booking] if document is not None]
            all_documents.extend(doc for doc in [invoice, bl_doc] if doc is not None)
            proposals.append(
                CaseProposal(
                    case_id=cluster["case_id"],
                    document_ids=[doc.document_id for doc in all_documents],
                    booking_document_id=booking.document_id if booking else None,
                    invoice_document_id=invoice.document_id if invoice else None,
                    bl_document_id=bl_doc.document_id if bl_doc else None,
                    match_scores=cluster["scores"],
                    missing_documents=missing_documents,
                    missing_core_fields=sorted(set(missing_core)),
                    status=status,
                )
            )
        type_confirmations = [
            doc.document_id
            for doc in documents
            if doc.extraction and doc.extraction.classification.status == "CONFIRM_REQUIRED"
        ]
        unmatched = [
            doc.document_id
            for doc in documents
            if doc.document_id not in assigned and doc.document_id not in type_confirmations
        ]
        proposals.sort(key=lambda proposal: proposal.case_id)
        return proposals, unmatched, type_confirmations, trace

    def apply_document_field_override(
        self,
        batch_id: str,
        document_id: str,
        exact_standard_field: str,
        value: Any,
        *,
        actor: str,
        reason: str | None = None,
    ) -> BatchAnalysis:
        """Append a human correction and recompute completeness without mutating extraction."""
        batch = self.session.get(IngestionBatch, batch_id)
        if batch is None or not batch.analysis_json:
            raise KeyError(batch_id)
        analysis = BatchAnalysis.model_validate(batch.analysis_json)
        document = next(
            (candidate for candidate in analysis.documents if candidate.document_id == document_id),
            None,
        )
        if document is None or document.doc_type is None:
            raise KeyError(document_id)
        if document_id not in {
            item for proposal in analysis.summary.cases for item in proposal.document_ids
        }:
            raise ValueError("Manual overrides require a document assigned to a case")
        registry_entry = field_registry().by_exact(document.doc_type, exact_standard_field)
        if not registry_entry.core_blocking_candidate:
            raise ValueError("Manual overrides are limited to the 22 core document fields")
        raw_input = str(value).strip()
        normalized_actor = actor.strip()
        if not raw_input:
            raise ValueError("Manual override value must be non-empty")
        if not normalized_actor:
            raise ValueError("Manual override actor must be non-empty")
        normalized_value = _normalize_manual_override(
            exact_standard_field,
            raw_input,
        )
        sequence = len(analysis.manual_overrides) + 1
        override = ManualFieldOverride(
            override_id=_stable_id(
                "OVR",
                (
                    f"{batch_id}:{document_id}:{exact_standard_field}:"
                    f"{sequence}:{raw_input}:{normalized_actor}"
                ),
            ),
            document_id=document_id,
            exact_standard_field=exact_standard_field,
            raw_input=raw_input,
            normalized_value=normalized_value,
            actor=normalized_actor,
            reason=reason.strip() if reason and reason.strip() else None,
            sequence=sequence,
        )
        analysis.manual_overrides.append(override)
        summary, _ = self.match_and_check_completeness(
            analysis.documents,
            batch_id,
            analysis.manual_overrides,
        )
        summary.domain_commit_completed = analysis.summary.domain_commit_completed
        analysis.summary = summary
        analysis.trace.append(
            {
                "name": "apply_manual_field_override",
                "override_id": override.override_id,
                "document_id": document_id,
                "exact_standard_field": exact_standard_field,
                "evidence_source": "MANUAL_OVERRIDE",
            }
        )
        batch.status = "ANALYZED"
        batch.analysis_json = analysis.model_dump(mode="json")
        self.session.flush()
        return analysis

    def apply_manual_override(
        self,
        batch_id: str,
        document_id: str,
        exact_standard_field: str,
        value: Any,
        *,
        actor: str,
        reason: str | None = None,
    ) -> BatchAnalysis:
        """Backward-compatible alias for the explicit trade-case manager operation."""
        return self.apply_document_field_override(
            batch_id,
            document_id,
            exact_standard_field,
            value,
            actor=actor,
            reason=reason,
        )

    def commit(self, batch_id: str, confirmed_case_ids: set[str] | None = None) -> CommitResult:
        """Upsert transaction shells, immutable facts, overrides, and projections."""
        confirmed_case_ids = confirmed_case_ids or set()
        batch = self.session.get(IngestionBatch, batch_id)
        if batch is None or not batch.analysis_json:
            raise KeyError(batch_id)
        analysis = BatchAnalysis.model_validate(batch.analysis_json)
        documents = {doc.document_id: doc for doc in analysis.documents}
        committed: list[str] = []
        pending: list[str] = []
        created_document_count = 0
        duplicate_document_count = 0
        created_override_count = 0
        for proposal in analysis.summary.cases:
            if (
                proposal.status == "CONFIRMATION_REQUIRED"
                and proposal.case_id not in confirmed_case_ids
            ):
                pending.append(proposal.case_id)
                continue
            booking = (
                documents[proposal.booking_document_id] if proposal.booking_document_id else None
            )
            invoice = (
                documents[proposal.invoice_document_id] if proposal.invoice_document_id else None
            )
            bl_doc = documents[proposal.bl_document_id] if proposal.bl_document_id else None
            case_documents = [
                document for document in [booking, invoice, bl_doc] if document is not None
            ]
            company_name = (
                (_value(booking, "shipper", analysis.manual_overrides) if booking else None)
                or (_value(invoice, "seller", analysis.manual_overrides) if invoice else None)
                or (_value(bl_doc, "shipper", analysis.manual_overrides) if bl_doc else None)
                or "UNKNOWN"
            )
            company_id = next(
                (
                    source_company_id
                    for document in case_documents
                    if (
                        source_company_id := _source_reference(
                            document,
                            "company_id",
                        )
                    )
                ),
                None,
            ) or _stable_id(
                "COMPANY",
                " ".join(company_name.upper().split()),
            )
            transaction_id = (
                next(
                    (
                        source_transaction_id
                        for document in case_documents
                        if (
                            source_transaction_id := _source_reference(
                                document,
                                "finance_transaction_id",
                            )
                        )
                    ),
                    None,
                )
                or proposal.case_id
            )
            company = self.session.get(Company, company_id)
            if company is None:
                company = Company(
                    company_id=company_id,
                    legal_name=company_name,
                )
                self.session.add(company)
                self.session.flush()
            case_status = {
                "READY": "MONITORING_READY",
                "AWAITING_FIELD_INPUT": "AWAITING_FIELD_INPUT",
                "AWAITING_DOCUMENT": "AWAITING_DOCUMENT",
                "DRAFT": "DRAFT",
                "CONFIRMATION_REQUIRED": "DRAFT",
            }[proposal.status]
            counterparty = (
                _value(invoice, "buyer", analysis.manual_overrides) if invoice else None
            ) or (_value(bl_doc, "consignee", analysis.manual_overrides) if bl_doc else None)
            invoice_no = (
                _value(invoice, "invoice No.", analysis.manual_overrides) if invoice else None
            )
            currency = _value(invoice, "currency", analysis.manual_overrides) if invoice else None
            goods = (
                _value(invoice, "description_of_goods", analysis.manual_overrides)
                if invoice
                else None
            ) or (_value(booking, "commodity", analysis.manual_overrides) if booking else None)
            trade_case = self.session.get(TradeCase, proposal.case_id)
            if trade_case is None:
                trade_case = TradeCase(
                    case_id=proposal.case_id,
                    company_id=company_id,
                    transaction_id=transaction_id,
                    company=company_name,
                    counterparty=counterparty or "UNKNOWN",
                    invoice_no=invoice_no,
                    currency=currency,
                    goods=goods,
                    status=case_status,
                    monitoring_enabled=proposal.status == "READY",
                    basis_version=f"{batch_id}:{proposal.case_id}:v1",
                )
                self.session.add(trade_case)
                self.session.flush()
            else:
                trade_case.company_id = company_id
                trade_case.transaction_id = transaction_id
                if company_name != "UNKNOWN":
                    trade_case.company = company_name
                if counterparty:
                    trade_case.counterparty = counterparty
                if invoice_no:
                    trade_case.invoice_no = invoice_no
                if currency:
                    trade_case.currency = currency
                if goods:
                    trade_case.goods = goods
                if proposal.status == "READY" or trade_case.status != "MONITORING_READY":
                    trade_case.status = case_status
                    trade_case.monitoring_enabled = proposal.status == "READY"

            booking_no = (
                _value(booking, "booking_no", analysis.manual_overrides) if booking else None
            )
            bl_no = _value(bl_doc, "bl_no", analysis.manual_overrides) if bl_doc else None
            planned_vessel_name = (
                _value(booking, "vessel_name", analysis.manual_overrides) if booking else None
            )
            planned_voyage_no = (
                _value(booking, "voyage_no", analysis.manual_overrides) if booking else None
            )
            actual_vessel_name = (
                _value(bl_doc, "vessel_name", analysis.manual_overrides) if bl_doc else None
            )
            actual_voyage_no = (
                _value(bl_doc, "voyage_no", analysis.manual_overrides) if bl_doc else None
            )
            booking_port_of_loading = (
                _value(booking, "port_of_loading", analysis.manual_overrides) if booking else None
            )
            booking_port_of_discharge = (
                _value(booking, "port_of_discharge", analysis.manual_overrides) if booking else None
            )
            bl_port_of_loading = (
                _value(bl_doc, "port_of_loading", analysis.manual_overrides) if bl_doc else None
            )
            bl_port_of_discharge = (
                _value(bl_doc, "port_of_discharge", analysis.manual_overrides) if bl_doc else None
            )
            # The minimal Shipment schema has one shared route. Prefer the Booking
            # plan and use B/L ports only when no Booking route has been projected.
            port_of_loading = booking_port_of_loading or bl_port_of_loading
            port_of_discharge = booking_port_of_discharge or bl_port_of_discharge
            etd = (
                _parse_date(_value(booking, "etd", analysis.manual_overrides)) if booking else None
            )
            on_board_date = (
                _parse_date(_value(bl_doc, "on_board_date", analysis.manual_overrides))
                if bl_doc
                else None
            )
            shipment = self.session.get(Shipment, f"SHIP-{proposal.case_id}")
            bl_is_missing = DocumentType.BILL_OF_LADING in proposal.missing_documents
            bl_has_missing_core = any(
                field.startswith(f"{DocumentType.BILL_OF_LADING.value}.")
                for field in proposal.missing_core_fields
            )
            shipment_status = (
                "AWAITING_BILL_OF_LADING"
                if bl_is_missing
                else "BL_RECEIVED_FIELD_PENDING"
                if bl_has_missing_core
                else "BL_RECEIVED"
            )
            if shipment is None and booking is None and bl_doc is None:
                # An Invoice-only TradeCase has no authoritative Shipment projection.
                # read_shipment_snapshot represents that absence explicitly.
                pass
            elif shipment is None:
                shipment = Shipment(
                    shipment_id=f"SHIP-{proposal.case_id}",
                    case_id=proposal.case_id,
                    booking_no=booking_no,
                    bl_no=bl_no,
                    planned_vessel_name=planned_vessel_name,
                    planned_voyage_no=planned_voyage_no,
                    actual_vessel_name=actual_vessel_name,
                    actual_voyage_no=actual_voyage_no,
                    port_of_loading=port_of_loading,
                    port_of_discharge=port_of_discharge,
                    etd=etd,
                    on_board_date=on_board_date,
                    status=shipment_status,
                    next_check_at=etd,
                )
                self.session.add(shipment)
            else:
                before_projection = {
                    attribute: getattr(shipment, attribute)
                    for attribute in (
                        "booking_no",
                        "bl_no",
                        "planned_vessel_name",
                        "planned_voyage_no",
                        "actual_vessel_name",
                        "actual_voyage_no",
                        "port_of_loading",
                        "port_of_discharge",
                        "etd",
                        "on_board_date",
                        "status",
                        "next_check_at",
                    )
                }
                for attribute, projected_value in {
                    "booking_no": booking_no,
                    "bl_no": bl_no,
                    "planned_vessel_name": planned_vessel_name,
                    "planned_voyage_no": planned_voyage_no,
                    "actual_vessel_name": actual_vessel_name,
                    "actual_voyage_no": actual_voyage_no,
                    "etd": etd,
                    "on_board_date": on_board_date,
                    "next_check_at": etd,
                }.items():
                    if projected_value is not None:
                        setattr(shipment, attribute, projected_value)
                if booking_port_of_loading is not None:
                    shipment.port_of_loading = booking_port_of_loading
                elif shipment.port_of_loading is None and bl_port_of_loading is not None:
                    shipment.port_of_loading = bl_port_of_loading
                if booking_port_of_discharge is not None:
                    shipment.port_of_discharge = booking_port_of_discharge
                elif shipment.port_of_discharge is None and bl_port_of_discharge is not None:
                    shipment.port_of_discharge = bl_port_of_discharge
                shipment.status = shipment_status
                after_projection = {
                    attribute: getattr(shipment, attribute) for attribute in before_projection
                }
                if after_projection != before_projection:
                    shipment.state_version += 1

            for doc_id in proposal.document_ids:
                staged = documents[doc_id]
                existing = self.session.scalar(
                    select(Document).where(Document.sha256 == staged.sha256)
                )
                if existing is not None:
                    duplicate_document_count += 1
                    continue
                extraction = staged.extraction
                if extraction is None or extraction.classification.doc_type is None:
                    continue
                registry = field_registry()
                core_keys = registry.core_keys(extraction.classification.doc_type)
                document = Document(
                    document_id=staged.document_id,
                    batch_id=batch_id,
                    case_id=proposal.case_id,
                    doc_type=extraction.classification.doc_type.value,
                    template_id=extraction.classification.template_id or "",
                    sha256=staged.sha256,
                    object_path=staged.object_path,
                    classification_status=extraction.classification.status,
                    source_references_json=extraction.source_references,
                )
                self.session.add(document)
                for exact_standard_field, field in extraction.fields.items():
                    if exact_standard_field not in core_keys:
                        continue
                    self.session.add(
                        DocumentFact(
                            document_id=staged.document_id,
                            exact_standard_field=field.exact_standard_field,
                            raw_value=field.raw_value,
                            normalized_json={"value": field.normalized_value},
                        )
                    )
                for exact_standard_field in extraction.missing_core_fields:
                    self.session.add(
                        DocumentFact(
                            document_id=staged.document_id,
                            exact_standard_field=exact_standard_field,
                            raw_value="",
                            normalized_json={"value": None},
                            evidence_source="DOCUMENT_MISSING",
                        )
                    )
                created_document_count += 1
            self.session.flush()

            proposal_document_ids = set(proposal.document_ids)
            effective_history: dict[tuple[str, str], Any] = {}
            for staged in case_documents:
                if staged.extraction is None:
                    continue
                for exact, field in staged.extraction.fields.items():
                    effective_history[(staged.document_id, exact)] = field.normalized_value
            for override in sorted(
                (
                    item
                    for item in analysis.manual_overrides
                    if item.document_id in proposal_document_ids
                ),
                key=lambda item: item.sequence,
            ):
                key = (override.document_id, override.exact_standard_field)
                previous = effective_history.get(key)
                if self.session.get(DocumentFieldOverride, override.override_id) is None:
                    self.session.add(
                        DocumentFieldOverride(
                            override_id=override.override_id,
                            document_id=override.document_id,
                            exact_standard_field=override.exact_standard_field,
                            raw_input=override.raw_input,
                            normalized_json={"value": override.normalized_value},
                            previous_normalized_json=(
                                {"value": previous} if previous is not None else None
                            ),
                            actor=override.actor,
                            reason=override.reason,
                        )
                    )
                    created_override_count += 1
                effective_history[key] = override.normalized_value
            if invoice:
                raw_terms = _value(
                    invoice,
                    "payment_terms",
                    analysis.manual_overrides,
                )
                if raw_terms:
                    for obligation in parse_payment_terms(raw_terms):
                        obligation_id = f"PO-{proposal.case_id}-{obligation.tranche_index}"
                        if self.session.get(PaymentObligation, obligation_id) is None:
                            self.session.add(
                                PaymentObligation(
                                    obligation_id=obligation_id,
                                    case_id=proposal.case_id,
                                    tranche_index=obligation.tranche_index,
                                    raw_text=obligation.raw_text,
                                    payment_method=obligation.payment_method,
                                    credit_term_type=obligation.credit_term_type,
                                    lc_type=obligation.lc_type,
                                    anchor_type_extracted=obligation.anchor_type_extracted,
                                    anchor_type_effective=obligation.anchor_type_effective,
                                    tenor_days=obligation.tenor_days,
                                    day_type_extracted=obligation.day_type_extracted,
                                    day_type_effective=obligation.day_type_effective,
                                    payment_ratio=obligation.payment_ratio,
                                    advance_or_deferred=obligation.advance_or_deferred,
                                    calculation_allowed=obligation.calculation_allowed,
                                    verified=obligation.verified,
                                )
                            )
            committed.append(proposal.case_id)
        batch.status = "COMMITTED" if not pending else "PARTIAL_COMMIT"
        analysis.summary.domain_commit_completed = len(committed)
        analysis.summary.confirmation_pending = len(pending)
        batch.analysis_json = analysis.model_dump(mode="json")
        self.session.flush()
        return CommitResult(
            batch_id=batch_id,
            committed_case_ids=committed,
            pending_case_ids=pending,
            created_document_count=created_document_count,
            duplicate_document_count=duplicate_document_count,
            created_override_count=created_override_count,
        )


DATE_OVERRIDE_FIELDS = {
    "etd",
    "on_board_date",
}


def _normalize_manual_override(exact_standard_field: str, raw_input: str) -> str:
    if exact_standard_field not in DATE_OVERRIDE_FIELDS:
        return raw_input
    cleaned = raw_input.replace(".", " ").replace(",", " ")
    for fmt in (
        "%Y-%m-%d",
        "%d%b%y",
        "%b %d %Y",
        "%d %b %Y",
    ):
        try:
            return datetime.strptime(" ".join(cleaned.split()), fmt).date().isoformat()
        except ValueError:
            continue
    raise ValueError(f"Manual override for {exact_standard_field} must be a recognized date")


def _parse_date(value: str | None) -> date | None:
    if not value:
        return None
    try:
        return date.fromisoformat(value)
    except ValueError:
        return None

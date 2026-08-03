from __future__ import annotations

from pathlib import Path

import pytest
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session

from app.core.config import PROJECT_ROOT
from app.db.models import Base, Document, DocumentFact, TradeCase
from app.domain_inputs.providers import field_registry
from app.schemas.field_contract import DocumentType
from app.services.ingestion import batch_service
from app.services.ingestion.batch_service import (
    BatchService,
    upload_batch_directory,
    uploaded_batch_paths,
    uploaded_document_paths,
)


def _paths() -> list[Path]:
    return sorted((PROJECT_ROOT / "data" / "fixtures" / "live_trade_documents").glob("*.pdf"))


def test_upload_paths_keep_financial_calendar_separate_from_document_batch(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    upload_root = tmp_path / "uploads"
    monkeypatch.setattr(batch_service, "UPLOAD_ROOT", upload_root)
    target = upload_batch_directory("CUSTOMER-BATCH-7")
    target.mkdir(parents=True)
    (target / "booking.pdf").write_bytes(b"pdf")
    calendar_path = target / "financial-calendar.xlsx"
    calendar_path.write_bytes(b"xlsx")
    (target / "notes.txt").write_text("ignored", encoding="utf-8")

    assert [path.name for path in uploaded_batch_paths("CUSTOMER-BATCH-7")] == [
        "booking.pdf",
        "financial-calendar.xlsx",
    ]
    with pytest.raises(ValueError, match="PDF files only"):
        uploaded_document_paths("CUSTOMER-BATCH-7")

    calendar_path.unlink()
    assert [path.name for path in uploaded_document_paths("CUSTOMER-BATCH-7")] == ["booking.pdf"]


def test_document_intelligence_rejects_financial_workbook(tmp_path: Path) -> None:
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    workbook_path = tmp_path / "financial-calendar.xlsx"
    workbook_path.write_bytes(b"xlsx")

    with (
        Session(engine) as session,
        pytest.raises(ValueError, match="Financial Calendar Agent"),
    ):
        BatchService(session).classify_extract_validate([workbook_path])


def test_uploaded_batch_paths_rejects_missing_or_unsafe_batch(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(batch_service, "UPLOAD_ROOT", tmp_path)

    with pytest.raises(FileNotFoundError, match="missing"):
        uploaded_batch_paths("missing")

    for unsafe in ("", "../escape", "nested/batch"):
        with pytest.raises(ValueError, match="path-safe"):
            upload_batch_directory(unsafe)


def test_live_batch_summary_and_idempotent_commit() -> None:
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        service = BatchService(session)
        analysis = service.analyze(_paths(), "BATCH-LIVE-8")
        summary = analysis.summary
        assert summary.file_count == 8
        assert summary.case_count == 3
        assert summary.unmatched_document_ids == []
        assert summary.type_confirmation_document_ids == []
        assert [case.case_id for case in summary.cases] == [
            "TRD-001",
            "TRD-002",
            "TRD-003",
        ]
        ready, missing_field, missing_document = summary.cases
        assert ready.status == "READY"
        assert ready.missing_documents == []
        assert ready.missing_core_fields == []
        assert missing_field.status == "AWAITING_FIELD_INPUT"
        assert missing_field.missing_documents == []
        assert missing_field.missing_core_fields == ["BILL_OF_LADING.on_board_date"]
        assert missing_document.status == "AWAITING_DOCUMENT"
        assert missing_document.missing_documents == ["BILL_OF_LADING"]
        assert missing_document.missing_core_fields == []
        assert ready.booking_document_id is not None
        with pytest.raises(ValueError, match="22 core"):
            service.apply_document_field_override(
                "BATCH-LIVE-8",
                ready.booking_document_id,
                "remarks",
                "optional note",
                actor="test-user",
            )

        committed = service.commit("BATCH-LIVE-8")
        assert committed.committed_case_ids == [
            "TRD-001",
            "TRD-002",
            "TRD-003",
        ]
        assert committed.pending_case_ids == []
        assert session.get(TradeCase, "TRD-001").status == "MONITORING_READY"
        assert session.get(TradeCase, "TRD-002").status == "AWAITING_FIELD_INPUT"
        assert session.get(TradeCase, "TRD-003").status == "AWAITING_DOCUMENT"
        assert session.scalar(select(func.count()).select_from(DocumentFact)) == 58
        registry = field_registry()
        for document in session.scalars(select(Document)).all():
            persisted_keys = set(
                session.scalars(
                    select(DocumentFact.exact_standard_field).where(
                        DocumentFact.document_id == document.document_id
                    )
                ).all()
            )
            assert persisted_keys == registry.core_keys(DocumentType(document.doc_type))
        session.commit()

        duplicate_analysis = service.analyze(_paths(), "BATCH-LIVE-8-REUPLOAD")
        assert duplicate_analysis.summary.duplicate_document_count == 8
        duplicate_commit = service.commit("BATCH-LIVE-8-REUPLOAD")
        assert duplicate_commit.duplicate_document_count == 8
        assert duplicate_commit.created_document_count == 0
        assert session.scalar(select(func.count()).select_from(Document)) == 8


def test_mixed_documents_bundle_by_business_fields_without_transaction_reference() -> None:
    """Mixed PDFs remain matchable when demo-only transaction metadata is absent."""
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        service = BatchService(session)
        documents, _ = service.classify_extract_validate(list(reversed(_paths())))
        for document in documents:
            if document.extraction is not None:
                document.extraction.source_references.clear()

        summary, trace = service.match_and_check_completeness(
            documents,
            "BATCH-BUSINESS-FIELDS",
        )

        assert summary.case_count == 3
        assert summary.unmatched_document_ids == []
        assert sorted(case.status for case in summary.cases) == [
            "AWAITING_DOCUMENT",
            "AWAITING_FIELD_INPUT",
            "READY",
        ]

        by_id = {document.document_id: document for document in documents}
        expected_links = {
            "BK-TRD-001": ("INV-TXN001", "BL-TRD-001"),
            "BK-TRD-002": ("INV-TXN002", "BL-TRD-002"),
            "BK-TRD-003": ("INV-TXN003", None),
        }
        for proposal in summary.cases:
            booking = by_id[proposal.booking_document_id]
            invoice = by_id[proposal.invoice_document_id]
            booking_no = booking.extraction.fields["booking_no"].normalized_value
            invoice_no = invoice.extraction.fields["invoice No."].normalized_value
            bl_no = (
                by_id[proposal.bl_document_id].extraction.fields["bl_no"].normalized_value
                if proposal.bl_document_id
                else None
            )
            assert (invoice_no, bl_no) == expected_links[booking_no]

        score_trace = [item for item in trace if item["name"] == "score_case_candidates"]
        assert len(score_trace) == 5
        assert all(item["strategy"] == "BUSINESS_FIELD_SCORE" for item in score_trace)
        bl_trace = [
            item
            for item in score_trace
            if item["document_type"] == DocumentType.BILL_OF_LADING.value
        ]
        assert len(bl_trace) == 2
        assert all(item["verdict"] == "HIGH" for item in bl_trace)
        assert all(
            {relation["relation"] for relation in item["relations"]}
            == {"BILL_OF_LADING_BOOKING", "BILL_OF_LADING_INVOICE"}
            for item in bl_trace
        )


def test_bill_of_lading_can_use_invoice_evidence_when_booking_evidence_is_weak() -> None:
    """B/L↔Invoice remains a real fallback even when a Booking is present."""
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    paths = sorted(path for path in _paths() if "TRD-001" in path.name)
    with Session(engine) as session:
        service = BatchService(session)
        documents, _ = service.classify_extract_validate(paths)
        booking = next(
            document
            for document in documents
            if document.doc_type is DocumentType.BOOKING_CONFIRMATION
        )
        for document in documents:
            if document.extraction is not None:
                document.extraction.source_references.clear()
        assert booking.extraction is not None
        for field in (
            "booking_bl_no",
            "port_of_loading",
            "port_of_discharge",
            "vessel_name",
            "voyage_no",
        ):
            booking.extraction.fields[field].normalized_value = f"MISMATCH-{field}"

        summary, trace = service.match_and_check_completeness(
            documents,
            "BATCH-INVOICE-FALLBACK",
        )

        assert summary.case_count == 1
        assert summary.unmatched_document_ids == []
        assert summary.cases[0].status == "READY"
        bl_trace = next(
            item for item in trace if item.get("document_type") == DocumentType.BILL_OF_LADING.value
        )
        assert bl_trace["score"] == 85.0
        assert bl_trace["verdict"] == "HIGH"
        relation_scores = {
            relation["relation"]: relation["score"] for relation in bl_trace["relations"]
        }
        assert relation_scores == {
            "BILL_OF_LADING_BOOKING": 20.0,
            "BILL_OF_LADING_INVOICE": 85.0,
        }

from __future__ import annotations

from dataclasses import replace
from datetime import date
from pathlib import Path

from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session

from app.core.config import PROJECT_ROOT
from app.db.models import (
    Base,
    Company,
    Document,
    DocumentFact,
    DocumentFieldOverride,
    IngestionBatch,
    Shipment,
    TradeCase,
)
from app.domain_inputs.providers import field_registry
from app.schemas.field_contract import DocumentType
from app.services.ingestion.batch_service import BatchService
from scripts.generate_live_trade_documents import FIXTURES, _bill_of_lading

FIXTURE_DIR = PROJECT_ROOT / "data" / "fixtures" / "live_trade_documents"


def _paths() -> list[Path]:
    return list(reversed(sorted(FIXTURE_DIR.glob("*.pdf"))))


def test_live_eight_pdf_pipeline_persists_partial_shells_and_override_provenance() -> None:
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        service = BatchService(session)
        staged = service.stage_document_intelligence(_paths(), "LIVE-8-PDF")
        assert len(staged.documents) == 8
        assert session.get(IngestionBatch, "LIVE-8-PDF").status == "DOCUMENTS_ANALYZED"

        analysis = service.bundle_staged_batch("LIVE-8-PDF")
        assert [proposal.case_id for proposal in analysis.summary.cases] == [
            "TRD-001",
            "TRD-002",
            "TRD-003",
        ]
        normal, missing_field, missing_document = analysis.summary.cases
        assert normal.status == "READY"
        assert missing_field.status == "AWAITING_FIELD_INPUT"
        assert missing_field.missing_core_fields == ["BILL_OF_LADING.on_board_date"]
        assert missing_document.status == "AWAITING_DOCUMENT"
        assert missing_document.missing_documents == [DocumentType.BILL_OF_LADING]
        assert missing_document.missing_core_fields == []

        assert len(analysis.summary.human_requests) == 1
        request = analysis.summary.human_requests[0]
        assert request.case_id == "TRD-002"
        assert request.field_path == "BILL_OF_LADING.on_board_date"
        assert request.exact_standard_field == "on_board_date"
        assert {
            (issue.case_id, issue.doc_type) for issue in analysis.summary.missing_document_issues
        } == {("TRD-003", DocumentType.BILL_OF_LADING)}

        initial_commit = service.commit("LIVE-8-PDF")
        assert initial_commit.committed_case_ids == [
            "TRD-001",
            "TRD-002",
            "TRD-003",
        ]
        assert initial_commit.pending_case_ids == []
        assert initial_commit.created_document_count == 8
        assert initial_commit.created_override_count == 0
        assert session.get(TradeCase, "TRD-001").status == "MONITORING_READY"
        assert session.get(TradeCase, "TRD-002").status == "AWAITING_FIELD_INPUT"
        assert session.get(TradeCase, "TRD-003").status == "AWAITING_DOCUMENT"
        assert session.get(Shipment, "SHIP-TRD-003").status == "AWAITING_BILL_OF_LADING"
        assert session.get(Company, "A").legal_name == "HANBIT PRECISION CO., LTD."
        assert [
            session.get(TradeCase, reference).transaction_id
            for reference in ("TRD-001", "TRD-002", "TRD-003")
        ] == ["TXN001", "TXN002", "TXN003"]
        assert session.scalar(select(func.count()).select_from(DocumentFact)) == 58
        registry = field_registry()
        for persisted_document in session.scalars(select(Document)).all():
            persisted_keys = set(
                session.scalars(
                    select(DocumentFact.exact_standard_field).where(
                        DocumentFact.document_id == persisted_document.document_id
                    )
                ).all()
            )
            assert persisted_keys == registry.core_keys(DocumentType(persisted_document.doc_type))

        missing_bl = next(
            document
            for document in analysis.documents
            if document.document_id == request.document_id
        )
        assert "on_board_date" not in (
            missing_bl.extraction.fields if missing_bl.extraction else {}
        )
        blank_fact = session.scalar(
            select(DocumentFact).where(
                DocumentFact.document_id == request.document_id,
                DocumentFact.exact_standard_field == "on_board_date",
            )
        )
        assert blank_fact is not None
        assert blank_fact.raw_value == ""
        assert blank_fact.normalized_json == {"value": None}
        assert blank_fact.evidence_source == "DOCUMENT_MISSING"

        corrected = service.apply_document_field_override(
            "LIVE-8-PDF",
            request.document_id,
            "on_board_date",
            "Sep 03, 2026",
            actor="demo-user",
            reason="Verified against carrier event",
        )
        corrected_case = next(
            proposal for proposal in corrected.summary.cases if proposal.case_id == "TRD-002"
        )
        assert corrected_case.status == "READY"
        assert corrected_case.missing_core_fields == []
        assert corrected.summary.human_requests == []
        corrected_source = next(
            document
            for document in corrected.documents
            if document.document_id == request.document_id
        )
        assert "on_board_date" not in (
            corrected_source.extraction.fields if corrected_source.extraction else {}
        )

        corrected_commit = service.commit("LIVE-8-PDF")
        assert corrected_commit.created_override_count == 1
        assert corrected_commit.duplicate_document_count == 8
        assert session.get(TradeCase, "TRD-002").status == "MONITORING_READY"
        assert session.get(Shipment, "SHIP-TRD-002").on_board_date == date(
            2026,
            9,
            3,
        )
        override = session.scalar(select(DocumentFieldOverride))
        assert override is not None
        assert override.raw_input == "Sep 03, 2026"
        assert override.normalized_json == {"value": "2026-09-03"}
        assert override.previous_normalized_json is None
        assert override.evidence_source == "MANUAL_OVERRIDE"
        assert blank_fact.raw_value == ""
        assert blank_fact.normalized_json == {"value": None}

        source_document = session.get(Document, request.document_id)
        assert source_document is not None
        assert source_document.source_references_json == {
            "transaction_reference": "TRD-002",
            "finance_transaction_id": "TXN002",
            "company_id": "A",
        }
        assert session.scalar(select(func.count()).select_from(Document)) == 8


def test_later_bill_of_lading_completes_existing_case_without_reset(
    tmp_path: Path,
) -> None:
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    initial_paths = sorted(FIXTURE_DIR.glob("*TRD-003*.pdf"))
    with Session(engine) as session:
        service = BatchService(session)
        initial = service.analyze(initial_paths, "TRD-003-INITIAL")
        assert initial.summary.cases[0].status == "AWAITING_DOCUMENT"
        service.commit("TRD-003-INITIAL")
        assert session.get(TradeCase, "TRD-003").status == "AWAITING_DOCUMENT"

        source_fixture = next(fixture for fixture in FIXTURES if fixture.reference == "TRD-003")
        completed_fixture = replace(
            source_fixture,
            on_board_date=source_fixture.actual_anchor_date,
        )
        later_bl = tmp_path / "carrier_document.pdf"
        _bill_of_lading(later_bl, completed_fixture)

        completed = service.analyze([later_bl], "TRD-003-LATER-BL")
        proposal = completed.summary.cases[0]
        assert proposal.case_id == "TRD-003"
        assert proposal.status == "READY"
        assert proposal.missing_documents == []
        assert proposal.missing_core_fields == []

        service.commit("TRD-003-LATER-BL")
        assert session.get(TradeCase, "TRD-003").status == "MONITORING_READY"
        shipment = session.get(Shipment, "SHIP-TRD-003")
        assert shipment.status == "BL_RECEIVED"
        assert shipment.on_board_date == date(2026, 8, 24)
        assert session.scalar(select(func.count()).select_from(Document)) == 3

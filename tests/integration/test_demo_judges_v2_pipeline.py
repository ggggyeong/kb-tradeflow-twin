from __future__ import annotations

from datetime import date

from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session

from app.core.config import PROJECT_ROOT
from app.db.models import (
    Base,
    Company,
    Document,
    DocumentFact,
    DocumentFieldOverride,
    PaymentObligation,
    Shipment,
    TradeCase,
)
from app.schemas.field_contract import DocumentType
from app.services.ingestion.batch_service import BatchService

FIXTURE_DIR = PROJECT_ROOT / "data" / "demo_judges_v2"


def test_demo_judges_v2_human_resume_and_minimal_persistence() -> None:
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    paths = list(reversed(sorted(FIXTURE_DIR.glob("*.pdf"))))

    with Session(engine) as session:
        service = BatchService(session)
        analysis = service.analyze(paths, "DEMO-DOCS-20260710")
        assert [proposal.case_id for proposal in analysis.summary.cases] == [
            "TRD-DEMO-001",
            "TRD-DEMO-002",
            "TRD-DEMO-003",
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
        assert request.case_id == "TRD-DEMO-002"
        assert request.exact_standard_field == "on_board_date"
        assert {
            (issue.case_id, issue.doc_type) for issue in analysis.summary.missing_document_issues
        } == {("TRD-DEMO-003", DocumentType.BILL_OF_LADING)}

        corrected = service.apply_document_field_override(
            "DEMO-DOCS-20260710",
            request.document_id,
            "on_board_date",
            "Jun 28, 2026",
            actor="judge-demo-user",
            reason="Verified against carrier event",
        )
        corrected_case = next(
            proposal for proposal in corrected.summary.cases if proposal.case_id == "TRD-DEMO-002"
        )
        assert corrected_case.status == "READY"
        assert corrected.summary.human_requests == []

        commit = service.commit("DEMO-DOCS-20260710")
        assert commit.committed_case_ids == [
            "TRD-DEMO-001",
            "TRD-DEMO-002",
            "TRD-DEMO-003",
        ]
        assert commit.created_document_count == 8
        assert commit.created_override_count == 1
        assert session.scalar(select(func.count()).select_from(Document)) == 8
        assert session.scalar(select(func.count()).select_from(DocumentFact)) == 58
        assert session.scalar(select(func.count()).select_from(DocumentFieldOverride)) == 1

        company = session.get(Company, "DEMO-A")
        assert company is not None
        assert company.legal_name == "HANBIT PRECISION CO., LTD."
        case_001 = session.get(TradeCase, "TRD-DEMO-001")
        case_002 = session.get(TradeCase, "TRD-DEMO-002")
        case_003 = session.get(TradeCase, "TRD-DEMO-003")
        assert case_001 is not None
        assert case_002 is not None
        assert case_003 is not None
        assert [
            case_001.transaction_id,
            case_002.transaction_id,
            case_003.transaction_id,
        ] == ["TXN-DEMO-001", "TXN-DEMO-002", "TXN-DEMO-003"]
        assert case_001.status == "MONITORING_READY"
        assert case_002.status == "MONITORING_READY"
        assert case_003.status == "AWAITING_DOCUMENT"

        shipment_001 = session.get(Shipment, "SHIP-TRD-DEMO-001")
        shipment_002 = session.get(Shipment, "SHIP-TRD-DEMO-002")
        shipment_003 = session.get(Shipment, "SHIP-TRD-DEMO-003")
        assert shipment_001 is not None
        assert shipment_002 is not None
        assert shipment_003 is not None
        assert shipment_001.on_board_date == date(2026, 6, 25)
        assert shipment_002.on_board_date == date(2026, 6, 28)
        assert shipment_003.on_board_date is None
        assert shipment_003.status == "AWAITING_BILL_OF_LADING"

        obligation_terms = list(
            session.scalars(
                select(PaymentObligation.raw_text).order_by(PaymentObligation.case_id)
            ).all()
        )
        assert obligation_terms == [
            "T/T 30 DAYS AFTER B/L DATE",
            "T/T 60 DAYS AFTER B/L DATE",
            "T/T 45 DAYS AFTER B/L DATE",
        ]

        override = session.scalar(select(DocumentFieldOverride))
        assert override is not None
        assert override.raw_input == "Jun 28, 2026"
        assert override.normalized_json == {"value": "2026-06-28"}
        assert override.previous_normalized_json is None
        assert override.evidence_source == "MANUAL_OVERRIDE"

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

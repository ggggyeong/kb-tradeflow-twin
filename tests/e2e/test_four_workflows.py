from __future__ import annotations

from datetime import date
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import create_engine
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

from app.core.config import PROJECT_ROOT
from app.db.models import Base, Shipment
from app.schemas.financial_calendar import (
    FinancialCalendarContract,
    FinancialCalendarEventRow,
    FinancialCalendarLinkRow,
)
from app.services.advisory import AdvisoryService
from app.services.financial_calendar import FinancialCalendarService
from app.services.financial_exposure import FinancialExposureService
from app.services.ingestion.batch_service import BatchService
from app.services.product_advisory import ProductAdvisoryService
from app.services.reports import ReportService


def _paths(pattern: str = "*.pdf") -> list[Path]:
    return sorted((PROJECT_ROOT / "data" / "fixtures" / "live_trade_documents").glob(pattern))


def _txn003_financial_contract() -> FinancialCalendarContract:
    return FinancialCalendarContract(
        source_sha256="test-two-sheet-txn003",
        source_path="tests:two-sheet-txn003",
        version="financial-calendar.v2:test",
        company_id="A",
        events=[
            FinancialCalendarEventRow(
                event_id="FE001",
                event_type_code="WORKING_CAPITAL_LOAN_MATURITY",
                event_name="운전자금대출 만기",
                event_date=date(2026, 10, 3),
                amount=50_000,
                currency="USD",
                financial_institution="KB국민은행",
                is_kb_contract=True,
            )
        ],
        links=[
            FinancialCalendarLinkRow(
                link_id="LNK-TEST-TXN003-FE001",
                transaction_id="TXN003",
                event_id="FE001",
                link_type="EXPECTED_EXPORT_RECEIPT",
                link_status="CONFIRMED",
            )
        ],
    )


@pytest.fixture(scope="module")
def live_domain() -> dict[str, Any]:
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        batch = BatchService(session)
        analysis = batch.analyze(_paths(), "E2E-LIVE-8-PDF")
        commit = batch.commit("E2E-LIVE-8-PDF")
        FinancialCalendarService(session).import_contract(_txn003_financial_contract())
        session.commit()
    return {
        "engine": engine,
        "analysis": analysis,
        "commit": commit,
    }


def _engine(live_domain: dict[str, Any]) -> Engine:
    return live_domain["engine"]


def test_workflow_1_document_intelligence_and_case_bundling(
    live_domain: dict[str, Any],
) -> None:
    summary = live_domain["analysis"].summary
    commit = live_domain["commit"]

    assert summary.file_count == 8
    assert {item.case_id: item.status for item in summary.cases} == {
        "TRD-001": "READY",
        "TRD-002": "AWAITING_FIELD_INPUT",
        "TRD-003": "AWAITING_DOCUMENT",
    }
    assert [request.field_path for request in summary.human_requests] == [
        "BILL_OF_LADING.on_board_date"
    ]
    assert [issue.case_id for issue in summary.missing_document_issues] == ["TRD-003"]
    assert commit.committed_case_ids == ["TRD-001", "TRD-002", "TRD-003"]
    assert commit.pending_case_ids == []


def test_workflow_2_proactive_monitoring_uses_persisted_calendar_facts(
    live_domain: dict[str, Any],
) -> None:
    with Session(_engine(live_domain)) as session:
        snapshot = FinancialExposureService(session).run_proactive_risk_scan(
            "TRD-003",
            as_of_date=date(2026, 9, 18),
            request_id="e2e-proactive-monitoring",
        )

        assert snapshot["source_kind"] == "MONITORING_SCAN"
        assert snapshot["calculation_id"]
        assert snapshot["highest_priority"] == "P4"
        assert snapshot["conflict_count"] >= 1
        assert all(conflict["financial_event_id"] for conflict in snapshot["conflicts"])


def test_workflow_3_user_reported_delay_keeps_original_shipment_immutable(
    live_domain: dict[str, Any],
) -> None:
    with Session(_engine(live_domain)) as session:
        shipment = session.get(Shipment, "SHIP-TRD-003")
        assert shipment is not None
        original = (shipment.status, shipment.etd, shipment.on_board_date)

        advisory = AdvisoryService(session).record_shipment_delay_scenarios(
            "TRD-003",
            reported_at=date(2026, 8, 20),
            request_id="e2e-user-delay",
            expected_delay_days=[9],
        )
        gate = FinancialExposureService(session).inspect_payment_gate("TRD-003")
        exposure = FinancialExposureService(session).calculate_financial_exposure(
            "TRD-003",
            request_id="e2e-user-delay-finance",
            confirmed_anchor="ON_BOARD_DATE",
        )
        confirmed_gate = FinancialExposureService(session).inspect_payment_gate("TRD-003")

        session.refresh(shipment)
        assert advisory.reported_at == date(2026, 8, 20)
        assert advisory.scenarios[0]["delay_days"] == 9
        assert advisory.scenarios[0]["revised_expected_departure_date"] == "2026-08-24"
        assert gate.permitted is False
        assert gate.calculation_allowed == "AFTER_CONFIRMATION"
        assert confirmed_gate.permitted is True
        assert exposure.scenarios[0]["calculated_payment_date"] == "2026-10-08"
        assert (
            shipment.status,
            shipment.etd,
            shipment.on_board_date,
        ) == original


def test_workflow_4_cited_product_options_and_same_basis_reports(
    live_domain: dict[str, Any],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("app.services.reports.REPORT_DIR", tmp_path)
    with Session(_engine(live_domain)) as session:
        FinancialExposureService(session).run_proactive_risk_scan(
            "TRD-003",
            as_of_date=date(2026, 9, 18),
            request_id="e2e-report-snapshot",
        )
        product_service = ProductAdvisoryService()
        matched = product_service.match_scenario(
            query="수입대금과 운전자금 상담",
            scenario_codes=["SUPPLIER_PAYMENT"],
            customer_role="IMPORTER",
            borrower_type="CORPORATION",
        )
        selected = matched["products"][:2]
        evidence = product_service.retrieve_evidence(
            query="수입대금과 운전자금 상담",
            product_ids=[item["product_id"] for item in selected],
            matched_products=selected,
            top_k_per_product=1,
        )
        options = evidence["options"]
        assert options
        assert all(option["citations"] for option in options)

        reports = ReportService(session)
        payload = reports.build_briefing_payload(
            "TRD-003",
            product_options=options,
            as_of_date=date(2026, 9, 18),
        )
        customer = reports.render_report(
            briefing_payload=payload,
            audience="CUSTOMER",
            consent=True,
        )
        rm_report = reports.render_report(
            briefing_payload=payload,
            audience="RM",
            consent=True,
        )
        assert customer["basis_version"] == rm_report["basis_version"]
        assert customer["payload_hash"] == rm_report["payload_hash"]
        assert Path(customer["asset_path"]).stat().st_size > 10_000
        assert Path(rm_report["asset_path"]).stat().st_size > 10_000

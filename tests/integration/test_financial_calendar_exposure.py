from __future__ import annotations

from datetime import date
from pathlib import Path

from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session

from app.core.config import PROJECT_ROOT
from app.db.models import (
    Base,
    CalculationResult,
    Company,
    Conflict,
    Document,
    PaymentObligation,
    Shipment,
    TradeCase,
    TransactionFinancialEventLink,
)
from app.schemas.financial_calendar import (
    FinancialCalendarContract,
    FinancialCalendarEventRow,
    FinancialCalendarLinkRow,
)
from app.services.advisory import AdvisoryService
from app.services.financial_calendar import FinancialCalendarService
from app.services.financial_exposure import FinancialExposureService
from app.services.ingestion.batch_service import BatchService

FIXTURE_DIR = PROJECT_ROOT / "data" / "fixtures" / "live_trade_documents"


def _txn003_paths() -> list[Path]:
    return sorted(FIXTURE_DIR.glob("*TRD-003*.pdf"))


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


def test_missing_bl_case_becomes_monitoring_candidate_without_state_promotion() -> None:
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        batch = BatchService(session)
        batch.analyze(_txn003_paths(), "TRD-003-MONITORING-CANDIDATE")
        batch.commit("TRD-003-MONITORING-CANDIDATE")

        calendar = FinancialCalendarService(session)
        before_import = calendar.select_monitoring_candidates("A", date(2026, 9, 18))
        assert before_import["candidates"] == []

        trade_case = session.get(TradeCase, "TRD-003")
        shipment = session.get(Shipment, "SHIP-TRD-003")
        assert trade_case is not None
        assert shipment is not None
        assert trade_case.status == "AWAITING_DOCUMENT"
        assert trade_case.monitoring_enabled is False
        assert shipment.status == "AWAITING_BILL_OF_LADING"
        assert shipment.etd == date(2026, 8, 15)
        assert shipment.bl_no is None
        assert shipment.on_board_date is None
        assert set(
            session.scalars(select(Document.doc_type).where(Document.case_id == "TRD-003"))
        ) == {"BOOKING_CONFIRMATION", "COMMERCIAL_INVOICE"}

        calendar.import_contract(_txn003_financial_contract())
        obligation = session.scalar(
            select(PaymentObligation).where(PaymentObligation.case_id == "TRD-003")
        )
        assert obligation is not None
        assert obligation.calculation_allowed == "AFTER_CONFIRMATION"
        assert obligation.verified is False

        # This control case exercises the original monitoring_enabled path. It
        # intentionally has no document, shipment, or payment-gate rows.
        session.add(
            TradeCase(
                case_id="READY-CONTROL",
                company_id="A",
                transaction_id="READY-CONTROL",
                company="HANBIT PRECISION CO., LTD.",
                counterparty="CONTROL BUYER",
                status="MONITORING_READY",
                monitoring_enabled=True,
                basis_version="ready-control:v1",
            )
        )
        session.add(
            TransactionFinancialEventLink(
                transaction_event_link_id="READY-CONTROL-LINK",
                company_id="A",
                case_id="READY-CONTROL",
                transaction_id="READY-CONTROL",
                financial_event_id="FE001",
                link_type="SOURCE_REFERENCE",
                link_status="CONFIRMED",
            )
        )
        session.flush()

        selected = calendar.select_monitoring_candidates("A", date(2026, 9, 18))
        assert {item["case_id"] for item in selected["candidates"]} == {
            "READY-CONTROL",
            "TRD-003",
        }

        obligation.tenor_days = None
        session.flush()
        without_verified_gate = calendar.select_monitoring_candidates("A", date(2026, 9, 18))
        assert [item["case_id"] for item in without_verified_gate["candidates"]] == [
            "READY-CONTROL"
        ]
        obligation.tenor_days = 45

        shipment.etd = None
        session.flush()
        without_planned_etd = calendar.select_monitoring_candidates("A", date(2026, 9, 18))
        assert [item["case_id"] for item in without_planned_etd["candidates"]] == ["READY-CONTROL"]
        shipment.etd = date(2026, 8, 15)
        session.flush()

        assert trade_case.status == "AWAITING_DOCUMENT"
        assert trade_case.monitoring_enabled is False
        assert shipment.status == "AWAITING_BILL_OF_LADING"
        assert shipment.bl_no is None
        assert shipment.on_board_date is None


def test_reviewed_on_board_anchor_drives_generic_delay_scenario() -> None:
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        batch = BatchService(session)
        batch.analyze(_txn003_paths(), "TRD-003-FINANCE")
        batch.commit("TRD-003-FINANCE")

        shipment = session.get(Shipment, "SHIP-TRD-003")
        assert shipment is not None
        assert shipment.etd == date(2026, 8, 15)
        assert shipment.on_board_date is None

        company_name = session.get(Company, "A").legal_name
        first_import = FinancialCalendarService(session).import_contract(
            _txn003_financial_contract()
        )
        second_import = FinancialCalendarService(session).import_contract(
            _txn003_financial_contract()
        )
        assert first_import["created"] == {"events": 1, "links": 1}
        assert second_import["created"] == {"events": 0, "links": 0}
        assert session.get(Company, "A").legal_name == company_name
        assert shipment.etd == date(2026, 8, 15)
        assert shipment.on_board_date is None
        obligation = session.scalar(
            select(PaymentObligation).where(PaymentObligation.case_id == "TRD-003")
        )
        assert obligation is not None
        assert obligation.raw_text == "T/T 45 DAYS AFTER B/L DATE"
        assert obligation.anchor_type_effective == "UNKNOWN"
        assert obligation.calculation_allowed == "AFTER_CONFIRMATION"
        assert obligation.verified is False

        AdvisoryService(session).record_shipment_delay_scenarios(
            "TRD-003",
            reported_at=date(2026, 8, 20),
            request_id="txn003-delay",
            expected_delay_days=[9],
        )
        result = FinancialExposureService(session).calculate_financial_exposure(
            "TRD-003",
            request_id="txn003-finance",
            confirmed_anchor="ON_BOARD_DATE",
        )
        calculation = session.get(CalculationResult, result.calculation_id)
        assert calculation is not None
        assert calculation.result_json["expected_receipt"] == {
            "date": "2026-10-08",
            # The minimal financial workbook intentionally carries no invoice
            # amount projection; source-document facts remain authoritative.
            "amount": None,
            "currency": "USD",
        }

        assert result.effective_anchor_type == "ON_BOARD_DATE"
        assert result.scenario_count == 1
        scenario = result.scenarios[0]
        assert scenario["delay_days"] == 9
        assert scenario["revised_expected_departure_date"] == "2026-08-24"
        assert scenario["calculation_anchor_date"] == "2026-08-24"
        assert scenario["calculation_anchor_basis"] == (
            "REVISED_EXPECTED_DEPARTURE_PROXY_FOR_ON_BOARD_DATE"
        )
        assert not any(key.startswith("hypothetical_") for key in scenario)
        assert scenario["calculated_payment_date"] == "2026-10-08"
        assert scenario["expected_receipt_date"] == "2026-10-08"
        fe001 = next(item for item in result.conflicts if item["financial_event_id"] == "FE001")
        assert fe001["gap_days"] == -5
        assert fe001["days_until_event"] == 44
        assert fe001["priority"] == "P4"
        assert fe001["priority_basis"]["response_priority_level"] == "P4"
        assert "legacy_priority_alias" not in fe001["priority_basis"]


def test_proactive_frontier_as_of_priority_and_dedup_are_auditable() -> None:
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        batch = BatchService(session)
        batch.analyze(_txn003_paths(), "TRD-003-PROACTIVE")
        batch.commit("TRD-003-PROACTIVE")
        FinancialCalendarService(session).import_contract(_txn003_financial_contract())

        service = FinancialExposureService(session)
        p4_snapshot = service.run_proactive_risk_scan(
            "TRD-003",
            as_of_date=date(2026, 9, 18),
            request_id="frontier-p4",
        )
        p3_snapshot = service.run_proactive_risk_scan(
            "TRD-003",
            as_of_date=date(2026, 9, 19),
            request_id="frontier-p3",
        )
        p3_duplicate = service.run_proactive_risk_scan(
            "TRD-003",
            as_of_date=date(2026, 9, 19),
            request_id="frontier-p3-duplicate",
        )

        assert p4_snapshot["deduplicated"] is False
        assert p3_snapshot["deduplicated"] is False
        assert p3_duplicate["deduplicated"] is True
        assert p3_duplicate["calculation_id"] == p3_snapshot["calculation_id"]
        assert p4_snapshot["calculation_id"] != p3_snapshot["calculation_id"]
        fe001_p4 = next(
            item for item in p4_snapshot["conflicts"] if item["financial_event_id"] == "FE001"
        )
        fe001_p3 = next(
            item for item in p3_snapshot["conflicts"] if item["financial_event_id"] == "FE001"
        )
        assert fe001_p4["latest_safe_anchor_date"] == "2026-08-19"
        assert fe001_p4["conflict_origin"] == "PREEMPTIVE_BREACH"
        assert fe001_p4["days_until_event"] == 15
        assert fe001_p4["priority"] == "P4"
        assert fe001_p3["days_until_event"] == 14
        assert fe001_p3["priority"] == "P3"
        assert (
            session.scalar(
                select(func.count())
                .select_from(CalculationResult)
                .where(CalculationResult.scenario_name == "PROACTIVE_FINANCIAL_RISK")
            )
            == 2
        )
        assert session.scalar(select(func.count()).select_from(Conflict)) == (
            p4_snapshot["conflict_count"] + p3_snapshot["conflict_count"]
        )

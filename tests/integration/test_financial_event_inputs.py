from __future__ import annotations

from datetime import date

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.core.config import PROJECT_ROOT
from app.db.models import Base
from app.schemas.financial_calendar import (
    FinancialCalendarContract,
    FinancialCalendarEventRow,
    FinancialCalendarLinkRow,
)
from app.services.financial_calendar import FinancialCalendarService
from app.services.financial_exposure import FinancialExposureService
from app.services.ingestion.batch_service import BatchService


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


def test_minimal_calendar_and_reviewed_priority_policy_drive_financial_risk() -> None:
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    paths = sorted(
        (PROJECT_ROOT / "data" / "fixtures" / "live_trade_documents").glob("*TRD-003*.pdf")
    )
    with Session(engine) as session:
        batch = BatchService(session)
        batch.analyze(paths, "FINANCIAL-INPUTS-TRD-003")
        batch.commit("FINANCIAL-INPUTS-TRD-003")
        calendar = FinancialCalendarService(session)
        imported = calendar.import_contract(_txn003_financial_contract())

        snapshot = FinancialExposureService(session).run_proactive_risk_scan(
            "TRD-003",
            as_of_date=date(2026, 9, 18),
            request_id="reviewed-financial-inputs",
        )

    assert imported["created"] == {"events": 1, "links": 1}
    assert snapshot["highest_priority"] == "P4"
    conflict = next(item for item in snapshot["conflicts"] if item["financial_event_id"] == "FE001")
    assert conflict["event_date"] == "2026-10-03"
    assert conflict["days_until_event"] == 15
    assert conflict["latest_safe_anchor_date"] == "2026-08-19"
    assert conflict["conflict_origin"] == "PREEMPTIVE_BREACH"
    assert conflict["priority"] == "P4"

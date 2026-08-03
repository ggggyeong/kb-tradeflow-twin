from __future__ import annotations

from datetime import date
from typing import Any

from langchain_core.tools import tool

from app.db.models import TradeCase
from app.db.session import session_scope
from app.services.financial_exposure import FinancialExposureService


@tool
def inspect_payment_gate(case_id: str) -> dict[str, Any]:
    """Read a Case payment obligation and verify whether calculation is permitted."""
    with session_scope() as session:
        return (
            FinancialExposureService(session).inspect_payment_gate(case_id).model_dump(mode="json")
        )


@tool
def calculate_financial_exposure(
    case_id: str,
    request_id: str,
    confirmed_anchor: str | None = None,
) -> dict[str, Any]:
    """Evaluate canonical shipment-delay scenarios and persist finance-owned conflicts."""
    with session_scope() as session:
        return (
            FinancialExposureService(session)
            .calculate_financial_exposure(case_id, request_id, confirmed_anchor)
            .model_dump(mode="json")
        )


@tool
def run_proactive_risk_scan(
    case_id: str,
    as_of_date: date,
    request_id: str,
) -> dict[str, Any]:
    """Evaluate and persist live due risk, including unresolved shipping-anchor frontiers."""
    with session_scope() as session:
        return FinancialExposureService(session).run_proactive_risk_scan(
            case_id, as_of_date, request_id
        )


@tool
def get_financial_risk_snapshot(
    case_id: str,
    as_of_date: date | None = None,
    allow_missing: bool = False,
) -> dict[str, Any]:
    """Read the latest monitoring-scan or user-reported immutable risk snapshot."""
    with session_scope() as session:
        if session.get(TradeCase, case_id) is None:
            raise KeyError(f"Unknown TradeCase: {case_id}")
        try:
            return FinancialExposureService(session).get_risk_snapshot(case_id, as_of_date)
        except KeyError:
            if not allow_missing:
                raise
            return {
                "case_id": case_id,
                "as_of_date": as_of_date.isoformat() if as_of_date else None,
                "status": "NO_SNAPSHOT",
                "conflict_count": 0,
                "conflicts": [],
            }

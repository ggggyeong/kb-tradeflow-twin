from __future__ import annotations

from datetime import date
from pathlib import Path
from typing import Any

from langchain_core.tools import tool

from app.db.session import session_scope
from app.services.financial_calendar import FinancialCalendarService


@tool
def validate_financial_calendar(
    company_id: str,
    workbook_path: str | None = None,
    batch_id: str | None = None,
) -> dict[str, Any]:
    """Validate the exact two-sheet calendar and existing transaction ownership."""
    path = FinancialCalendarService.resolve_workbook_path(
        path=Path(workbook_path) if workbook_path else None,
        batch_id=batch_id,
    )
    with session_scope() as session:
        return FinancialCalendarService(session).validate_workbook(
            path,
            company_id=company_id,
        )


@tool
def import_financial_calendar(
    company_id: str,
    workbook_path: str | None = None,
    batch_id: str | None = None,
) -> dict[str, Any]:
    """Upsert only financial events and links for existing company transactions."""
    path = FinancialCalendarService.resolve_workbook_path(
        path=Path(workbook_path) if workbook_path else None,
        batch_id=batch_id,
    )
    with session_scope() as session:
        return FinancialCalendarService(session).import_workbook(
            path,
            company_id=company_id,
        )


@tool
def select_financial_monitoring_candidates(
    company_id: str,
    as_of_date: date,
    limit: int = 100,
) -> dict[str, Any]:
    """Select the authenticated company's linked cases for downstream risk analysis."""
    with session_scope() as session:
        return FinancialCalendarService(session).select_monitoring_candidates(
            company_id,
            as_of_date,
            limit,
        )


@tool
def update_financial_event_link_details(
    company_id: str,
    case_id: str,
    transaction_id: str,
    transaction_event_link_id: str,
    details: dict[str, Any],
    actor: str,
) -> dict[str, Any]:
    """Store explicit Human review of optional post-conflict link details."""
    with session_scope() as session:
        return FinancialCalendarService(session).update_financial_event_link_details(
            company_id=company_id,
            case_id=case_id,
            transaction_id=transaction_id,
            transaction_event_link_id=transaction_event_link_id,
            details=details,
            actor=actor,
        )

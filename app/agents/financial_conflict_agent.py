from __future__ import annotations

from datetime import date

from app.schemas.portfolio import (
    PortfolioConflictResult,
    PortfolioFinancialEvent,
)
from app.services.financial_conflict import classify_portfolio_conflicts


class FinancialConflictAgent:
    """Classify the three portfolio scenarios with deterministic date rules."""

    name = "financial_conflict_agent"

    def run(
        self,
        expected_receipt_date: date | None,
        events: list[PortfolioFinancialEvent],
    ) -> list[PortfolioConflictResult]:
        return classify_portfolio_conflicts(expected_receipt_date, events)

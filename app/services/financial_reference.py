from __future__ import annotations

from typing import Any

from sqlalchemy.orm import Session

from app.db.models import TradeCase


def read_transaction_financial_reference(
    session: Session,
    trade_case: TradeCase,
) -> dict[str, Any]:
    """Return only values projected by the active minimal document contract.

    The final two-sheet financial calendar does not create a financial timeline,
    and the minimal invoice projection does not persist an invoice amount.  The
    case currency is therefore the only supported receipt reference; an amount
    remains explicitly unknown until a reviewed source adds a canonical field.
    """
    del session
    return {
        "timeline_id": None,
        "amount": None,
        "currency": trade_case.currency,
    }

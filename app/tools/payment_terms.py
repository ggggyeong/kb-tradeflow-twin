from __future__ import annotations

from datetime import date, timedelta

from langchain_core.tools import tool
from pydantic import BaseModel


class PaymentDateToolResult(BaseModel):
    """Deterministic date result with explicit tool version."""

    anchor_date: date
    tenor_days: int
    day_type: str
    payment_date: date
    tool_version: str = "payment-date.v16.0"


def calculate_payment_date_value(
    anchor_date: date, tenor_days: int, day_type: str
) -> PaymentDateToolResult:
    """Calculate a payment date after all graph-level verification gates pass."""
    if tenor_days < 0:
        raise ValueError("tenor_days must be non-negative")
    if day_type == "CALENDAR":
        result = anchor_date + timedelta(days=tenor_days)
    elif day_type == "BUSINESS":
        result = anchor_date
        remaining = tenor_days
        while remaining:
            result += timedelta(days=1)
            if result.weekday() < 5:
                remaining -= 1
    else:
        raise ValueError(f"Unsupported effective day type: {day_type}")
    return PaymentDateToolResult(
        anchor_date=anchor_date,
        tenor_days=tenor_days,
        day_type=day_type,
        payment_date=result,
    )


@tool
def calculate_payment_date(
    anchor_date: date, tenor_days: int, day_type: str
) -> dict[str, str | int]:
    """Calculate the payment date only after verified=true and calculation_allowed=YES."""
    return calculate_payment_date_value(anchor_date, tenor_days, day_type).model_dump(mode="json")

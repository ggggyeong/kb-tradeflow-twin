from __future__ import annotations

from datetime import date

from app.schemas.portfolio import (
    ConflictStatus,
    PortfolioConflictResult,
    PortfolioFinancialEvent,
)


def classify_portfolio_conflicts(
    expected_receipt_date: date | None,
    events: list[PortfolioFinancialEvent],
) -> list[PortfolioConflictResult]:
    """Compare one expected receipt date with the three supported schedules.

    Positive ``gap_days`` means the financial obligation arrives before cash is
    expected.  The function is intentionally rule based: an LLM never calculates
    or changes dates, amounts, or currencies.
    """
    results: list[PortfolioConflictResult] = []
    for event in sorted(events, key=lambda item: (item.event_date, item.event_id)):
        gap_days = (
            (expected_receipt_date - event.event_date).days
            if expected_receipt_date is not None
            else None
        )
        status: ConflictStatus
        if event.link_status == "NOT_LINKED":
            status = "NO_CONFLICT"
            reason = "거래 대금과 연결되지 않은 금융일정입니다."
        elif expected_receipt_date is None:
            status = "REVIEW_REQUIRED"
            reason = "예상 대금 유입일이 없어 날짜 충돌을 확정할 수 없습니다."
        elif gap_days is not None and gap_days >= 0:
            if event.link_status == "CONFIRMED":
                status = "CONFLICT"
                reason = (
                    f"{event.event_name} 일정이 예상 대금 유입보다 "
                    f"{gap_days}일 먼저 도래합니다."
                )
            else:
                status = "REVIEW_REQUIRED"
                reason = (
                    f"날짜상 {gap_days}일의 자금 공백 가능성이 있으나 "
                    "거래 연결 확인이 필요합니다."
                )
        else:
            status = "NO_CONFLICT"
            reason = "예상 대금 유입 후 도래하는 일정으로 날짜 충돌이 없습니다."

        results.append(
            PortfolioConflictResult(
                event_id=event.event_id,
                scenario_code=event.scenario_code,
                status=status,
                event_name=event.event_name,
                event_date=event.event_date,
                expected_receipt_date=expected_receipt_date,
                gap_days=gap_days,
                amount=event.amount,
                currency=event.currency,
                reason=reason,
            )
        )
    return results

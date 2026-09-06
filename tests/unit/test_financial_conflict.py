from datetime import date

from app.schemas.portfolio import PortfolioFinancialEvent
from app.services.financial_conflict import classify_portfolio_conflicts


def _event(
    event_id: str,
    scenario_code: str,
    event_date: date,
    *,
    link_status: str = "CONFIRMED",
) -> PortfolioFinancialEvent:
    return PortfolioFinancialEvent.model_validate(
        {
            "event_id": event_id,
            "scenario_code": scenario_code,
            "event_name": event_id,
            "event_date": event_date,
            "amount": 10_000,
            "currency": "usd",
            "link_status": link_status,
        }
    )


def test_three_scenarios_share_one_explainable_date_rule() -> None:
    results = classify_portfolio_conflicts(
        date(2026, 9, 20),
        [
            _event("loan", "WORKING_CAPITAL_LOAN_MATURITY", date(2026, 9, 15)),
            _event(
                "fx",
                "FX_FORWARD_MATURITY",
                date(2026, 9, 20),
                link_status="UNCONFIRMED",
            ),
            _event("supplier", "SUPPLIER_PAYMENT", date(2026, 9, 25)),
        ],
    )

    assert [item.status for item in results] == [
        "CONFLICT",
        "REVIEW_REQUIRED",
        "NO_CONFLICT",
    ]
    assert [item.gap_days for item in results] == [5, 0, -5]
    assert results[0].currency == "USD"


def test_missing_receipt_date_fails_closed_without_guessing() -> None:
    [result] = classify_portfolio_conflicts(
        None,
        [_event("loan", "WORKING_CAPITAL_LOAN_MATURITY", date(2026, 9, 15))],
    )

    assert result.status == "REVIEW_REQUIRED"
    assert result.gap_days is None
    assert "유입일" in result.reason


def test_unlinked_schedule_is_not_presented_as_a_conflict() -> None:
    [result] = classify_portfolio_conflicts(
        date(2026, 9, 20),
        [
            _event(
                "supplier",
                "SUPPLIER_PAYMENT",
                date(2026, 9, 10),
                link_status="NOT_LINKED",
            )
        ],
    )

    assert result.status == "NO_CONFLICT"

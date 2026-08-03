from __future__ import annotations

from datetime import date

from app.services.payment_terms import (
    compute_if_allowed,
    confirm_anchor,
    parse_payment_terms,
)


def test_tt_bl_date_does_not_calculate_before_confirmation() -> None:
    obligation = parse_payment_terms("T/T 30 DAYS AFTER B/L DATE")[0]
    result = compute_if_allowed(obligation, date(2026, 8, 31))
    assert obligation.verified is False
    assert result.calculate_called is False
    assert result.computed_payment_date is None


def test_confirmed_tt_bl_date_calculates_calendar_days() -> None:
    obligation = parse_payment_terms("T/T 30 DAYS AFTER B/L DATE")[0]
    confirmed = confirm_anchor(obligation, "B/L_DATE")
    result = compute_if_allowed(confirmed, date(2026, 8, 31))
    assert result.calculate_called is True
    assert result.computed_payment_date == date(2026, 9, 30)


def test_event_based_terms_never_calculate_dates() -> None:
    for raw in ["L/C AT SIGHT", "D/P AT SIGHT", "CASH AGAINST DOCUMENTS"]:
        obligation = parse_payment_terms(raw)[0]
        result = compute_if_allowed(obligation, date(2026, 8, 31))
        assert obligation.calculation_allowed == "NO"
        assert result.calculate_called is False


def test_mixed_terms_are_split_into_two_tranches() -> None:
    obligations = parse_payment_terms("20% DOWN PAYMENT, 80% BY T/T 30 DAYS AFTER B/L DATE")
    assert [item.payment_ratio for item in obligations] == ["20%", "80%"]
    assert [item.calculation_allowed for item in obligations] == [
        "NO",
        "AFTER_CONFIRMATION",
    ]

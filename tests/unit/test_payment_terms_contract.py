from __future__ import annotations

from app.domain_inputs.providers import payment_terms_contract
from app.services.payment_terms import parse_payment_terms


def test_payment_terms_has_four_reviewed_sheets_and_21_examples() -> None:
    contract = payment_terms_contract()
    assert list(contract.sheet_rows) == [
        "1.분해 컬럼 스펙",
        "2.결제조건 분류(핵심_부차)",
        "3.고객확인 질문",
        "4.예문 세트",
    ]
    assert len(contract.examples) == 21


def test_final_workbook_safety_examples() -> None:
    contract = payment_terms_contract()
    tt_bl = contract.exact_examples("T/T 30 DAYS AFTER B/L DATE")
    assert tt_bl[0].anchor_type_effective == "UNKNOWN"
    assert tt_bl[0].calculation_allowed == "AFTER_CONFIRMATION"
    for raw in ["L/C AT SIGHT", "D/P AT SIGHT", "CASH AGAINST DOCUMENTS"]:
        assert contract.exact_examples(raw)[0].calculation_allowed == "NO"


def test_bounded_workbook_families_and_unknown_forms_fail_closed() -> None:
    tt = parse_payment_terms("T/T 45 DAYS AFTER B/L DATE")[0]
    assert tt.tenor_days == 45
    assert tt.anchor_type_effective == "UNKNOWN"
    assert tt.calculation_allowed == "AFTER_CONFIRMATION"
    assert tt.verified is False

    usance = parse_payment_terms("USANCE L/C 60 DAYS AFTER B/L DATE")[0]
    assert usance.tenor_days == 60
    assert usance.anchor_type_effective == "ON_BOARD_DATE"
    assert usance.calculation_allowed == "YES"
    assert usance.verified is True

    unknown = parse_payment_terms("T/T 45 DAYS AFTER UNKNOWN SHIPPING EVENT")[0]
    assert unknown.anchor_type_effective == "UNKNOWN"
    assert unknown.calculation_allowed == "NO"
    assert unknown.verified is False

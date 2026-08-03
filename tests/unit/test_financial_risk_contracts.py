from __future__ import annotations

from datetime import date

from app.domain_inputs.providers import (
    financial_priority_policy,
)
from app.schemas.financial_calendar import (
    FinancialPriorityFact,
    FinancialRiskEvaluationInput,
    FinancialRiskEventFact,
)
from app.services.financial_risk import DeterministicFinancialRiskEngine


def _event_fact() -> FinancialRiskEventFact:
    return FinancialRiskEventFact(
        financial_event_id="FE001",
        company_id="A",
        event_type_code="WORKING_CAPITAL_LOAN_MATURITY",
        event_name="운전자금대출 만기",
        event_date=date(2026, 10, 3),
        amount=50_000,
        currency="USD",
        financial_institution="KB국민은행",
        is_kb_contract=True,
        transaction_event_link_id="LNK-TEST-TXN003-FE001",
        link_type="EXPECTED_EXPORT_RECEIPT",
        link_status="CONFIRMED",
        dependency_scope="UNKNOWN",
    )


def test_priority_engine_matches_reviewed_portfolio_example_order() -> None:
    policy = financial_priority_policy()
    type_codes = {
        "대출": "WORKING_CAPITAL_LOAN_MATURITY",
        "선물환": "FX_FORWARD_MATURITY",
        "공급업체": "SUPPLIER_PAYMENT",
    }
    facts = [
        FinancialPriorityFact(
            company_id=row.company_id,
            event_id=row.event_id,
            days_until_event=row.days_until_event,
            link_status=row.link_status,
            impact_level=row.impact_level,
            event_type_code=type_codes[row.event_type],
            lead_days=row.lead_days,
            same_day_flag=row.same_day_flag,
        )
        for row in policy.portfolio_examples
    ]

    ranked = DeterministicFinancialRiskEngine(policy).rank_priority_facts(
        facts,
        ranking_scope="KB_PORTFOLIO_VIEW",
    )
    assert [item.event_id for item in ranked] == [
        "FE-B03",
        "FE001",
        "FE002",
        "FE-B01",
        "FE007",
        "FE006",
        "FE004",
    ]
    expected_by_id = {row.event_id: row for row in policy.portfolio_examples}
    for item in ranked:
        expected = expected_by_id[item.event_id]
        assert item.priority_rank == expected.expected_priority_rank
        assert item.response_priority_level == expected.expected_response_priority_level
        assert item.urgency_bucket == expected.urgency_bucket
        assert item.display_label == expected.display_label


def test_unresolved_anchor_uses_event_specific_risk_frontier() -> None:
    event = _event_fact()
    evaluation = DeterministicFinancialRiskEngine().evaluate(
        FinancialRiskEvaluationInput(
            case_id="TRD-003",
            company_id="A",
            transaction_id="TXN003",
            as_of_date=date(2026, 8, 19),
            source_kind="MONITORING_SCAN",
            source_ref_id="frontier:TRD-003",
            planned_due_date=date(2026, 9, 29),
            revised_due_date=None,
            tenor_days=45,
            anchor_resolved=False,
            events=[event],
        )
    )

    assert evaluation.status == "EVALUATED"
    assert len(evaluation.conflicts) == 1
    conflict = evaluation.conflicts[0]
    assert conflict.latest_safe_anchor_date == date(2026, 8, 19)
    assert conflict.frontier_days_remaining == 0
    assert conflict.conflict_origin == "PREEMPTIVE_BREACH"
    assert conflict.display_to_user is True


def test_conflict_link_details_are_requested_in_two_reviewed_stages() -> None:
    engine = DeterministicFinancialRiskEngine()

    def evaluate(event: FinancialRiskEventFact):
        return engine.evaluate(
            FinancialRiskEvaluationInput(
                case_id="TRD-003",
                company_id="A",
                transaction_id="TXN003",
                as_of_date=date(2026, 8, 20),
                source_kind="USER_REPORTED_DELAY",
                source_ref_id=f"link-details:{event.dependency_scope}",
                payment_obligation_id="PO-TRD-003-1",
                effective_anchor_type="ON_BOARD_DATE",
                day_type_effective="CALENDAR",
                planned_anchor_basis="BOOKING_ETD_PROXY",
                revised_anchor_basis="REVISED_EXPECTED_DEPARTURE_PROXY",
                planned_due_date=date(2026, 9, 29),
                revised_due_date=date(2026, 10, 8),
                tenor_days=45,
                events=[event],
            )
        )

    unknown = evaluate(_event_fact())
    assert unknown.data_actions[0]["code"] == "FINANCIAL_LINK_DETAILS_REQUIRED"
    assert unknown.data_actions[0]["missing_fields"] == ["dependency_scope"]
    assert unknown.data_actions[0]["transaction_event_link_id"] == "LNK-TEST-TXN003-FE001"

    full = evaluate(_event_fact().model_copy(update={"dependency_scope": "FULL"}))
    assert full.data_actions == []
    assert full.conflicts[0].expected_receipt_amount == 50_000
    assert full.conflicts[0].expected_receipt_currency == "USD"
    assert full.conflicts[0].uncovered_amount == 0

    partial = evaluate(_event_fact().model_copy(update={"dependency_scope": "PARTIAL"}))
    assert partial.data_actions[0]["missing_fields"] == ["linked_amount", "linked_currency"]

    completed_partial = evaluate(
        _event_fact().model_copy(
            update={
                "dependency_scope": "PARTIAL",
                "linked_amount": 25_000,
                "linked_currency": "USD",
            }
        )
    )
    assert completed_partial.data_actions == []
    assert completed_partial.conflicts[0].expected_receipt_amount == 25_000
    assert completed_partial.conflicts[0].uncovered_amount == 25_000

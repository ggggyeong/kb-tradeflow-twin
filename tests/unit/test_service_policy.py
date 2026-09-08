from typing import Any

import pytest

from app.agents.finance_advisor import FinanceAdvisorAgent
from app.schemas.portfolio import PortfolioConflictResult
from app.services.financial_retrieval import FinancialRetrieval
from app.services.llm_controller import RunModel
from app.services.product_vector_store import ProductVectorError, extract_reviewed_section
from app.services.service_policy import POLICIES, build_service_cards, get_answer_slots
from tests.helpers import ScriptedModel
from tests.unit.test_finance_advisor import FakeStore, catalog, request, submission


def test_three_policies_have_information_questions_not_answer_locations() -> None:
    assert len({p.code for p in POLICIES.values()}) == 3
    questions = [q for p in POLICIES.values() for q in p.search_questions]
    assert len({q.query_id for q in questions}) == len(questions)
    assert all(".pdf" not in q.text and "페이지" not in q.text for q in questions)
    assert all(
        q.query_id.startswith("fx_") for q in POLICIES["FX_FORWARD_MATURITY"].search_questions
    )
    assert sum(q.receivables_only for q in questions) == 4


def test_answer_slots_are_linked_to_search_questions_and_receivable_gate() -> None:
    for scenario, policy in POLICIES.items():
        query_ids = {q.query_id for q in policy.search_questions}
        for confirmed in (False, True):
            slots = get_answer_slots(scenario, export_receivable_confirmed=confirmed)
            assert 1 <= len(slots) <= 3
            assert len({s.slot_id for s in slots}) == len(slots)
            assert all(set(s.query_ids) <= query_ids for s in slots)
    plain = get_answer_slots("SUPPLIER_PAYMENT", export_receivable_confirmed=False)
    verified = get_answer_slots("SUPPLIER_PAYMENT", export_receivable_confirmed=True)
    assert {s.slot_id for s in plain} == {"funding_conditions", "funding_costs"}
    assert {s.slot_id for s in verified} == {
        "funding_conditions",
        "receivable_requirements",
        "receivable_repurchase",
    }


@pytest.mark.parametrize("status", ["CONFLICT", "NO_CONFLICT", "REVIEW_REQUIRED"])
def test_cards_keep_abstention_and_input_review_visible(status: Any) -> None:
    conflict = PortfolioConflictResult(
        event_id="E",
        scenario_code="SUPPLIER_PAYMENT",
        status=status,
        event_name="지급",
        reason="규칙 판단 결과",
    )
    card = build_service_cards([conflict], [])[0]
    assert not card.information
    assert card.status == ("NO_CONFLICT" if status == "NO_CONFLICT" else "REVIEW_REQUIRED")
    assert bool(card.questions) == (status != "NO_CONFLICT")
    assert card.notices
    if status == "CONFLICT":
        assert not any("근거가 부족" in notice for notice in card.notices)
        assert any("검색·생성·근거 대조" in notice for notice in card.notices)


def test_only_retrieved_source_span_can_be_selected() -> None:
    fake = ScriptedModel(
        [
            "compare_financial_dates",
            "search_financial_documents",
            submission(evidence_id="invented-source-span"),
        ]
    )
    _, options, warnings = FinanceAdvisorAgent(
        retrieval=FinancialRetrieval(store=FakeStore(), catalog=catalog())
    ).run(request(), [], model=RunModel(fake, 12))
    assert not options and any("근거 ID" in w for w in warnings)


@pytest.mark.parametrize(
    "changes",
    [
        {"topic": "LOAN_EXTENSION"},
        {"section_id": "invented"},
        {"page": 3},
    ],
)
def test_wrong_topic_or_section_is_not_evidence(changes: dict[str, Any]) -> None:
    store = FakeStore()
    store.hit_changes = changes
    fake = ScriptedModel(["compare_financial_dates", "search_financial_documents"])
    _, options, warnings = FinanceAdvisorAgent(
        retrieval=FinancialRetrieval(store=store, catalog=catalog())
    ).run(request(), [], model=RunModel(fake, 12))
    assert not options and warnings


def test_unreviewed_sections_never_fall_back_to_whole_pdf() -> None:
    data = catalog()
    for product in data.products:
        product.sections = []
    store = FakeStore()
    fake = ScriptedModel(["compare_financial_dates", "search_financial_documents"])
    _, options, warnings = FinanceAdvisorAgent(
        retrieval=FinancialRetrieval(store=store, catalog=data)
    ).run(request(), [], model=RunModel(fake, 12))
    assert not options and warnings and not store.calls


def test_section_extraction_excludes_adjacent_irrelevant_product() -> None:
    assert (
        extract_reviewed_section(
            "목차\n대출 용도\n 생산 판매\n 수입 보증 unrelated", "대출 용도", "수입 보증"
        )
        == "대출 용도 생산 판매"
    )


@pytest.mark.parametrize("text", ["no anchors", "끝문장 시작문장", "시작문장 내용 시작문장 끝문장"])
def test_changed_or_ambiguous_source_boundaries_fail_closed(text: str) -> None:
    with pytest.raises(ProductVectorError):
        extract_reviewed_section(text, "시작문장", "끝문장")

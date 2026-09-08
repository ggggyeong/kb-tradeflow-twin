from typing import Any

import pytest

from app.agents.finance_advisor import FinanceAdvisorAgent
from app.schemas.portfolio import PortfolioConflictResult
from app.services.financial_retrieval import FinancialRetrieval
from app.services.llm_controller import RunModel
from app.services.product_vector_store import ProductVectorError, extract_reviewed_section
from app.services.service_policy import POLICIES, build_service_cards
from tests.helpers import ScriptedModel
from tests.unit.test_finance_advisor import FakeStore, catalog, request, submission


def test_three_policies_have_distinct_services_and_topics() -> None:
    assert len({p.code for p in POLICIES.values()}) == 3
    assert not set(POLICIES["WORKING_CAPITAL_LOAN_MATURITY"].topics) & set(
        POLICIES["SUPPLIER_PAYMENT"].topics
    )
    assert all(t.startswith("FX_") for t in POLICIES["FX_FORWARD_MATURITY"].topics)


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


def test_source_quote_must_be_exact_not_generated() -> None:
    fake = ScriptedModel(
        [
            "compare_financial_dates",
            "search_financial_documents",
            submission(
                supporting_quote="원문에 존재하지 않는 승인 보장 문장은 이 테스트에서 반드시 거부되어야 합니다."
            ),
        ]
    )
    _, options, warnings = FinanceAdvisorAgent(
        retrieval=FinancialRetrieval(store=FakeStore(), catalog=catalog())
    ).run(request(), [], model=RunModel(fake, 12))
    assert not options and any("원문과 일치하지" in w for w in warnings)


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

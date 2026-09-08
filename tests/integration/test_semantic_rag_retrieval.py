"""Actual local E5/Chroma retrieval checks, with no LLM or paid API calls.

Run after rebuilding the reviewed page corpus:
    TRADEFLOW_LOCAL_MODELS=1 pytest tests/integration/test_semantic_rag_retrieval.py -q

These are retrieval regression checks, not evidence of financial advice accuracy.
"""

from __future__ import annotations

import json
import os
from collections.abc import Iterator
from datetime import date

import pytest

from app.core.paths import PRODUCT_CATALOG_PATH, PRODUCT_PDF_DIR, PRODUCT_VECTOR_DIR
from app.schemas.portfolio import (
    PortfolioConflictResult,
    PortfolioFinancialEvent,
    PortfolioRunRequest,
)
from app.services.financial_retrieval import FinancialRetrieval
from app.services.product_catalog import ProductCatalog
from app.services.product_vector_store import (
    VECTOR_MANIFEST_SCHEMA,
    ChromaProductVectorStore,
)
from app.services.service_policy import POLICIES

pytestmark = pytest.mark.skipif(
    os.getenv("TRADEFLOW_LOCAL_MODELS") != "1",
    reason="Opt in after downloading E5 and rebuilding the page corpus; no live LLM is used.",
)

SHINHAN = "SHINHAN-WORKING-CAPITAL-GUIDE"
RECEIVABLES = "HSBC-EXPORT-RECEIVABLE-AGREEMENT"
ARCHIVED_COST_GUIDE = "HSBC-EXPORT-RECEIVABLE-GUIDE"
FX = "BOC-FX-FORWARD-SELL-GUIDE"


@pytest.fixture(scope="module")
def local_store() -> Iterator[ChromaProductVectorStore]:
    manifest = json.loads((PRODUCT_VECTOR_DIR / "product_vector_manifest.json").read_text())
    assert manifest["schema_version"] == VECTOR_MANIFEST_SCHEMA, "Rebuild the page corpus first"
    assert manifest["unique_chunk_count"] > 12
    assert ARCHIVED_COST_GUIDE not in manifest["product_ids"]
    # Local-only means the test must never silently fetch a model from the Hub.
    with pytest.MonkeyPatch.context() as patch:
        patch.setenv("HF_HUB_OFFLINE", "1")
        patch.setenv("TRANSFORMERS_OFFLINE", "1")
        store = ChromaProductVectorStore(persist_directory=PRODUCT_VECTOR_DIR)
        store.validate_manifest(pdf_directory=PRODUCT_PDF_DIR, catalog_path=PRODUCT_CATALOG_PATH)
        yield store


def test_different_questions_select_different_evidence_in_the_same_source_scope(
    local_store: ChromaProductVectorStore,
) -> None:
    questions = {
        question.query_id: question.text
        for question in POLICIES["WORKING_CAPITAL_LOAN_MATURITY"].search_questions
    }
    overdue = local_store.search(
        questions["loan_repayment_risk"],
        ["WORKING_CAPITAL_LOAN_MATURITY"],
        allowed_product_ids=[SHINHAN],
        top_k=3,
    )
    overdue_trace = dict(local_store.last_search_trace)
    renewal = local_store.search(
        questions["loan_renewal_documents"],
        ["WORKING_CAPITAL_LOAN_MATURITY"],
        allowed_product_ids=[SHINHAN],
        top_k=3,
    )
    renewal_trace = dict(local_store.last_search_trace)
    extension = local_store.search(
        questions["loan_extension_preparation"],
        ["WORKING_CAPITAL_LOAN_MATURITY"],
        allowed_product_ids=[SHINHAN],
        top_k=3,
    )

    assert overdue and renewal
    assert overdue_trace["where"] == renewal_trace["where"]
    assert overdue_trace["unique_candidate_count"] > 3
    assert "topic" not in json.dumps(overdue_trace["where"])
    assert "page" not in json.dumps(overdue_trace["where"])
    assert {hit["chunk_id"] for hit in overdue} != {hit["chunk_id"] for hit in renewal}
    assert any("연체이자" in hit["excerpt"] or "연체이율" in hit["excerpt"] for hit in overdue)
    assert any("재무제표" in hit["excerpt"] or "매출" in hit["excerpt"] for hit in renewal)
    assert any("연장" in hit["excerpt"] and "거절" in hit["excerpt"] for hit in extension)
    assert all(hit["product_id"] == SHINHAN for hit in [*overdue, *renewal, *extension])

    # The old multi-intent question missed the document requirement even in
    # its top eight before section-aware chunking. Keep that recall regression.
    combined = local_store.search(
        "기업대출 만기 연장과 갱신의 심사 조건 및 준비해야 할 재무제표와 매출 자료는 무엇인가?",
        ["WORKING_CAPITAL_LOAN_MATURITY"],
        allowed_product_ids=[SHINHAN],
        top_k=3,
    )
    assert any("재무제표" in hit["excerpt"] and "매출" in hit["excerpt"] for hit in combined)


def test_irrelevant_query_does_not_escape_the_fx_source_scope(
    local_store: ChromaProductVectorStore,
) -> None:
    hits = local_store.search(
        "주택담보대출 한도와 우대 금리 신청 조건은 무엇인가?",
        ["FX_FORWARD_MATURITY"],
        allowed_product_ids=[FX],
        top_k=3,
    )
    assert hits
    assert all(hit["product_id"] == FX for hit in hits)
    assert all(hit["scenario_code"] == "FX_FORWARD_MATURITY" for hit in hits)
    assert local_store.last_search_trace["unique_candidate_count"] > 3
    # Nearest-neighbour retrieval can return an irrelevant passage. A score is
    # not proof the question is answerable; abstention belongs to generation.


def test_fx_questions_retrieve_contract_obligation_and_closeout_conditions(
    local_store: ChromaProductVectorStore,
) -> None:
    answers = {
        question.query_id: local_store.search(
            question.text, ["FX_FORWARD_MATURITY"], allowed_product_ids=[FX], top_k=3
        )
        for question in POLICIES["FX_FORWARD_MATURITY"].search_questions
    }
    obligations = answers["fx_settlement_obligation"]
    assert any(
        "약정환율" in hit["excerpt"] and "매도하기로" in hit["excerpt"] for hit in obligations
    )
    closeout = answers["fx_closeout_risk"]
    assert any("동의" in hit["excerpt"] and "중도해지" in hit["excerpt"] for hit in closeout)
    assert any("손실" in hit["excerpt"] and "정산" in hit["excerpt"] for hit in closeout)


def test_receivable_questions_find_documents_fees_and_nonpayment_repurchase_context(
    local_store: ChromaProductVectorStore,
) -> None:
    answers = {
        question.query_id: local_store.search(
            question.text, ["SUPPLIER_PAYMENT"], allowed_product_ids=[RECEIVABLES], top_k=3
        )
        for question in POLICIES["SUPPLIER_PAYMENT"].search_questions
        if question.receivables_only
    }
    conditions = answers["receivable_purchase_conditions"]
    assert any("수락" in hit["excerpt"] for hit in conditions)
    documents = answers["receivable_required_documents"]
    assert any("선하증권" in hit["excerpt"] for hit in documents)
    assert any("서류" in hit["excerpt"] and "제출" in hit["excerpt"] for hit in documents)
    fees = answers["receivable_fees"]
    assert any("할인수수료" in hit["excerpt"] for hit in fees)
    assert any("조정수수료" in hit["excerpt"] for hit in fees)
    recourse = answers["receivable_repurchase_obligation"]
    assert any(
        "환매" in hit["excerpt"]
        and "유예기간" in hit["excerpt"]
        and "결제를 하지 않은" in hit["excerpt"]
        for hit in recourse
    ), "Nonpayment exception and governing repurchase obligation must share context"


@pytest.mark.parametrize("receivable_confirmed", [False, True])
def test_receivable_sources_are_gated_by_confirmed_export_input(
    local_store: ChromaProductVectorStore, receivable_confirmed: bool
) -> None:
    event = PortfolioFinancialEvent(
        event_id="supplier",
        scenario_code="SUPPLIER_PAYMENT",
        event_name="공급자 지급",
        event_date=date(2026, 10, 19),
        amount=15_000_000,
        currency="KRW",
        link_status="CONFIRMED",
        payment_purpose="DOMESTIC",
    )
    request = PortfolioRunRequest(
        trade_direction="EXPORT",
        export_receivable_confirmed=receivable_confirmed,
        receipt_currency="USD",
        expected_receipt_date=date(2026, 10, 21),
        financial_events=[event],
    )
    conflict = PortfolioConflictResult(
        event_id=event.event_id,
        scenario_code=event.scenario_code,
        event_name=event.event_name,
        status="CONFLICT",
        event_date=event.event_date,
        expected_receipt_date=request.expected_receipt_date,
        gap_days=2,
        reason="입금 예정일보다 공급자 지급일이 2일 빠릅니다.",
    )
    retrieval = FinancialRetrieval(store=local_store, catalog=ProductCatalog.load())
    evidence, warnings = retrieval.search(request, [conflict])
    assert evidence and not warnings
    source_ids = {hit["product_id"] for hit in evidence}
    assert SHINHAN in source_ids
    assert (RECEIVABLES in source_ids) is receivable_confirmed
    assert ARCHIVED_COST_GUIDE not in source_ids
    traces = retrieval.last_search_trace
    assert all(trace["page_filter"] is None and trace["topic_filter"] is None for trace in traces)
    assert all(
        trace["unique_candidate_count"] > 3 for trace in traces if trace["status"] == "SEARCHED"
    )
    if not receivable_confirmed:
        assert all(RECEIVABLES not in trace["allowed_source_ids"] for trace in traces)
        assert any(trace["status"] == "SKIPPED" for trace in traces)


def test_no_conflict_does_not_search_even_when_a_receivable_is_confirmed(
    local_store: ChromaProductVectorStore,
) -> None:
    request = PortfolioRunRequest(trade_direction="EXPORT", export_receivable_confirmed=True)
    conflict = PortfolioConflictResult(
        event_id="not-due",
        scenario_code="SUPPLIER_PAYMENT",
        event_name="공급자 지급",
        status="NO_CONFLICT",
        reason="입금일 이후 지급 예정입니다.",
    )
    retrieval = FinancialRetrieval(store=local_store, catalog=ProductCatalog.load())
    assert retrieval.search(request, [conflict]) == ([], [])
    assert retrieval.last_search_trace == []

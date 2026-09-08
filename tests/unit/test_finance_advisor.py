from __future__ import annotations

from typing import Any

import pytest

from app.agents.finance_advisor import FinanceAdvisorAgent
from app.schemas.portfolio import PortfolioRunRequest
from app.services.financial_retrieval import FinancialRetrieval
from app.services.llm_controller import RunModel
from app.services.product_catalog import ProductCatalog
from tests.helpers import ScriptedModel


def catalog() -> ProductCatalog:
    products = []
    for pid, directions, import_payment, receivable in [
        ("WORKING", ["EXPORT", "IMPORT"], False, False),
        ("USANCE", ["IMPORT"], True, False),
        ("FACTORING", ["EXPORT"], False, True),
    ]:
        products.append(
            {
                "product_id": pid,
                "canonical_name": pid,
                "bank_name": "TEST",
                "source_file": pid + ".pdf",
                "source_url": "https://example.com/" + pid,
                "reviewed_on": "2026-09-06",
                "reviewed_sha256": "a" * 64,
                "review_status": "REVIEWED",
                "enabled": True,
                "selection_reason": "synthetic test source, not a real product",
                "scenario_codes": ["SUPPLIER_PAYMENT"],
                "trade_directions": directions,
                "requires_import_payment": import_payment,
                "requires_export_receivable": receivable,
                "include_pages": [2],
                "sections": [
                    {
                        "section_id": "test-purpose",
                        "topic": "FUNDING_PURPOSE",
                        "page": 2,
                        "start_anchor": "TEST:",
                        "end_anchor": "END",
                    }
                ],
            }
        )
    return ProductCatalog.model_validate(
        {"schema_version": "kb-product-catalog-v3", "products": products}
    )


def request(**changes: Any) -> PortfolioRunRequest:
    return PortfolioRunRequest.model_validate(
        {
            "trade_direction": "IMPORT",
            "receipt_currency": "USD",
            "expected_receipt_date": "2026-09-20",
            "financial_events": [
                {
                    "event_id": "PAY-1",
                    "scenario_code": "SUPPLIER_PAYMENT",
                    "event_name": "supplier",
                    "event_date": "2026-09-15",
                    "link_status": "CONFIRMED",
                    "payment_purpose": "IMPORT",
                }
            ],
            **changes,
        }
    )


class FakeStore:
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []
        self.hit_changes: dict[str, Any] = {}

    def search(self, query: str, scenario_codes: Any, **kwargs: Any) -> list[dict[str, Any]]:
        self.calls.append({"query": query, "scenarios": scenario_codes, **kwargs})
        return [
            {
                "product_id": "USANCE",
                "product_name": "Untrusted name",
                "scenario_code": "SUPPLIER_PAYMENT",
                "source_file": "USANCE.pdf",
                "page": 2,
                "source_sha256": "a" * 64,
                "excerpt": "TEST: 수입거래용 자료. 대상 조건은 은행 확인 필요.",
                "chunk_id": "chunk-1",
                "score": 0.8,
                "topic": "FUNDING_PURPOSE",
                "section_id": "test-purpose",
                **self.hit_changes,
            }
        ]


def submission(*, evidence_id: str | None = "ref-1", **slots: Any) -> tuple[str, dict[str, Any]]:
    return "choose_financial_evidence", {
        "selections": {"funding_conditions": evidence_id, "funding_costs": evidence_id, **slots}
    }


def explanation(
    text: str | None = "이 자료에서는 수입거래의 대상 조건을 은행에 확인하도록 안내합니다.",
) -> tuple[str, dict[str, Any]]:
    return "explain_financial_evidence", {"text": text}


class EvidenceRetrieval:
    def __init__(self, evidence: list[dict[str, Any]]) -> None:
        self.evidence = evidence

    def search(self, *args: Any) -> tuple[list[dict[str, Any]], list[str]]:
        return self.evidence, []


def evidence_hit(**changes: Any) -> dict[str, Any]:
    return {
        "citation_id": "ref-1",
        "event_id": "PAY-1",
        "product_id": "USANCE",
        "product_name": "TEST SOURCE",
        "financial_institution": "TEST BANK",
        "source_file": "USANCE.pdf",
        "source_url": "https://example.com/test.pdf",
        "source_sha256": "a" * 64,
        "chunk_id": "chunk-1",
        "page": 2,
        "excerpt": "TEST: 수입거래용 자료의 대상 조건은 은행 확인이 필요합니다.",
        "query_ids": ["funding_conditions", "funding_costs"],
        "retrieval_queries": ["지급자금의 조건과 증빙은 무엇인가?"],
        **changes,
    }


def run_with_evidence(
    evidence: list[dict[str, Any]],
    actions: list[Any],
) -> tuple[Any, Any, Any]:
    from typing import cast

    fake = ScriptedModel(["compare_financial_dates", "search_financial_documents", *actions])
    result = FinanceAdvisorAgent(
        retrieval=cast(FinancialRetrieval, EvidenceRetrieval(evidence))
    ).run(request(), [], model=RunModel(fake, 20))
    assert not fake.actions
    return result


def test_rag_resolves_names_pages_and_runs_selection_then_explicit_individual_writers() -> None:
    store = FakeStore()
    fake = ScriptedModel(
        [
            "compare_financial_dates",
            "search_financial_documents",
            submission(),
            explanation(),
            explanation(),
        ]
    )
    result, options, warnings = FinanceAdvisorAgent(
        retrieval=FinancialRetrieval(store=store, catalog=catalog())
    ).run(request(), [], model=RunModel(fake, 20))
    assert result[0].gap_days == 5
    assert options[0].product_name == "USANCE"
    assert options[0].citations[0].page == 2
    assert options[0].conditions_to_check
    assert len(options[0].explanation_points) == 2
    assert not warnings
    assert all(c["allowed_product_ids"] == ["WORKING", "USANCE"] for c in store.calls)
    assert all(c["top_k"] == 3 and "topics" not in c for c in store.calls)
    assert len(fake.requests) == 5


def test_selection_schema_allows_only_question_specific_source_ids_or_null() -> None:
    import jsonschema

    from app.agents.finance_advisor import selection_tool
    from app.services.service_policy import get_answer_slots

    slots = get_answer_slots("SUPPLIER_PAYMENT", export_receivable_confirmed=False)
    hits = [
        evidence_hit(query_ids=["funding_conditions"]),
        evidence_hit(citation_id="ref-2", query_ids=["funding_costs"]),
    ]
    schema = selection_tool(hits, slots)["parameters"]
    jsonschema.validate(submission(funding_costs="ref-2")[1], schema)
    jsonschema.validate(submission(evidence_id=None)[1], schema)
    for data in (
        submission(evidence_id="invented")[1],
        submission()[1],
        {
            **submission(funding_costs="ref-2")[1],
            "text": "선택 단계에서는 설명을 생성할 수 없습니다.",
        },
    ):
        with pytest.raises(jsonschema.ValidationError):
            jsonschema.validate(data, schema)


def test_explanation_schema_cannot_change_source_or_add_uncited_advice_fields() -> None:
    import jsonschema

    from app.agents.finance_advisor import EvidenceExplanation

    schema = EvidenceExplanation.model_json_schema()
    jsonschema.validate(explanation()[1], schema)
    jsonschema.validate(explanation(None)[1], schema)
    for changes in (
        {"evidence_id": "ref-other"},
        {"warnings": ["추가 금융 조언"]},
        {"supporting_quote": "조작된 원문"},
        {"text": "가" * 221},
    ):
        with pytest.raises(jsonschema.ValidationError):
            jsonschema.validate({**explanation()[1], **changes}, schema)


def test_selection_context_exposes_full_paragraphs_and_question_source_ranges() -> None:
    from app.agents.finance_advisor import selection_context
    from app.services.service_policy import get_answer_slots

    hit = evidence_hit(excerpt="■ 상위 조건이 충족되는 경우 • 해당 조건에 따라 의무가 발생합니다.")
    context = selection_context(
        [hit], get_answer_slots("SUPPLIER_PAYMENT", export_receivable_confirmed=False)
    )
    assert context["evidence"][0]["excerpt"] == hit["excerpt"]
    assert context["questions"][0]["evidence_ids"] == ["ref-1"]


def test_writer_sees_only_one_question_one_source_and_no_identifier_or_history() -> None:
    import json
    from typing import cast

    fake = ScriptedModel(
        [
            "compare_financial_dates",
            "search_financial_documents",
            submission(funding_costs="ref-2"),
            explanation(),
            explanation(),
        ]
    )
    first = evidence_hit(query_ids=["funding_conditions"])
    second = evidence_hit(
        citation_id="ref-2",
        excerpt="두 번째 자료에서는 해당 거래의 비용을 확인하도록 안내합니다.",
        query_ids=["funding_costs"],
    )
    _, options, _ = FinanceAdvisorAgent(
        retrieval=cast(FinancialRetrieval, EvidenceRetrieval([first, second]))
    ).run(request(), [], model=RunModel(fake, 20))
    writers = [r for r in fake.requests if r["forced_tool"] == "explain_financial_evidence"]
    assert len(writers) == 2
    for writer, hit in zip(writers, [first, second], strict=True):
        assert len(writer["messages"]) == 1
        context = json.loads(writer["messages"][0]["content"])
        assert set(context) == {"question", "source_text"}
        assert context["source_text"] == hit["excerpt"]
    assert {c.citation_id for c in options[0].citations} == {"ref-1", "ref-2"}


@pytest.mark.parametrize("changes", [{"chunk_id": None}, {"page": 0}, {"excerpt": ""}])
def test_incomplete_search_hits_are_rejected(changes: dict[str, Any]) -> None:
    store = FakeStore()
    store.hit_changes = changes
    fake = ScriptedModel(["compare_financial_dates", "search_financial_documents"])
    _, options, warnings = FinanceAdvisorAgent(
        retrieval=FinancialRetrieval(store=store, catalog=catalog())
    ).run(request(), [], model=RunModel(fake, 20))
    assert not options and warnings


def test_unknown_evidence_id_is_rejected_without_requesting_explanation() -> None:
    _, options, warnings = run_with_evidence([evidence_hit()], [submission(evidence_id="invented")])
    assert not options and any("근거 ID" in warning for warning in warnings)


def test_selection_abstention_never_calls_writer() -> None:
    _, options, warnings = run_with_evidence([evidence_hit()], [submission(evidence_id=None)])
    assert not options and warnings


def test_explanation_abstention_is_not_replaced_by_unverified_draft() -> None:
    _, options, warnings = run_with_evidence(
        [evidence_hit()], [submission(), explanation(None), explanation(None)]
    )
    assert not options
    assert any("선택된 본문에서 확인하지 못했습니다" in warning for warning in warnings)


def test_selected_source_cannot_be_changed_by_writer() -> None:
    _, options, warnings = run_with_evidence(
        [evidence_hit()],
        [
            submission(),
            ("explain_financial_evidence", {**explanation()[1], "evidence_id": "OTHER"}),
            explanation(),
        ],
    )
    assert [p.slot_id for p in options[0].explanation_points] == ["funding_costs"]
    assert any("설명 응답 형식이 잘못" in warning for warning in warnings)
    assert all(c.citation_id == "ref-1" for c in options[0].citations)


def test_full_source_preserves_conditions_symbols_and_whitespace() -> None:
    original = (
        "▣ 유예기간 마지막 날까지 지급이 없으면 • 은행 요청에 따라 수수 료와 함께 환매합니다."
    )
    _, options, warnings = run_with_evidence(
        [evidence_hit(excerpt=original)],
        [
            submission(),
            explanation(),
            explanation(),
        ],
    )
    assert not warnings
    assert options[0].supporting_quote == original
    assert all(p.supporting_quote == original for p in options[0].explanation_points)


def test_selected_bank_and_page_are_server_pinned() -> None:
    selected = evidence_hit(
        citation_id="ref-2",
        product_id="SECOND",
        product_name="SECOND SOURCE",
        financial_institution="OTHER BANK",
        page=5,
    )
    _, options, warnings = run_with_evidence(
        [evidence_hit(), selected],
        [
            submission(evidence_id="ref-2"),
            explanation(),
            explanation(),
        ],
    )
    assert not warnings
    assert options[0].product_id == "SECOND" and options[0].financial_institution == "OTHER BANK"
    assert {c.page for c in options[0].citations} == {5}


def test_selector_cannot_skip_required_question_keys() -> None:
    answer = submission()
    del answer[1]["selections"]["funding_costs"]
    _, options, warnings = run_with_evidence([evidence_hit()], [answer])
    assert not options and any("선택 형식" in warning for warning in warnings)


def test_cross_question_reference_is_removed_but_other_question_remains() -> None:
    _, options, warnings = run_with_evidence(
        [
            evidence_hit(query_ids=["funding_conditions"]),
            evidence_hit(citation_id="ref-2", query_ids=["funding_costs"]),
        ],
        [submission(evidence_id="ref-2"), explanation()],
    )
    assert [p.slot_id for p in options[0].explanation_points] == ["funding_costs"]
    assert any("질문에 연결되지 않은 근거" in warning for warning in warnings)


def test_one_writer_api_failure_preserves_another_answer_without_retries() -> None:
    from app.services.llm_controller import LLMError

    _, options, warnings = run_with_evidence(
        [evidence_hit()],
        [
            submission(),
            LLMError("synthetic writer failure"),
            explanation(),
        ],
    )
    assert [p.slot_id for p in options[0].explanation_points] == ["funding_costs"]
    assert any("설명 생성 호출이 실패" in warning for warning in warnings)
    assert not any("근거가 부족" in warning for warning in warnings)


def test_selection_api_failure_never_calls_writers() -> None:
    from app.services.llm_controller import LLMError

    _, options, warnings = run_with_evidence(
        [evidence_hit()], [LLMError("synthetic selector failure")]
    )
    assert not options
    assert any("근거 선택 호출이 실패" in warning for warning in warnings)


def test_programming_error_is_not_hidden_as_provider_failure() -> None:
    with pytest.raises(RuntimeError, match="programming error"):
        run_with_evidence([evidence_hit()], [submission(), RuntimeError("programming error")])


@pytest.mark.parametrize("receipt", ["2026-09-10", "2026-09-15", None])
def test_no_confirmed_conflict_does_not_search(receipt: str | None) -> None:
    store = FakeStore()
    fake = ScriptedModel(["compare_financial_dates"])
    _, options, _ = FinanceAdvisorAgent(
        retrieval=FinancialRetrieval(store=store, catalog=catalog())
    ).run(request(expected_receipt_date=receipt), [], model=RunModel(fake, 20))
    assert not options and not store.calls


def test_catalog_blocks_domestic_usance_and_unconfirmed_export_receivable() -> None:
    req = request(trade_direction="EXPORT")
    req.financial_events[0].payment_purpose = "DOMESTIC"
    assert [p.product_id for p in catalog().eligible(req, req.financial_events[0])] == ["WORKING"]
    req.export_receivable_confirmed = True
    assert [p.product_id for p in catalog().eligible(req, req.financial_events[0])] == [
        "WORKING",
        "FACTORING",
    ]


def test_fx_missing_source_never_falls_back_to_fx_loan() -> None:
    req = request()
    req.financial_events[0].scenario_code = "FX_FORWARD_MATURITY"
    req.financial_events[0].currency = "USD"
    req.financial_events[0].fx_direction = "SELL"
    store = FakeStore()
    fake = ScriptedModel(["compare_financial_dates", "search_financial_documents"])
    _, options, warnings = FinanceAdvisorAgent(
        retrieval=FinancialRetrieval(store=store, catalog=catalog())
    ).run(req, [], model=RunModel(fake, 20))
    assert not options and warnings and not store.calls


def test_stale_source_is_rejected() -> None:
    store = FakeStore()
    store.hit_changes = {"source_sha256": "b" * 64}
    fake = ScriptedModel(["compare_financial_dates", "search_financial_documents"])
    _, options, warnings = FinanceAdvisorAgent(
        retrieval=FinancialRetrieval(store=store, catalog=catalog())
    ).run(request(), [], model=RunModel(fake, 20))
    assert not options and warnings


def test_pending_source_cannot_be_enabled() -> None:
    data = catalog().model_dump()
    data["products"][0]["review_status"] = "PENDING"
    with pytest.raises(ValueError):
        ProductCatalog.model_validate(data)


def test_malformed_selection_preserves_previous_event_and_hides_provider_payload() -> None:
    from typing import cast

    from app.schemas.portfolio import PortfolioFinancialEvent

    req = request()
    req.financial_events.append(
        PortfolioFinancialEvent.model_validate(
            {
                **req.financial_events[0].model_dump(),
                "event_id": "PAY-2",
            }
        )
    )
    fake = ScriptedModel(
        [
            "compare_financial_dates",
            "search_financial_documents",
            submission(),
            explanation(),
            explanation(),
            ("choose_financial_evidence", {"selections": "synthetic-private-provider-payload"}),
        ]
    )
    runtime = RunModel(fake, 20)
    _, options, warnings = FinanceAdvisorAgent(
        retrieval=cast(
            FinancialRetrieval,
            EvidenceRetrieval(
                [
                    evidence_hit(),
                    evidence_hit(event_id="PAY-2", citation_id="ref-2"),
                ]
            ),
        ),
    ).run(req, [], model=runtime)
    assert [o.event_id for o in options] == ["PAY-1"]
    assert any("PAY-2" in warning and "응답 형식" in warning for warning in warnings)
    assert "synthetic-private-provider-payload" not in " ".join(warnings)
    assert runtime.audit[-1].status == "REJECTED"
    assert not fake.actions


def test_malformed_explanation_preserves_other_question_and_hides_provider_payload() -> None:
    from typing import cast

    fake = ScriptedModel(
        [
            "compare_financial_dates",
            "search_financial_documents",
            submission(),
            ("explain_financial_evidence", {"text": {"raw": "synthetic-private-provider-payload"}}),
            explanation(),
        ]
    )
    runtime = RunModel(fake, 20)
    _, options, warnings = FinanceAdvisorAgent(
        retrieval=cast(FinancialRetrieval, EvidenceRetrieval([evidence_hit()])),
    ).run(request(), [], model=runtime)
    assert [p.slot_id for p in options[0].explanation_points] == ["funding_costs"]
    assert "synthetic-private-provider-payload" not in " ".join(warnings)
    assert any(
        a.status == "REJECTED" and a.tool == "explain_financial_evidence" for a in runtime.audit
    )
    assert not fake.actions


@pytest.mark.parametrize(
    "reference", ["아래 각호", "아래의 각호", "다음 각 호", "다음의 각호", "위 각호", "위의 각 호"]
)
def test_unresolved_list_reference_abstains_without_retry_or_disclosing_answer(
    reference: str,
) -> None:
    from typing import cast

    unsafe_text = f"{reference}의 조건에 따라 처리합니다. synthetic-private-answer"
    fake = ScriptedModel(
        [
            "compare_financial_dates",
            "search_financial_documents",
            submission(),
            explanation(unsafe_text),
            explanation(),
        ]
    )
    runtime = RunModel(fake, 20)
    _, options, warnings = FinanceAdvisorAgent(
        retrieval=cast(FinancialRetrieval, EvidenceRetrieval([evidence_hit()])),
    ).run(request(), [], model=runtime)
    assert [p.slot_id for p in options[0].explanation_points] == ["funding_costs"]
    assert any("원문 목록에 의존" in warning for warning in warnings)
    assert "synthetic-private-answer" not in " ".join(warnings)
    assert any(a.status == "REJECTED" and "목록 참조" in a.message for a in runtime.audit)
    assert runtime.calls == 5 and not fake.actions


def test_clause_number_alone_is_not_an_unresolved_list_reference() -> None:
    source = "3.01 조항의 조건이 충족되면 은행의 요청에 따라 해당 절차를 진행합니다."
    _, options, warnings = run_with_evidence(
        [evidence_hit(excerpt=source)],
        [submission(), explanation(source), explanation(source)],
    )
    assert not warnings
    assert len(options[0].explanation_points) == 2


def test_source_list_reference_is_allowed_when_standalone_explanation_names_condition() -> None:
    source = "아래의 각호에 따라 처리합니다. (1) 약정 조건이 충족되는 경우 은행 요청으로 절차를 진행합니다."
    text = "약정 조건이 충족되는 경우 은행 요청으로 해당 절차를 진행합니다."
    _, options, warnings = run_with_evidence(
        [evidence_hit(excerpt=source)],
        [submission(), explanation(text), explanation(text)],
    )
    assert not warnings
    assert len(options[0].explanation_points) == 2
    assert options[0].supporting_quote == source

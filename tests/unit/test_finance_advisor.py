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


def submission(**changes: Any) -> tuple[str, dict[str, Any]]:
    return (
        "submit_financial_review",
        {
            "options": [
                {
                    "event_id": "PAY-1",
                    "product_id": "USANCE",
                    "supporting_quote": "TEST: 수입거래용 자료. 대상 조건은 은행 확인 필요.",
                    "citation_ids": ["ref-1"],
                    **changes,
                }
            ],
            "warnings": [],
        },
    )


def test_rag_generation_resolves_names_and_pages_from_retrieval() -> None:
    store = FakeStore()
    fake = ScriptedModel(["compare_financial_dates", "search_financial_documents", submission()])
    result, options, warnings = FinanceAdvisorAgent(
        retrieval=FinancialRetrieval(store=store, catalog=catalog())
    ).run(request(), [], model=RunModel(fake, 12))
    assert result[0].gap_days == 5
    assert options[0].product_name == "USANCE"
    assert options[0].citations[0].page == 2
    assert options[0].conditions_to_check
    assert warnings == []
    assert [c["allowed_product_ids"] for c in store.calls] == [["WORKING"], ["USANCE"]]
    assert all(c["top_k"] == 1 and c["topics"] == ["FUNDING_PURPOSE"] for c in store.calls)
    assert "TEST:" in str(fake.requests[-1]["messages"])


def test_review_schema_only_allows_matching_retrieved_identifiers() -> None:
    import jsonschema

    from app.agents.finance_advisor import review_tool

    evidence = [
        {"event_id": "PAY-1", "product_id": "WORKING", "citation_id": "ref-1"},
        {"event_id": "PAY-1", "product_id": "USANCE", "citation_id": "ref-2"},
        {"event_id": "PAY-2", "product_id": "USANCE", "citation_id": "ref-3"},
    ]
    schema = review_tool(evidence)["parameters"]
    valid = submission(citation_ids=["ref-2"])[1]
    valid["options"] = [
        submission(product_id="WORKING", citation_ids=["ref-1"])[1]["options"][0],
        submission(citation_ids=["ref-2"])[1]["options"][0],
        submission(event_id="PAY-2", citation_ids=["ref-3"], supporting_quote=None)[1]["options"][
            0
        ],
    ]
    jsonschema.validate(valid, schema)
    for changes in (
        {"citation_ids": ["ref-1"]},
        {"citation_ids": ["ref-3"]},
        {"citation_ids": ["invented"]},
        {"product_id": "invented", "citation_ids": ["ref-2"]},
    ):
        with pytest.raises(jsonschema.ValidationError):
            jsonschema.validate(
                {
                    "options": [
                        valid["options"][0],
                        submission(**changes)[1]["options"][0],
                        valid["options"][2],
                    ],
                    "warnings": [],
                },
                schema,
            )


def test_compact_context_keeps_full_text_without_repeated_source_metadata() -> None:
    from app.agents.finance_advisor import review_context

    fake = ScriptedModel(["compare_financial_dates", "search_financial_documents", submission()])
    retrieval = FinancialRetrieval(store=FakeStore(), catalog=catalog())
    conflicts, _, _ = FinanceAdvisorAgent(retrieval=retrieval).run(
        request(), [], model=RunModel(fake, 12)
    )
    evidence, _ = retrieval.search(request(), conflicts)
    extra = {**evidence[0], "citation_id": "ref-2"}
    compact = review_context([*evidence, extra])
    assert len(compact["candidates"]) == 1
    assert len(compact["evidence"]) == 2
    assert compact["evidence"][0]["excerpt"] == evidence[0]["excerpt"]
    assert "source_sha256" not in compact["evidence"][0]
    assert "selection_reason" not in compact["evidence"][0]


@pytest.mark.parametrize("changes", [{"chunk_id": None}, {"page": 0}, {"excerpt": ""}])
def test_incomplete_search_hits_are_rejected(changes: dict[str, Any]) -> None:
    store = FakeStore()
    store.hit_changes = changes
    fake = ScriptedModel(["compare_financial_dates", "search_financial_documents"])
    _, options, warnings = FinanceAdvisorAgent(
        retrieval=FinancialRetrieval(store=store, catalog=catalog())
    ).run(request(), [], model=RunModel(fake, 12))
    assert options == [] and warnings


@pytest.mark.parametrize(
    "changes", [{"citation_ids": ["invented"]}, {"event_id": "OTHER"}, {"product_id": "FACTORING"}]
)
def test_forged_citation_or_cross_event_link_is_removed(changes: dict[str, Any]) -> None:
    fake = ScriptedModel(
        ["compare_financial_dates", "search_financial_documents", submission(**changes)]
    )
    _, options, warnings = FinanceAdvisorAgent(
        retrieval=FinancialRetrieval(store=FakeStore(), catalog=catalog())
    ).run(request(), [], model=RunModel(fake, 12))
    assert options == [] and warnings


@pytest.mark.parametrize("receipt", ["2026-09-10", "2026-09-15", None])
def test_no_confirmed_conflict_does_not_search(receipt: str | None) -> None:
    store = FakeStore()
    fake = ScriptedModel(["compare_financial_dates"])
    _, options, _ = FinanceAdvisorAgent(
        retrieval=FinancialRetrieval(store=store, catalog=catalog())
    ).run(request(expected_receipt_date=receipt), [], model=RunModel(fake, 12))
    assert options == [] and store.calls == []


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
    ).run(req, [], model=RunModel(fake, 12))
    assert options == [] and warnings and store.calls == []


def test_stale_source_is_rejected() -> None:
    store = FakeStore()
    store.hit_changes = {"source_sha256": "b" * 64}
    fake = ScriptedModel(["compare_financial_dates", "search_financial_documents"])
    _, options, warnings = FinanceAdvisorAgent(
        retrieval=FinancialRetrieval(store=store, catalog=catalog())
    ).run(request(), [], model=RunModel(fake, 12))
    assert options == [] and warnings


def test_pending_source_cannot_be_enabled() -> None:
    data = catalog().model_dump()
    data["products"][0]["review_status"] = "PENDING"
    with pytest.raises(ValueError):
        ProductCatalog.model_validate(data)

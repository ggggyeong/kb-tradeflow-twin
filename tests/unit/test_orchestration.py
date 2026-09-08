from __future__ import annotations

from pathlib import Path
from typing import Any, cast

import pytest

from app.agents.document_agent import DocumentAgent
from app.agents.supervisor import Supervisor
from app.graphs.compiled import build_portfolio_graph
from app.schemas.orchestration import ExecutionPlan
from app.schemas.portfolio import PortfolioRunRequest
from app.services.llm_controller import LLMError, LLMSettings, RunModel
from app.services.portfolio_pipeline import PortfolioPipeline
from tests.helpers import FULL_PLAN, ScriptedModel
from tests.integration.test_portfolio_pipeline import StubDocumentAgent

DOC_PLAN = {"status": "READY", "steps": ["call_document_agent"], "reason": "문서만 분석"}


def run(actions: list[Any], tmp_path: Path, **dependencies: Any) -> tuple[Any, ScriptedModel]:
    fake = ScriptedModel(actions)
    dependencies.setdefault("document_agent", cast(DocumentAgent, StubDocumentAgent()))
    pipeline = PortfolioPipeline(model=fake, **dependencies)
    payload = {
        "document_paths": [tmp_path / "invoice.pdf"],
        "financial_events": [
            {
                "event_id": "p1",
                "scenario_code": "SUPPLIER_PAYMENT",
                "event_name": "supplier",
                "event_date": "2026-09-15",
            }
        ],
        "report_output_path": tmp_path / "report.pdf",
    }
    state = build_portfolio_graph(pipeline).invoke({"request": payload})
    return pipeline.result(state), fake


def test_document_only_does_not_call_finance_or_report(tmp_path: Path) -> None:
    result, _ = run([DOC_PLAN, "call_document_agent"], tmp_path)
    assert result.status == "SUCCESS"
    assert result.report_path is None and result.conflicts == []
    assert result.llm_calls == 2


@pytest.mark.parametrize("status", ["NEEDS_INPUT", "OUT_OF_SCOPE"])
def test_supervisor_can_stop_without_any_tool(tmp_path: Path, status: str) -> None:
    result, _ = run([{"status": status, "steps": [], "reason": "확인 필요"}], tmp_path)
    assert result.status == "REVIEW_REQUIRED" and result.report_path is None
    assert result.llm_calls == 1


@pytest.mark.parametrize(
    "steps",
    [
        ["call_document_agent", "call_document_agent"],
        ["arbitrary_shell"],
        ["generate_report"],
        ["generate_report", "call_document_agent"],
        ["call_finance_agent", "call_document_agent"],
    ],
)
def test_invalid_plans_are_rejected(steps: list[str]) -> None:
    with pytest.raises(ValueError):
        ExecutionPlan.model_validate({"status": "READY", "steps": steps, "reason": "test"})


def test_plan_cannot_invent_missing_documents() -> None:
    with pytest.raises(ValueError, match="문서 없는"):
        Supervisor().plan(PortfolioRunRequest(), RunModel(ScriptedModel([DOC_PLAN]), 12))


@pytest.mark.parametrize(
    "bad",
    [
        "generate_report",
        "call_finance_agent",
        "shell",
        ("call_document_agent", {"path": "/etc/passwd"}),
    ],
)
def test_allowlist_order_and_arguments_are_enforced(tmp_path: Path, bad: Any) -> None:
    result, _ = run([DOC_PLAN, bad, "call_document_agent"], tmp_path)
    assert [a.status for a in result.tool_audit][-2:] == ["REJECTED", "SUCCESS"]
    assert result.status == "REVIEW_REQUIRED"


def test_call_budget_stops_without_writing_report(tmp_path: Path) -> None:
    result, fake = run(
        [FULL_PLAN, "generate_report", "generate_report"],
        tmp_path,
        settings=LLMSettings(max_calls=3),
    )
    assert result.status == "FAILED" and result.llm_calls == len(fake.requests) == 3
    assert result.report_path is None


def test_nested_agent_calls_share_budget(tmp_path: Path) -> None:
    # Real document agent needs another call to read the PDF; global budget prevents it.
    result, fake = run(
        [DOC_PLAN, "call_document_agent"],
        tmp_path,
        settings=LLMSettings(max_calls=2),
        document_agent=DocumentAgent(),
    )
    assert result.status == "FAILED" and result.llm_calls == len(fake.requests) == 2


def test_failure_is_not_hidden(tmp_path: Path) -> None:
    class FailingDocs:
        def run(self, *args: Any, **kwargs: Any) -> Any:
            raise RuntimeError("secret-path")

    result, _ = run([DOC_PLAN, "call_document_agent"], tmp_path, document_agent=FailingDocs())
    assert result.status == "FAILED"
    assert "secret-path" not in result.model_dump_json()


def test_llm_error_has_no_fixed_pipeline_fallback(tmp_path: Path) -> None:
    result, _ = run([LLMError("unavailable")], tmp_path)
    assert result.status == "FAILED" and result.documents == []


def test_tool_outputs_and_reasoning_preserved(tmp_path: Path) -> None:
    plan = {**DOC_PLAN, "steps": ["call_document_agent", "generate_report"]}
    result, fake = run([plan, "call_document_agent", "generate_report"], tmp_path)
    messages = fake.requests[-1]["messages"]
    assert any(m.get("type") == "function_call_output" for m in messages)
    assert any(m.get("type") == "reasoning" for m in messages)
    assert str(tmp_path) not in str(fake.requests)
    assert result.input_tokens == 60


def test_requests_do_not_share_runtime(tmp_path: Path) -> None:
    fake = ScriptedModel([DOC_PLAN, "call_document_agent", DOC_PLAN, "call_document_agent"])
    pipeline = PortfolioPipeline(
        model=fake, document_agent=cast(DocumentAgent, StubDocumentAgent())
    )
    graph = build_portfolio_graph(pipeline)
    for _ in range(2):
        result = pipeline.result(
            graph.invoke({"request": {"document_paths": [tmp_path / "a.pdf"]}})
        )
        assert result.llm_calls == 2 and len(result.documents) == 1


def test_default_budget_wins_over_graph_recursion_limit(tmp_path: Path) -> None:
    result, fake = run([DOC_PLAN, *(["generate_report"] * 19)], tmp_path)
    assert result.status == "FAILED" and result.llm_calls == 20
    assert len(fake.requests) == 20

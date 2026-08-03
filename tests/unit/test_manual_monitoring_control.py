from __future__ import annotations

from types import SimpleNamespace
from typing import Any, cast

import pytest

from app.agents import report_writer
from app.agents.planning import TEAM_AND_TOOL_CATALOG
from app.agents.supervisor import TOOL_OWNERS
from app.graphs import common_control
from app.graphs.state import TradeFlowState
from app.tools.reports import _rank_monitoring_results, finalize_manual_monitoring


def test_manual_monitoring_finalizer_is_registered_to_report_writer(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, Any] = {}

    def fake_create_agent(**kwargs: Any) -> object:
        captured.update(kwargs)
        return object()

    monkeypatch.setattr(report_writer, "langchain_create_agent", fake_create_agent)
    report_writer.build_agent(model=cast(Any, object()))

    assert TOOL_OWNERS[finalize_manual_monitoring.name] == "report_writer"
    assert finalize_manual_monitoring.name in {tool.name for tool in captured["tools"]}
    report_writer_catalog = TEAM_AND_TOOL_CATALOG.split("- report_writer", 1)[1].split(
        "- critic", 1
    )[0]
    assert f"- {finalize_manual_monitoring.name}" in report_writer_catalog


@pytest.mark.parametrize(
    "evidence_snapshot",
    [
        pytest.param({}, id="missing-review"),
        pytest.param(
            {"evidence_review": {"verdict": "FAIL"}},
            id="non-pass-review",
        ),
    ],
)
def test_manual_monitoring_finalizer_requires_critic_pass(
    evidence_snapshot: dict[str, Any],
) -> None:
    with pytest.raises(
        ValueError,
        match="Manual monitoring requires a PASS evidence_review result",
    ):
        finalize_manual_monitoring.invoke(
            {
                "as_of_date": "2026-07-31",
                "request_id": "manual-monitoring-rejected",
                "evidence_snapshot": evidence_snapshot,
            }
        )


def test_manual_monitoring_ranking_orders_priority_and_counts_conflicts() -> None:
    ranked, summary = _rank_monitoring_results(
        {
            "p2_scan": {
                "case_id": "TRD-P2",
                "calculation_id": "CALC-P2",
                "source_kind": "MONITORING_SCAN",
                "highest_priority": "P2",
                "conflicts": [{"conflict_id": "C-2A"}, {"conflict_id": "C-2B"}],
            },
            "p1_scan": {
                "case_id": "TRD-P1",
                "calculation_id": "CALC-P1",
                "source_kind": "MONITORING_SCAN",
                "highest_priority": "P1",
                "conflicts": [{"conflict_id": "C-1"}],
            },
            "user_scenario": {
                "case_id": "TRD-SCENARIO",
                "calculation_id": "CALC-SCENARIO",
                "source_kind": "USER_REPORTED_DELAY",
                "highest_priority": "P1",
                "conflicts": [{"conflict_id": "NOT-SCHEDULED"}],
            },
        }
    )

    assert [item["case_id"] for item in ranked] == ["TRD-P1", "TRD-P2"]
    assert [item["conflict_count"] for item in ranked] == [1, 2]
    assert summary == {
        "total_ranked": 2,
        "cases_with_conflicts": 2,
        "total_conflicts": 3,
        "by_highest_priority": {"P1": 1, "P2": 1},
        "ordered_case_ids": ["TRD-P1", "TRD-P2"],
    }


def test_monitoring_final_response_uses_deterministic_summary_and_next_action(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        common_control,
        "get_settings",
        lambda: SimpleNamespace(
            tradeflow_mode="live",
            deterministic_final_answer=True,
        ),
    )

    def unexpected_model_summary(**_: Any) -> str:
        raise AssertionError("deterministic final answers must not invoke a model")

    monkeypatch.setattr(common_control, "make_final_answer", unexpected_model_summary)
    state = cast(
        TradeFlowState,
        {
            "mission_type": "PROACTIVE_MONITORING",
            "case_ids": ["TRD-P1"],
            "task_results": {
                "finalize_manual_monitoring": {
                    "ranked_risks": [
                        {
                            "case_id": "TRD-P1",
                            "highest_priority": "P1",
                            "conflicts": [{"conflict_id": "C-1"}],
                        },
                        {
                            "case_id": "TRD-P2",
                            "highest_priority": "P2",
                            "conflicts": [
                                {"conflict_id": "C-2A"},
                                {"conflict_id": "C-2B"},
                            ],
                        },
                    ]
                },
                "evidence_review": {"verdict": "PASS"},
            },
        },
    )

    result = common_control._finalize_from_planning(state)
    response = result["final_response"]

    assert response["answer"] == (
        "금일 금융위험 분석을 완료했습니다. 분석 거래 2건, "
        "충돌 3건, 최고 우선순위 P1입니다."
    )
    assert "Critic" not in response["answer"]
    assert len(response["next_actions"]) == 1
    next_action = response["next_actions"][0]
    assert next_action["id"] == "build_conflict_report"
    assert next_action["label"] == "충돌 종합보고서 생성"
    assert "TRD-P1" in next_action["message"]

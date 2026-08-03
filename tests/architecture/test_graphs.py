from __future__ import annotations

import json

import pytest

import app.graphs.compiled as compiled_graphs
from app.core.config import PROJECT_ROOT
from app.db.session import init_database
from app.graphs.common_control import ROLE_NAMES
from app.graphs.compiled import (
    root_conversation_graph,
    trade_case_graph,
)
from app.tools.reports import _rank_monitoring_results


def test_only_chat_and_trade_case_graphs_are_compiled() -> None:
    assert "CompiledStateGraph" in type(root_conversation_graph).__name__
    assert "CompiledStateGraph" in type(trade_case_graph).__name__
    assert not hasattr(compiled_graphs, "daily_monitoring_graph")
    assert not hasattr(compiled_graphs, "build_daily_monitoring_graph")

    root_nodes = set(root_conversation_graph.get_graph().nodes)
    case_nodes = set(trade_case_graph.get_graph().nodes)

    assert {
        "planning_agent",
        "supervisor_agent",
        "human_question",
        "human_confirmation",
        "structured_trade",
    } <= root_nodes
    assert {f"{role}_agent" for role in ROLE_NAMES} <= root_nodes
    assert {
        "intent_router",
        "conversation",
        "planner_prepare",
        "planner_agent",
        "planner_collect",
        "task_router",
        "agent_prepare",
        "agent_router",
        "agent_collect",
        "common_reviewer",
        "final_response",
    }.isdisjoint(root_nodes)
    assert not any(node.startswith(("demo1_", "demo2_", "demo3_", "demo4_")) for node in root_nodes)
    assert {"trade_case_supervisor", "trade_case_response"} <= case_nodes
    assert {f"{role}_agent" for role in ROLE_NAMES} <= case_nodes
    assert "human_confirmation" not in case_nodes


def test_manual_monitoring_ranking_uses_only_finance_scan_snapshots() -> None:
    task_results = {
        "scheduled_p2_one": {
            "case_id": "TRD-P2-ONE",
            "calculation_id": "CALC-P2-ONE",
            "source_kind": "MONITORING_SCAN",
            "highest_priority": "P2",
            "conflicts": [{"conflict_id": "C-1"}],
        },
        "scheduled_p1_b": {
            "case_id": "TRD-P1-B",
            "calculation_id": "CALC-P1-B",
            "source_kind": "MONITORING_SCAN",
            "highest_priority": "P1",
            "conflicts": [{"conflict_id": "C-2"}],
        },
        "user_scenario": {
            "case_id": "TRD-SCENARIO",
            "calculation_id": "CALC-SCENARIO",
            "source_kind": "USER_REPORTED_DELAY",
            "highest_priority": "P1",
            "conflicts": [
                {"conflict_id": "SHOULD-NOT-RANK-1"},
                {"conflict_id": "SHOULD-NOT-RANK-2"},
            ],
        },
        "scheduled_unranked": {
            "case_id": "TRD-UNRANKED",
            "calculation_id": "CALC-UNRANKED",
            "source_kind": "MONITORING_SCAN",
            "highest_priority": None,
            "conflicts": [],
        },
        "scheduled_p2_many": {
            "case_id": "TRD-P2-MANY",
            "calculation_id": "CALC-P2-MANY",
            "source_kind": "MONITORING_SCAN",
            "highest_priority": "P2",
            "conflicts": [
                {"conflict_id": "C-3"},
                {"conflict_id": "C-4"},
            ],
        },
        "scheduled_p1_a": {
            "case_id": "TRD-P1-A",
            "calculation_id": "CALC-P1-A",
            "source_kind": "MONITORING_SCAN",
            "highest_priority": "P1",
            "conflicts": [{"conflict_id": "C-5"}],
        },
    }

    ranked, summary = _rank_monitoring_results(task_results)

    assert [item["case_id"] for item in ranked] == [
        "TRD-P1-A",
        "TRD-P1-B",
        "TRD-P2-MANY",
        "TRD-P2-ONE",
        "TRD-UNRANKED",
    ]
    assert [item["rank"] for item in ranked] == [1, 2, 3, 4, 5]
    assert ranked[2]["conflict_count"] == 2
    assert summary == {
        "total_ranked": 5,
        "cases_with_conflicts": 4,
        "total_conflicts": 5,
        "by_highest_priority": {"P1": 2, "P2": 2, "UNRANKED": 1},
        "ordered_case_ids": [
            "TRD-P1-A",
            "TRD-P1-B",
            "TRD-P2-MANY",
            "TRD-P2-ONE",
            "TRD-UNRANKED",
        ],
    }


def test_root_conversation_returns_a_normal_greeting_for_message_only_input() -> None:
    result = root_conversation_graph.invoke({"messages": [{"role": "user", "content": "안녕"}]})
    assert result["messages"][-1].content.startswith("안녕하세요!")
    assert result["planning_log"][0]["action"] == "DIRECT_RESPONSE"
    assert "portfolio_supervisor" not in result.get("agent_results", {})


@pytest.mark.parametrize(
    ("menu", "mission_type", "issue_code"),
    [
        ("1", "CASE_LOOKUP", "CASE_LOOKUP_ID_REQUIRED"),
        ("3", "UPLOAD_ANALYSIS", "UPLOAD_BATCH_ID_REQUIRED"),
        ("4", "FINANCIAL_CALENDAR_IMPORT", "COMPANY_CONTEXT_REQUIRED"),
    ],
)
def test_numeric_menu_enters_the_expected_typed_human_step(
    menu: str,
    mission_type: str,
    issue_code: str,
) -> None:
    init_database()
    result = root_conversation_graph.invoke({"messages": [{"role": "user", "content": menu}]})

    interrupt_payload = result["__interrupt__"][0].value
    assert result["mission_type"] == mission_type
    assert interrupt_payload["issue"]["issue_code"] == issue_code


def test_studio_xray_exposes_every_agent_model_and_tools_node() -> None:
    case_xray = set(trade_case_graph.get_graph(xray=True).nodes)
    for role in ROLE_NAMES:
        assert f"{role}_agent:model" in case_xray
        assert f"{role}_agent:tools" in case_xray

    root_xray = set(root_conversation_graph.get_graph(xray=True).nodes)
    for role in ROLE_NAMES:
        assert f"{role}_agent:model" in root_xray
        assert f"{role}_agent:tools" in root_xray
    assert not any(node.startswith(("demo1_", "demo2_", "demo3_", "demo4_")) for node in root_xray)


def test_studio_graph_exposes_common_control_connections() -> None:
    graph = root_conversation_graph.get_graph()
    edges = {(edge.source, edge.target) for edge in graph.edges}

    assert ("__start__", "planning_agent") in edges
    assert ("planning_agent", "__end__") in edges
    assert ("planning_agent", "supervisor_agent") in edges
    assert ("planning_agent", "structured_trade") in edges
    assert ("supervisor_agent", "planning_agent") in edges
    assert ("supervisor_agent", "human_question") in edges
    assert ("human_question", "human_confirmation") in edges
    assert ("human_confirmation", "planning_agent") in edges
    assert ("structured_trade", "__end__") in edges

    for role in ROLE_NAMES:
        agent_node = f"{role}_agent"
        assert ("supervisor_agent", agent_node) in edges
        assert (agent_node, "supervisor_agent") in edges


def test_studio_config_exposes_only_the_chat_entrypoint() -> None:
    config = json.loads((PROJECT_ROOT / "langgraph.json").read_text())
    assert set(config["graphs"]) == {"chat"}
    assert config["graphs"]["chat"].endswith(":root_conversation_graph")

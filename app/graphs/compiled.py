from __future__ import annotations

import json
from contextlib import suppress
from datetime import date
from functools import lru_cache
from time import perf_counter
from typing import Any, cast

from langchain_core.messages import HumanMessage, RemoveMessage, ToolMessage
from langgraph.graph import END, START, StateGraph
from langgraph.graph.message import REMOVE_ALL_MESSAGES
from langgraph.types import Command

from app.agents.common import validate_native_tool_chain
from app.agents.supervisor import TOOL_OWNERS, route_current_task
from app.graphs.common_control import ROLE_NAMES, build_common_control_graph
from app.graphs.state import TradeFlowState
from app.schemas.agent import AgentResult


@lru_cache(maxsize=16)
def _native_agent(name: str) -> Any:
    """Build each Tool-owning role graph once per process."""
    if name == "document_intelligence":
        from app.agents.document_intelligence import build_agent
    elif name == "trade_case_manager":
        from app.agents.trade_case_manager import build_agent
    elif name == "financial_calendar":
        from app.agents.financial_calendar import build_agent
    elif name == "shipment_timeline":
        from app.agents.shipment_timeline import build_agent
    elif name == "financial_exposure":
        from app.agents.financial_exposure import build_agent
    elif name == "product_advisor":
        from app.agents.product_advisor import build_agent
    elif name == "report_writer":
        from app.agents.report_writer import build_agent
    elif name == "critic":
        from app.agents.critic import build_agent
    else:
        raise KeyError(name)
    return build_agent()


def _default_tool_request(name: str, state: TradeFlowState) -> dict[str, Any]:
    """Build a safe read-oriented request when a caller did not supply one."""
    case_id = (state.get("case_ids") or ["CASE-UNKNOWN"])[0]
    as_of_date = state.get("as_of_date") or date.today().isoformat()
    defaults: dict[str, dict[str, Any]] = {
        "document_intelligence": {
            "tool_name": "stage_document_intelligence",
            "args": {"batch_id": state.get("batch_id") or "BATCH-UNKNOWN"},
        },
        "trade_case_manager": {
            "tool_name": "bundle_trade_cases",
            "args": {"batch_id": state.get("batch_id", "BATCH-UNKNOWN")},
        },
        "financial_calendar": {
            "tool_name": "select_financial_monitoring_candidates",
            "args": {
                "company_id": state.get("company_id") or "COMPANY-UNKNOWN",
                "as_of_date": as_of_date,
                "limit": 100,
            },
        },
        "shipment_timeline": {
            "tool_name": "read_shipment_snapshot",
            "args": {"case_id": case_id},
        },
        "financial_exposure": {
            "tool_name": "get_financial_risk_snapshot",
            "args": {"case_id": case_id, "as_of_date": None},
        },
        "product_advisor": {
            "tool_name": "search_product_knowledge",
            "args": {
                "query": state.get("user_query", "무역금융 상품"),
                "top_k": 3,
            },
        },
        "report_writer": {
            "tool_name": "build_briefing_payload",
            "args": {"case_id": case_id},
        },
        "critic": {
            "tool_name": "review_workflow_evidence",
            "args": {
                "workflow_kind": state.get("mission_type", "TRADE_CASE"),
                "expected_task_ids": list(state.get("task_results", {})),
                "evidence_snapshot": state.get("task_results", {}),
            },
        },
    }
    return state.get("agent_inputs", {}).get(name) or defaults[name]


def _prepare_native_role(name: str, state: TradeFlowState) -> dict[str, Any]:
    """Map graph state into a private native-agent message channel."""
    request = _default_tool_request(name, state)
    return {
        "messages": [
            RemoveMessage(id=REMOVE_ALL_MESSAGES),
            HumanMessage(
                content=f"TOOL_INPUT_JSON={json.dumps(request, ensure_ascii=False)}",
                name=f"{name}_input",
            ),
        ],
        "structured_response": None,
        "agent_started_at": perf_counter(),
    }


def _collect_native_role(
    name: str,
    state: TradeFlowState,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Collect the native Tool chain and clear its private message channel."""
    raw = state.get("structured_response")
    if isinstance(raw, AgentResult):
        structured = raw.model_dump(mode="json")
    elif isinstance(raw, dict):
        structured = dict(raw)
    else:
        raise RuntimeError(f"{name} did not return AgentResult")
    messages = list(state.get("messages", []))
    structured["native_tool_chain"] = validate_native_tool_chain(messages)
    tool_outputs: list[dict[str, Any]] = []
    for message in messages:
        if not isinstance(message, ToolMessage) or message.name == "AgentResult":
            continue
        content: Any = message.content
        if isinstance(content, str):
            with suppress(json.JSONDecodeError):
                content = json.loads(content)
        tool_outputs.append(
            {
                "tool_name": message.name,
                "tool_call_id": message.tool_call_id,
                "status": str(message.status or "success"),
                "content": content,
            }
        )
    structured["called_tools"] = [
        output["tool_name"] for output in tool_outputs if output["tool_name"]
    ]
    structured["tool_outputs"] = tool_outputs
    if tool_outputs and all(str(output["status"]).lower() == "success" for output in tool_outputs):
        structured["status"] = "SUCCESS"
    started = state.get("agent_started_at", perf_counter())
    structured["timing_ms"] = round((perf_counter() - started) * 1000, 3)
    results = dict(state.get("agent_results", {}))
    results[name] = structured
    cleanup = {
        "messages": [RemoveMessage(id=REMOVE_ALL_MESSAGES)],
        "structured_response": None,
        "agent_started_at": 0.0,
    }
    return results, cleanup


def _tool_result(structured: dict[str, Any]) -> Any:
    data = structured.get("data", {})
    if isinstance(data, dict) and "tool_result" in data:
        return data["tool_result"]
    outputs = structured.get("tool_outputs", [])
    return outputs[-1].get("content") if outputs else structured.get("summary")


def _task_from_role(role: str, state: TradeFlowState, index: int) -> dict[str, Any]:
    request = _default_tool_request(role, state)
    return {
        "task_id": f"{role}_{index}",
        "description": f"Run the requested {role} Tool",
        "agent": role,
        "tool": request["tool_name"],
        "args": request["args"],
        "reason": "The standalone trade-case graph received an explicit role queue.",
    }


def _normalize_trade_case_queue(state: TradeFlowState) -> list[dict[str, Any]]:
    raw_queue = list(state.get("control_task_queue", [])) or list(state.get("task_queue", []))
    normalized: list[dict[str, Any]] = []
    for index, item in enumerate(raw_queue, start=1):
        if isinstance(item, dict):
            task = dict(item)
            owner = TOOL_OWNERS.get(str(task.get("tool", "")))
            if owner is None:
                raise ValueError(f"Unsupported trade-case Tool: {task.get('tool')}")
            task["agent"] = owner
            normalized.append(task)
        elif str(item) in ROLE_NAMES:
            normalized.append(_task_from_role(str(item), state, index))
        else:
            raise ValueError(f"Unsupported trade-case role: {item}")
    return normalized


def _prepare_task_role(
    *,
    state: TradeFlowState,
    role: str,
    task: dict[str, Any],
) -> dict[str, Any]:
    prepared = cast(TradeFlowState, dict(state))
    prepared["agent_inputs"] = {
        **state.get("agent_inputs", {}),
        role: {
            "tool_name": task["tool"],
            "args": task.get("args", {}),
        },
    }
    return _prepare_native_role(role, prepared)


def _trade_case_response(state: TradeFlowState) -> dict[str, Any]:
    return {
        "final_response": {
            "status": "SUCCESS",
            "mission_type": state.get("mission_type", "TRADE_CASE"),
            "case_ids": state.get("case_ids", []),
            "called_agents": state.get("called_agents", []),
            "task_results": state.get("task_results", {}),
            "agent_results": state.get("agent_results", {}),
        }
    }


def build_trade_case_graph() -> Any:
    """Compile a bounded, Human-free trade-case Specialist graph."""
    role_graphs = {name: _native_agent(name) for name in ROLE_NAMES}

    def trade_case_supervisor(state: TradeFlowState) -> Command:
        if state.get("supervisor_phase") == "AWAITING_RESULT":
            role = str(state["current_agent"])
            task = dict(state.get("current_task") or {})
            agent_results, cleanup = _collect_native_role(role, state)
            result = _tool_result(agent_results[role])
            task_results = {
                **state.get("task_results", {}),
                str(task["task_id"]): result,
            }
            remaining = list(state.get("control_task_queue", []))[1:]
            return Command(
                goto="trade_case_supervisor",
                update={
                    **cleanup,
                    "called_agents": [role],
                    "agent_results": agent_results,
                    "task_results": task_results,
                    "control_task_queue": remaining,
                    "current_agent": "",
                    "current_task": None,
                    "supervisor_phase": "IDLE",
                },
            )

        queue = list(state.get("control_task_queue", []))
        if not state.get("trade_case_initialized"):
            queue = _normalize_trade_case_queue(state)
        if not queue:
            return Command(
                goto="trade_case_response",
                update={
                    "trade_case_initialized": True,
                    "control_task_queue": [],
                },
            )
        task = dict(queue[0])
        if task.get("agent") == "human":
            raise ValueError("Human interrupt/resume is only reachable in the chat graph")
        route = route_current_task(task, past_steps=[])
        role = str(route["next"])
        return Command(
            goto=f"{role}_agent",
            update={
                **_prepare_task_role(state=state, role=role, task=task),
                "trade_case_initialized": True,
                "control_task_queue": queue,
                "current_agent": role,
                "current_task": task,
                "supervisor_phase": "AWAITING_RESULT",
                "supervisor_route": route,
            },
        )

    builder = StateGraph(TradeFlowState)
    builder.add_node(
        "trade_case_supervisor",
        trade_case_supervisor,
        destinations=(
            *(f"{role}_agent" for role in ROLE_NAMES),
            "trade_case_response",
        ),
    )
    for role, graph in role_graphs.items():
        builder.add_node(
            f"{role}_agent",
            graph,
            destinations=("trade_case_supervisor",),
        )
        builder.add_edge(f"{role}_agent", "trade_case_supervisor")
    builder.add_node("trade_case_response", _trade_case_response)
    builder.add_edge(START, "trade_case_supervisor")
    builder.add_edge("trade_case_response", END)
    return builder.compile()


document_intelligence_agent_graph = _native_agent("document_intelligence")
trade_case_manager_agent_graph = _native_agent("trade_case_manager")
financial_calendar_agent_graph = _native_agent("financial_calendar")
shipment_timeline_agent_graph = _native_agent("shipment_timeline")
financial_exposure_agent_graph = _native_agent("financial_exposure")
product_advisor_agent_graph = _native_agent("product_advisor")
report_writer_agent_graph = _native_agent("report_writer")
critic_agent_graph = _native_agent("critic")

_ROLE_GRAPHS = {
    "document_intelligence": document_intelligence_agent_graph,
    "trade_case_manager": trade_case_manager_agent_graph,
    "financial_calendar": financial_calendar_agent_graph,
    "shipment_timeline": shipment_timeline_agent_graph,
    "financial_exposure": financial_exposure_agent_graph,
    "product_advisor": product_advisor_agent_graph,
    "report_writer": report_writer_agent_graph,
    "critic": critic_agent_graph,
}

trade_case_graph = build_trade_case_graph()


def build_root_conversation_graph(checkpointer: Any | None = None) -> Any:
    """Compile the request-derived chat graph with the common control loop."""
    return build_common_control_graph(
        role_graphs=_ROLE_GRAPHS,
        structured_trade_graph=trade_case_graph,
        prepare_role=_prepare_native_role,
        collect_role=_collect_native_role,
        checkpointer=checkpointer,
    )


root_conversation_graph = build_root_conversation_graph()

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Callable
from datetime import date
from typing import Any, cast

from langchain_core.messages import AIMessage, HumanMessage, RemoveMessage
from langgraph.graph import END, START, StateGraph
from langgraph.graph.message import REMOVE_ALL_MESSAGES
from langgraph.types import Command, interrupt

from app.agents.planning import (
    make_final_answer,
    make_initial_decision,
    make_replanning_decision,
)
from app.agents.supervisor import TOOL_OWNERS, route_current_task
from app.core.config import get_settings
from app.db.models import Confirmation
from app.db.session import session_scope
from app.graphs.state import TradeFlowState
from app.schemas.planning import (
    ConversationalResponse,
    HumanIssue,
    HumanIssueResponse,
    PlanningResponse,
)
from app.services.product_advisory import (
    product_payload_for_user,
    update_company_product_profile,
)
from app.services.virtual_clock import current_date
from app.services.workflow_planning import make_workflow_contract_decision

PrepareRole = Callable[[str, TradeFlowState], dict[str, Any]]
CollectRole = Callable[
    [str, TradeFlowState],
    tuple[dict[str, Any], dict[str, Any]],
]

ROLE_NAMES = (
    "document_intelligence",
    "trade_case_manager",
    "financial_calendar",
    "shipment_timeline",
    "financial_exposure",
    "product_advisor",
    "report_writer",
    "critic",
)

_REQUEST_HINT_KEYS = (
    "workflow_kind",
    "company_id",
    "batch_id",
    "workbook_path",
    "as_of_date",
    "reported_at",
    "delay_days",
    "case_ids",
    "customer_role",
    "borrower_type",
    "known_facts",
    "not_met_facts",
    "scenario_codes",
    "rm_inbox",
    "generate_report",
)


def _latest_human_text(state: TradeFlowState) -> str:
    for message in reversed(state.get("messages", [])):
        if isinstance(message, HumanMessage):
            return str(message.content).strip()
        if isinstance(message, dict) and message.get("role") == "user":
            return str(message.get("content", "")).strip()
    return str(state.get("user_query", "")).strip()


def _request_hints(state: TradeFlowState) -> dict[str, Any]:
    hints = {
        key: state[key]
        for key in _REQUEST_HINT_KEYS
        if key in state and state.get(key) not in (None, "", [])
    }
    hints.setdefault("current_date", current_date().isoformat())
    return hints


def _direct_response_update(user_query: str, answer: str) -> dict[str, Any]:
    return {
        "planning_initialized": True,
        "planning_iteration": 1,
        "intent": "CONVERSATION",
        "user_query": user_query,
        "final_answer": answer,
        "final_response": {
            "status": "SUCCESS",
            "intent": "CONVERSATION",
            "answer": answer,
            "execution_plan": {},
            "tool_decisions": [],
            "execution_log": [],
            "review_log": [],
            "human_answers": [],
            "results": {},
        },
        "planning_log": [
            {
                "iteration": 1,
                "action": "DIRECT_RESPONSE",
                "reason": "No specialist or Tool execution is required.",
            }
        ],
        "messages": [AIMessage(content=answer, name="planning_agent")],
    }


def _prefer_typed_workflow_contract(
    *,
    model_decision: PlanningResponse,
    contract_decision: PlanningResponse,
) -> tuple[PlanningResponse, bool]:
    """Keep supported workflows from being downgraded to a model direct response.

    The deterministic contract is authoritative only when it recognizes a supported
    workflow.  When it returns a conversational response, the live Planning model may
    still supply either a richer direct response or a valid dynamic plan.
    """
    if isinstance(contract_decision.final_output, ConversationalResponse):
        return model_decision, False
    return contract_decision, True


def _normalize_generated_steps(plan_payload: dict[str, Any]) -> list[dict[str, Any]]:
    """Validate generated tasks against the local role and Tool allow-list."""
    normalized: list[dict[str, Any]] = []
    for index, item in enumerate(plan_payload.get("steps", []), start=1):
        task = dict(item)
        task.setdefault("task_id", f"planned_task_{index}")
        task.setdefault("description", task.get("reason", task["task_id"]))
        task.setdefault("args", {})
        if task.get("agent") == "human":
            if task.get("tool") != "interrupt/resume":
                raise ValueError("Human plan steps require interrupt/resume")
            issue = task.get("human_issue")
            if issue is None:
                raise ValueError("Human plan steps require a structured human_issue")
            task["human_issue"] = HumanIssue.model_validate(issue).model_dump(mode="json")
        else:
            owner = TOOL_OWNERS.get(str(task.get("tool", "")))
            if owner is None:
                raise ValueError(f"Planning returned an unsupported Tool: {task.get('tool')}")
            task["agent"] = owner
            task["human_issue"] = None
        normalized.append(task)
    if not 2 <= len(normalized) <= 12:
        raise ValueError("Planning must return two to twelve bounded tasks")
    return normalized


def _plan_payload(
    *,
    user_query: str,
    live_plan: dict[str, Any],
    tasks: list[dict[str, Any]],
) -> dict[str, Any]:
    return {
        "user_request": user_query,
        "mission_type": live_plan["mission_type"],
        "case_ids": live_plan.get("case_ids", []),
        "steps": [
            {
                "order": index,
                "task_id": task["task_id"],
                "agent": task["agent"],
                "tool": task["tool"],
                "reason": task["reason"],
                "human_issue": task.get("human_issue"),
            }
            for index, task in enumerate(tasks, start=1)
        ],
        "task_queue": tasks,
        "selected_agents": list(
            dict.fromkeys(task["agent"] for task in tasks if task["agent"] != "human")
        ),
    }


def _tool_result(structured: dict[str, Any]) -> Any:
    data = structured.get("data", {})
    if isinstance(data, dict) and "tool_result" in data:
        return data["tool_result"]
    outputs = structured.get("tool_outputs", [])
    if outputs:
        return outputs[-1].get("content")
    summary = structured.get("summary", "")
    if isinstance(summary, str):
        try:
            return json.loads(summary)
        except json.JSONDecodeError:
            return {"summary": summary}
    return summary


def _lookup_result(path: str, state: TradeFlowState) -> Any:
    parts = path.split(".")
    if not parts or not parts[0]:
        raise KeyError("A $result reference requires a task id")
    current: Any = state.get("task_results", {})
    for part in parts:
        if isinstance(current, dict) and part in current:
            current = current[part]
        elif isinstance(current, list) and part.isdigit():
            current = current[int(part)]
        else:
            raise KeyError(f"Unresolved Tool-result reference: $result.{path}")
    return current


def _resolve_value(value: Any, state: TradeFlowState) -> Any:
    if isinstance(value, dict):
        return {key: _resolve_value(item, state) for key, item in value.items()}
    if isinstance(value, list):
        return [_resolve_value(item, state) for item in value]
    if value == "$task_results":
        return state.get("task_results", {})
    if isinstance(value, str) and value.startswith("$human."):
        key = value.removeprefix("$human.")
        if key not in state.get("human_context", {}):
            raise KeyError(f"Unresolved Human response reference: {value}")
        return state["human_context"][key]
    if isinstance(value, str) and value.startswith("$state."):
        path = value.removeprefix("$state.")
        current: Any = state
        for part in path.split("."):
            if isinstance(current, dict) and part in current:
                current = current[part]
            else:
                return None
        return current
    if isinstance(value, str) and value.startswith("$result."):
        return _lookup_result(value.removeprefix("$result."), state)
    if isinstance(value, str):
        return value.replace("${request_id}", state.get("request_id", "control")).replace(
            "${thread_id}", state.get("thread_id", state.get("request_id", "control"))
        )
    return value


def _final_text(state: TradeFlowState) -> str:
    if state.get("final_answer"):
        return state["final_answer"]
    results = state.get("task_results", {})
    critic = next(
        (
            result
            for task_id, result in reversed(list(results.items()))
            if "review" in task_id and isinstance(result, dict)
        ),
        {},
    )
    verdict = critic.get("verdict", "NOT_RUN") if isinstance(critic, dict) else "NOT_RUN"
    mission = str(state.get("mission_type", "TRADE_WORKFLOW"))

    if mission == "UPLOAD_ANALYSIS":
        staged = results.get("stage_document_intelligence", {})
        bundle = results.get("bundle_trade_cases", {})
        commit = results.get("commit_trade_cases", {})
        proposals = list(bundle.get("cases", [])) if isinstance(bundle, dict) else []
        document_type_by_id: dict[str, str] = {}
        if isinstance(staged, dict):
            for document in staged.get("documents", []):
                if not isinstance(document, dict):
                    continue
                classification = document.get("classification")
                if not isinstance(classification, dict):
                    extraction = document.get("extraction")
                    classification = (
                        extraction.get("classification")
                        if isinstance(extraction, dict)
                        else None
                    )
                document_id = str(document.get("document_id") or "").strip()
                doc_type = (
                    str(classification.get("doc_type") or "").strip()
                    if isinstance(classification, dict)
                    else ""
                )
                if document_id and doc_type:
                    document_type_by_id[document_id] = doc_type
        committed = []
        pending = []
        if isinstance(commit, dict):
            committed = list(
                commit.get("committed_case_ids")
                or commit.get("created_case_ids")
                or commit.get("committed_cases")
                or []
            )
            pending = list(
                commit.get("pending_case_ids")
                or commit.get("awaiting_case_ids")
                or commit.get("pending_cases")
                or []
            )
        lines = [f"무역 문서 분석을 완료했습니다. 거래 후보 {len(proposals)}건입니다."]
        for index, proposal in enumerate(proposals, start=1):
            if not isinstance(proposal, dict):
                continue
            assigned_types = [
                document_type_by_id.get(str(document_id), "")
                for document_id in proposal.get("document_ids", [])
            ]
            case_label = str(proposal.get("case_id") or f"거래 후보 {index}")
            lines.append(
                f"- {case_label}: "
                f"Booking Confirmation {assigned_types.count('BOOKING_CONFIRMATION')}개, "
                f"Commercial Invoice {assigned_types.count('COMMERCIAL_INVOICE')}개, "
                f"B/L {assigned_types.count('BILL_OF_LADING')}개"
            )
        lines.append(f"DB 반영 {len(committed)}건, 대기 {len(pending)}건입니다.")
        return "\n\n".join(lines)

    if mission == "FINANCIAL_CALENDAR_IMPORT":
        imported = results.get("import_financial_calendar", {})
        if not isinstance(imported, dict):
            imported = {}
        return (
            "업로드를 완료했습니다. "
            f"금융 이벤트 {int(imported.get('event_count') or 0)}건, "
            f"거래 연결 {int(imported.get('link_count') or 0)}건을 "
            "캘린더 DB에 반영했습니다."
        )

    if mission == "PROACTIVE_MONITORING":
        finalized = results.get("finalize_manual_monitoring", {})
        ranked = list(finalized.get("ranked_risks", [])) if isinstance(finalized, dict) else []
        if not ranked:
            ranked = [
                result
                for result in results.values()
                if isinstance(result, dict) and result.get("source_kind") == "MONITORING_SCAN"
            ]
        conflict_count = sum(
            len(item.get("conflicts", []))
            for item in ranked
            if isinstance(item, dict) and isinstance(item.get("conflicts", []), list)
        )
        highest = next(
            (
                str(item.get("highest_priority"))
                for item in ranked
                if isinstance(item, dict) and item.get("highest_priority")
            ),
            "정상",
        )
        return (
            f"금일 금융위험 분석을 완료했습니다. 분석 거래 {len(ranked)}건, "
            f"충돌 {conflict_count}건, 최고 우선순위 {highest}입니다."
        )

    if mission == "USER_REPORTED_DELAY":
        exposure = next(
            (
                result
                for result in reversed(list(results.values()))
                if isinstance(result, dict)
                and result.get("calculation_id")
                and result.get("shipment_calculation_id")
                and isinstance(result.get("scenarios"), list)
            ),
            results.get("calculate_financial_exposure", {}),
        )
        conflicts = list(exposure.get("conflicts", [])) if isinstance(exposure, dict) else []
        highest = exposure.get("highest_priority") if isinstance(exposure, dict) else None
        if not highest:
            highest = next(
                (
                    item.get("response_priority_level") or item.get("priority")
                    for item in conflicts
                    if isinstance(item, dict)
                    and (item.get("response_priority_level") or item.get("priority"))
                ),
                "정상",
            )
        return (
            f"사용자 제보 지연 시나리오를 저장하고 금융영향을 계산했습니다. "
            f"충돌 {len(conflicts)}건, 최고 우선순위 {highest}입니다. "
            "실제 B/L 또는 출항 사실은 변경하지 않았습니다. "
            f"독립 Critic 판정은 {verdict}입니다."
        )

    if mission == "PRODUCT_ADVISORY_REPORT":
        options = results.get("retrieve_product_evidence", {})
        product_count = len(options.get("options", [])) if isinstance(options, dict) else 0
        customer = results.get("render_customer_report")
        rm = results.get("render_rm_report")
        if isinstance(customer, dict) and isinstance(rm, dict):
            return (
                f"원문 페이지 근거가 있는 상품 후보 {product_count}개를 검토하고, "
                "동일한 계산 기준으로 고객용·KB 직원용 보고서를 저장했습니다. "
                f"독립 Critic 판정은 {verdict}입니다."
            )
        return (
            f"원문 페이지 근거가 있는 상품 후보 {product_count}개를 정리했습니다. "
            f"독립 Critic 판정은 {verdict}입니다."
        )

    completed = [str(item.get("task_id")) for item in state.get("past_steps", [])]
    missing_document_count = sum(
        len(result.get("missing_document_issues", []))
        for result in results.values()
        if isinstance(result, dict)
    )
    suffix = (
        f" 전체 문서가 없는 항목 {missing_document_count}건은 "
        "값 입력 요청으로 바꾸지 않고 AWAITING_DOCUMENT로 유지했습니다."
        if missing_document_count
        else ""
    )
    return (
        f"{mission} 작업을 완료했습니다. "
        f"완료 단계: {', '.join(completed) or '없음'}. "
        f"Critic 판정: {verdict}.{suffix}"
    )


def _next_actions(state: TradeFlowState) -> list[dict[str, str]]:
    """Return UI suggestions only; no external handoff is executed."""
    mission = str(state.get("mission_type", ""))
    case_id = next(iter(state.get("case_ids", [])), "")
    if mission == "UPLOAD_ANALYSIS":
        return [
            {
                "id": "register_financial_calendar",
                "label": "금융결제 조건 등록",
                "message": (
                    "4번 금융일정 등록을 선택하고 "
                    "2-Sheet XLSX(1.금융이벤트, 2.거래연결)를 업로드해 주세요."
                ),
            }
        ]
    if mission == "FINANCIAL_CALENDAR_IMPORT":
        return []
    if mission in {"PROACTIVE_MONITORING", "USER_REPORTED_DELAY"}:
        suffix = f" {case_id}" if case_id else ""
        return [
            {
                "id": "build_conflict_report",
                "label": "충돌 종합보고서 생성",
                "message": f"{suffix} 충돌 종합보고서와 고객용·RM용 브리핑을 만들어줘.",
            }
        ]
    if mission == "PRODUCT_ADVISORY_REPORT":
        return [
            {
                "id": "suggest_rm_contact",
                "label": "KB 무역상담사 연결 안내",
                "message": "KB 무역상담사에게 연락해 드릴까요?",
            }
        ]
    return []


def _finalize_from_planning(state: TradeFlowState) -> dict[str, Any]:
    existing = dict(state.get("final_response", {}))
    status = str(existing.get("status", "SUCCESS"))
    if status == "FAILED":
        answer = state.get("final_answer") or (
            "Supervisor가 실행 계약 오류를 확인해 계획을 중단했습니다. "
            f"reason={existing.get('reason', 'unknown')}."
        )
    elif status == "CANCELLED":
        answer = state.get("final_answer") or (
            "입력된 중단 값에 따라 후속 변경과 보고서 생성을 수행하지 않았습니다."
        )
    elif get_settings().tradeflow_mode == "live" and not get_settings().deterministic_final_answer:
        answer = make_final_answer(
            user_query=state.get("user_query", ""),
            grounded_results=json.dumps(
                product_payload_for_user(state.get("task_results", {})),
                ensure_ascii=False,
                default=str,
            ),
        )
    else:
        answer = _final_text(state)

    payload = {
        **existing,
        "status": status,
        "intent": state.get("intent"),
        "answer": answer,
        "execution_plan": state.get("execution_plan", {}),
        "tool_decisions": state.get("tool_decisions", []),
        "planning_log": state.get("planning_log", []),
        "supervisor_log": state.get("supervisor_log", []),
        "past_steps": state.get("past_steps", []),
        "execution_log": state.get("execution_log", []),
        "review_log": state.get("review_log", []),
        "human_answers": state.get("human_answers", []),
        "results": state.get("task_results", {}),
        "next_actions": _next_actions(state),
    }
    transcript: list[Any] = [
        RemoveMessage(id=REMOVE_ALL_MESSAGES),
        HumanMessage(content=state.get("user_query", "")),
    ]
    transcript.extend(
        HumanMessage(
            content=json.dumps(item, ensure_ascii=False, default=str),
            name="human_confirmation",
        )
        for item in state.get("human_answers", [])
    )
    transcript.append(AIMessage(content=answer, name="planning_agent"))
    return {
        "messages": transcript,
        "final_answer": answer,
        "final_response": payload,
    }


def _review_specialist_result(
    state: TradeFlowState,
    *,
    role: str,
    task: dict[str, Any],
    structured: dict[str, Any],
    result: Any,
) -> dict[str, Any]:
    expected_tool = str(task.get("tool", ""))
    matching_outputs = [
        output
        for output in structured.get("tool_outputs", [])
        if output.get("tool_name") == expected_tool
    ]
    tool_succeeded = bool(matching_outputs) and all(
        str(output.get("status", "")).lower() == "success" for output in matching_outputs
    )
    review = {
        "sequence": len(state.get("review_log", [])) + 1,
        "task_id": task.get("task_id"),
        "agent": role,
        "tool": expected_tool,
        "agent_status": structured.get("status", "UNKNOWN"),
        "tool_status": "SUCCESS" if tool_succeeded else "FAILED",
        "result_present": result not in (None, {}, ""),
        "decision": "CONTINUE",
        "reviewer": "supervisor_agent",
    }
    if not tool_succeeded or not review["result_present"]:
        review["decision"] = "STOP"
    if role == "critic" and isinstance(result, dict):
        review["critic_verdict"] = result.get("verdict")
        if result.get("verdict") != "PASS":
            review["decision"] = "STOP"
    return review


def _safe_task_suffix(value: str) -> str:
    normalized = re.sub(r"[^a-zA-Z0-9]+", "_", value).strip("_").lower()
    return normalized[:48] or hashlib.sha256(value.encode()).hexdigest()[:12]


def _open_human_requests(result: Any) -> list[dict[str, Any]]:
    if not isinstance(result, dict):
        return []
    requests = result.get("human_requests", [])
    if not isinstance(requests, list):
        return []
    return [
        dict(item)
        for item in requests
        if isinstance(item, dict)
        and str(item.get("status", "OPEN")).upper() in {"OPEN", "PENDING"}
        and item.get("request_id")
        and item.get("document_id")
        and item.get("exact_standard_field")
    ]


def _document_followups(
    *,
    state: TradeFlowState,
    result: Any,
    remaining: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    existing_ids = {str(item.get("task_id")) for item in [*state.get("past_steps", []), *remaining]}
    followups: list[dict[str, Any]] = []
    for request in _open_human_requests(result):
        request_id = str(request["request_id"])
        suffix = _safe_task_suffix(request_id)
        human_task_id = f"resolve_document_field_{suffix}"
        apply_task_id = f"apply_document_field_{suffix}"
        if human_task_id in existing_ids or apply_task_id in existing_ids:
            continue
        response_key = f"document_field_value_{suffix}"
        context = {
            key: request.get(key)
            for key in (
                "request_id",
                "request_type",
                "case_id",
                "document_id",
                "doc_type",
                "exact_standard_field",
                "field_path",
            )
            if request.get(key) is not None
        }
        followups.extend(
            [
                {
                    "task_id": human_task_id,
                    "description": (
                        f"Provide {request['exact_standard_field']} for "
                        f"document {request['document_id']}"
                    ),
                    "agent": "human",
                    "tool": "interrupt/resume",
                    "args": {},
                    "reason": (
                        "Document completeness returned one exact missing field that "
                        "requires an explicit value."
                    ),
                    "human_issue": {
                        "issue_code": request_id,
                        "prompt": str(request.get("prompt") or "필드 값을 입력해 주세요."),
                        "response_key": response_key,
                        "value_type": "string",
                        "allowed_values": [],
                        "cancel_values": [],
                        "context": context,
                    },
                },
                {
                    "task_id": apply_task_id,
                    "description": (
                        f"Apply the explicit value to {request['exact_standard_field']}"
                    ),
                    "agent": "trade_case_manager",
                    "tool": "apply_document_field_override",
                    "args": {
                        "batch_id": result.get("batch_id"),
                        "document_id": request["document_id"],
                        "exact_standard_field": request["exact_standard_field"],
                        "value": f"$human.{response_key}",
                        "actor": "human:${thread_id}",
                        "reason": f"Resolved structured issue {request_id}",
                    },
                    "reason": (
                        "Trade Case Manager applies only the exact Human-provided field "
                        "and recomputes completeness."
                    ),
                    "human_issue": None,
                },
            ]
        )
        existing_ids.update({human_task_id, apply_task_id})
    return followups


def _monitoring_followups(
    *,
    state: TradeFlowState,
    result: Any,
    remaining: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[str]]:
    if not isinstance(result, dict) or not isinstance(result.get("candidates"), list):
        return [], []
    as_of_date = str(result.get("as_of_date") or state.get("as_of_date") or date.today())
    existing_ids = {str(item.get("task_id")) for item in [*state.get("past_steps", []), *remaining]}
    followups: list[dict[str, Any]] = []
    case_ids: list[str] = []
    for candidate in result["candidates"]:
        if not isinstance(candidate, dict) or not candidate.get("case_id"):
            continue
        case_id = str(candidate["case_id"])
        case_ids.append(case_id)
        suffix = _safe_task_suffix(case_id)
        shipment_task_id = f"shipment_snapshot_{suffix}"
        finance_task_id = f"proactive_risk_scan_{suffix}"
        if shipment_task_id not in existing_ids:
            followups.append(
                {
                    "task_id": shipment_task_id,
                    "description": f"Read shipment facts for monitoring candidate {case_id}",
                    "agent": "shipment_timeline",
                    "tool": "read_shipment_snapshot",
                    "args": {"case_id": case_id},
                    "reason": (
                        "Shipment status must be read explicitly before finance risk is evaluated."
                    ),
                    "human_issue": None,
                }
            )
            existing_ids.add(shipment_task_id)
        if finance_task_id not in existing_ids:
            followups.append(
                {
                    "task_id": finance_task_id,
                    "description": f"Run the finance-owned proactive risk scan for {case_id}",
                    "agent": "financial_exposure",
                    "tool": "run_proactive_risk_scan",
                    "args": {
                        "case_id": case_id,
                        "as_of_date": as_of_date,
                        "request_id": f"${{request_id}}-proactive-{suffix}",
                    },
                    "reason": (
                        "Financial Exposure owns due dates, conflicts, priority, persistence, "
                        "and idempotency."
                    ),
                    "human_issue": None,
                }
            )
            existing_ids.add(finance_task_id)
    return followups, case_ids


def _payment_gate_followups(
    *,
    state: TradeFlowState,
    result: Any,
    remaining: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    if not isinstance(result, dict) or result.get("permitted") is True:
        return remaining
    missing_fields = {str(field) for field in result.get("missing_fields", [])}
    anchor_confirmation_ready = (
        result.get("calculation_allowed") == "AFTER_CONFIRMATION"
        and missing_fields == {"anchor_type_effective"}
        and result.get("tenor_days") is not None
        and result.get("day_type_effective") in {"CALENDAR", "BUSINESS"}
    )
    # A NO gate, missing tenor, or missing day type cannot be repaired by the
    # confirmed_anchor argument.  Leave the calculation in place so the
    # Financial Exposure specialist fails closed instead of asking an
    # irrelevant B/L-anchor question and pretending the gate was repaired.
    if not anchor_confirmation_ready:
        return remaining
    case_id = str(result.get("case_id") or next(iter(state.get("case_ids", [])), "UNKNOWN"))
    suffix = _safe_task_suffix(case_id)
    task_id = f"confirm_payment_anchor_{suffix}"
    existing_ids = {str(item.get("task_id")) for item in [*state.get("past_steps", []), *remaining]}
    updated_remaining: list[dict[str, Any]] = []
    for item in remaining:
        copied = dict(item)
        if copied.get("tool") == "calculate_financial_exposure":
            args = dict(copied.get("args", {}))
            args["confirmed_anchor"] = "$human.confirmed_anchor"
            copied["args"] = args
        updated_remaining.append(copied)
    if task_id in existing_ids:
        return updated_remaining
    raw_text = str(result.get("raw_text") or "").upper()
    if "B/L" in raw_text or "BILL OF LADING" in raw_text:
        # The minimal persisted document contract stores only the on-board date.
        # Do not offer B/L_DATE here: the Financial Exposure service will reject
        # it and the judge demo would stop after an apparently valid UI choice.
        allowed_values = ["ON_BOARD_DATE", "CANCEL"]
        customer_prompt = (
            "현재 최소 문서 계약에서 지급 기준으로 사용할 수 있는 B/L 날짜는 "
            "본선적재일(On-board date)입니다. 본선적재일 기준으로 계산할까요?"
        )
    elif "INVOICE" in raw_text:
        allowed_values = ["INVOICE_DATE", "CANCEL"]
        customer_prompt = str(
            result.get("customer_question") or "Invoice date를 지급 기준으로 확인할까요?"
        )
    else:
        allowed_values = ["B/L_DATE", "ON_BOARD_DATE", "INVOICE_DATE", "CANCEL"]
        customer_prompt = str(
            result.get("customer_question")
            or "지급 기준일을 B/L_DATE, ON_BOARD_DATE 또는 INVOICE_DATE 중에서 확인해 주세요."
        )
    issue_task = {
        "task_id": task_id,
        "description": f"Confirm the payment anchor for {case_id}",
        "agent": "human",
        "tool": "interrupt/resume",
        "args": {},
        "reason": (
            "Financial Exposure returned a non-permitted gate and an exact anchor "
            "value is required before calculation."
        ),
        "human_issue": {
            "issue_code": f"PAYMENT_ANCHOR_REQUIRED:{case_id}",
            "prompt": customer_prompt,
            "response_key": "confirmed_anchor",
            "value_type": "string",
            "allowed_values": allowed_values,
            "cancel_values": ["CANCEL"],
            "context": {
                "case_id": case_id,
                "obligation_id": result.get("obligation_id"),
                "calculation_allowed": result.get("calculation_allowed"),
                "missing_fields": result.get("missing_fields", []),
            },
        },
    }
    return [issue_task, *updated_remaining]


def _financial_link_detail_followups(
    *,
    state: TradeFlowState,
    task: dict[str, Any],
    result: Any,
    remaining: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Expand post-conflict link data actions into reviewed updates and one recalc.

    Automatic Human interruption is intentionally limited to user-reported delay
    calculations. Portfolio monitoring scans keep these actions non-blocking so a
    user-requested "today's risk" review can finish without fabricated answers.
    """
    if task.get("tool") != "calculate_financial_exposure" or not isinstance(result, dict):
        return remaining
    raw_actions = result.get("data_actions", [])
    if not isinstance(raw_actions, list):
        return remaining
    actions: list[dict[str, Any]] = []
    seen: set[tuple[str, tuple[str, ...]]] = set()
    for raw_action in raw_actions:
        if not isinstance(raw_action, dict):
            continue
        if raw_action.get("code") != "FINANCIAL_LINK_DETAILS_REQUIRED":
            continue
        required_ids = (
            "company_id",
            "case_id",
            "transaction_id",
            "transaction_event_link_id",
            "financial_event_id",
        )
        if any(not raw_action.get(key) for key in required_ids):
            continue
        missing = tuple(
            sorted(
                {
                    str(field)
                    for field in raw_action.get("missing_fields", [])
                    if str(field) in {"dependency_scope", "linked_amount", "linked_currency"}
                }
            )
        )
        if not missing:
            continue
        identity = (str(raw_action["transaction_event_link_id"]), missing)
        if identity in seen:
            continue
        seen.add(identity)
        actions.append({**raw_action, "missing_fields": list(missing)})
    if not actions:
        return remaining

    existing_ids = {str(item.get("task_id")) for item in [*state.get("past_steps", []), *remaining]}
    followups: list[dict[str, Any]] = []
    update_task_ids: list[str] = []
    action_identity: list[str] = []
    for action in actions:
        link_id = str(action["transaction_event_link_id"])
        missing = set(action["missing_fields"])
        phase = "scope" if "dependency_scope" in missing else "amount"
        suffix = _safe_task_suffix(f"{link_id}-{phase}")
        human_task_id = f"confirm_financial_link_{phase}_{suffix}"
        update_task_id = f"update_financial_link_{phase}_{suffix}"
        if human_task_id in existing_ids or update_task_id in existing_ids:
            continue
        action_identity.append(f"{link_id}:{phase}")
        if phase == "scope":
            response_key = f"financial_link_scope_{suffix}"
            prompt = (
                f"금융 이벤트 {action['financial_event_id']}가 거래 "
                f"{action['transaction_id']} 회수대금에 전액(FULL) 또는 "
                "일부(PARTIAL) 의존하는지 확인해 주세요."
            )
            value_type = "string"
            allowed_values = ["FULL", "PARTIAL", "CANCEL"]
            update_details: Any = {"dependency_scope": f"$human.{response_key}"}
        else:
            response_key = f"financial_link_amount_currency_{suffix}"
            prompt = (
                f"금융 이벤트 {action['financial_event_id']}에 연결된 금액과 통화를 "
                "'30000 USD' 형식으로 입력해 주세요."
            )
            value_type = "amount_currency"
            allowed_values = []
            update_details = f"$human.{response_key}"
        context = {
            key: action[key]
            for key in (
                "company_id",
                "case_id",
                "transaction_id",
                "transaction_event_link_id",
                "financial_event_id",
                "missing_fields",
            )
        }
        followups.extend(
            [
                {
                    "task_id": human_task_id,
                    "description": f"Confirm optional financial link {phase} details for {link_id}",
                    "agent": "human",
                    "tool": "interrupt/resume",
                    "args": {},
                    "reason": (
                        "A dated conflict requires explicit transaction-link facts that the "
                        "minimal two-sheet workbook intentionally omits."
                    ),
                    "human_issue": {
                        "issue_code": f"FINANCIAL_LINK_{phase.upper()}_REQUIRED:{link_id}",
                        "prompt": prompt,
                        "response_key": response_key,
                        "value_type": value_type,
                        "allowed_values": allowed_values,
                        "cancel_values": ["CANCEL"] if phase == "scope" else [],
                        "context": context,
                    },
                },
                {
                    "task_id": update_task_id,
                    "description": f"Store Human-reviewed optional financial link details for {link_id}",
                    "agent": "financial_calendar",
                    "tool": "update_financial_event_link_details",
                    "args": {
                        "company_id": action["company_id"],
                        "case_id": action["case_id"],
                        "transaction_id": action["transaction_id"],
                        "transaction_event_link_id": link_id,
                        "details": update_details,
                        "actor": "human:${thread_id}",
                    },
                    "reason": (
                        "Financial Calendar owns explicit optional facts on the existing "
                        "transaction-event link."
                    ),
                    "human_issue": None,
                },
            ]
        )
        update_task_ids.append(update_task_id)
        existing_ids.update({human_task_id, update_task_id})

    if not update_task_ids:
        return remaining
    case_id = str(result.get("case_id") or actions[0]["case_id"])
    recalc_digest = hashlib.sha256("|".join(sorted(action_identity)).encode()).hexdigest()[:10]
    recalc_task_id = f"recalculate_financial_exposure_{_safe_task_suffix(case_id)}_{recalc_digest}"
    recalc_args = dict(task.get("args", {}))
    request_id = str(recalc_args.get("request_id") or "${request_id}-financial-exposure")
    recalc_args["request_id"] = f"{request_id}-link-review"
    followups.append(
        {
            "task_id": recalc_task_id,
            "description": f"Recalculate finance exposure after reviewed link details for {case_id}",
            "agent": "financial_exposure",
            "tool": "calculate_financial_exposure",
            "args": recalc_args,
            "reason": (
                "Financial Exposure must persist a new immutable result using the reviewed "
                "link facts."
            ),
            "human_issue": None,
        }
    )
    evidence_ids = [*update_task_ids, recalc_task_id]
    updated_remaining: list[dict[str, Any]] = []
    for item in remaining:
        copied = dict(item)
        if copied.get("tool") == "review_workflow_evidence":
            args = dict(copied.get("args", {}))
            args["expected_task_ids"] = list(
                dict.fromkeys([*args.get("expected_task_ids", []), *evidence_ids])
            )
            copied["args"] = args
        updated_remaining.append(copied)
    return [*followups, *updated_remaining]


def _rewrite_product_result_references(
    remaining: list[dict[str, Any]],
    *,
    match_task_id: str,
) -> list[dict[str, Any]]:
    """Point downstream product work at the latest filter pass."""
    rewritten: list[dict[str, Any]] = []
    for item in remaining:
        copied = dict(item)
        args = dict(copied.get("args", {}))
        if copied.get("tool") == "retrieve_product_evidence":
            args.update(
                {
                    "query": f"$result.{match_task_id}.retrieval_query",
                    "product_ids": f"$result.{match_task_id}.product_ids",
                    "matched_products": f"$result.{match_task_id}.products",
                }
            )
        elif copied.get("tool") == "review_workflow_evidence":
            args["expected_task_ids"] = list(
                dict.fromkeys([*args.get("expected_task_ids", []), match_task_id])
            )
        copied["args"] = args
        rewritten.append(copied)
    return rewritten


def _product_filter_followups(
    *,
    state: TradeFlowState,
    task: dict[str, Any],
    result: Any,
    remaining: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Ask only unresolved product hard filters, then rerun one deterministic match."""
    if task.get("tool") != "match_product_scenario" or not isinstance(result, dict):
        return remaining

    filter_context = result.get("filter_context", {})
    if not isinstance(filter_context, dict):
        filter_context = {}
    case_id = str(filter_context.get("case_id") or next(iter(state.get("case_ids", [])), ""))
    company_id = str(filter_context.get("company_id") or state.get("company_id") or "")
    identity = company_id or case_id or state.get("thread_id", "product")
    suffix = _safe_task_suffix(identity)
    existing_ids = {str(item.get("task_id")) for item in [*state.get("past_steps", []), *remaining]}

    base_args = dict(task.get("args", {}))
    base_args.update(
        {
            "customer_role": str(result.get("customer_role") or "UNKNOWN"),
            "borrower_type": str(result.get("borrower_type") or "UNKNOWN"),
            "known_facts": "$state.known_facts",
            "not_met_facts": "$state.not_met_facts",
            "case_id": case_id or base_args.get("case_id"),
            "company_id": company_id or base_args.get("company_id"),
        }
    )

    missing_inputs = {str(item) for item in result.get("missing_filter_inputs", []) if str(item)}
    if "borrower_type" in missing_inputs:
        human_task_id = f"confirm_product_borrower_type_{suffix}"
        rematch_task_id = f"rematch_product_after_borrower_{suffix}"
        if human_task_id in existing_ids or rematch_task_id in existing_ids:
            return remaining
        rematch_args = {
            **base_args,
            "borrower_type": "$state.borrower_type",
            "requirements_reviewed": False,
        }
        return [
            {
                "task_id": human_task_id,
                "description": "Confirm the customer business registration type once",
                "agent": "human",
                "tool": "interrupt/resume",
                "args": {},
                "reason": (
                    "The KB customer profile has no borrower type, which is a hard product "
                    "filter and cannot be inferred from trade documents."
                ),
                "human_issue": {
                    "issue_code": f"PRODUCT_BORROWER_TYPE_REQUIRED:{identity}",
                    "prompt": "사업자 유형을 선택해 주세요: 법인사업자 또는 개인사업자",
                    "response_key": "borrower_type",
                    "value_type": "string",
                    "allowed_values": ["CORPORATION", "SOLE_PROPRIETOR"],
                    "cancel_values": [],
                    "context": {
                        "company_id": company_id or None,
                        "case_id": case_id or None,
                        "profile_field": "borrower_type",
                    },
                },
            },
            {
                "task_id": rematch_task_id,
                "description": "Rerun product filters with the confirmed borrower type",
                "agent": "product_advisor",
                "tool": "match_product_scenario",
                "args": rematch_args,
                "reason": "The explicit profile answer is now available to the hard filter.",
                "human_issue": None,
            },
            *_rewrite_product_result_references(
                remaining,
                match_task_id=rematch_task_id,
            ),
        ]

    raw_questions = result.get("requirement_questions", [])
    questions = [item for item in raw_questions if isinstance(item, dict)]
    if not questions or bool(base_args.get("requirements_reviewed")):
        return remaining

    rematch_task_id = f"rematch_product_after_requirements_{suffix}"
    if rematch_task_id in existing_ids:
        return remaining
    human_tasks: list[dict[str, Any]] = []
    for question in questions:
        code = str(question.get("requirement_code") or "").strip().upper()
        if not code:
            continue
        question_suffix = _safe_task_suffix(code)
        human_task_id = f"confirm_product_requirement_{suffix}_{question_suffix}"
        if human_task_id in existing_ids:
            continue
        requirement_label = str(question.get("requirement_label") or code).strip()
        requirement_summary = str(question.get("requirement_summary") or "").strip()
        if code == "STRATEGIC_TARGET_COMPANY":
            requirement_prompt = "전략 타깃 기업에 해당합니까?"
        elif code == "KSURE_PROGRAM_ELIGIBILITY":
            requirement_prompt = "K-SURE 지원 프로그램 대상에 해당합니까?"
        else:
            requirement_prompt = f"{requirement_label} 요건을 충족합니까?"
        human_tasks.append(
            {
                "task_id": human_task_id,
                "description": f"Confirm product requirement {code}",
                "agent": "human",
                "tool": "interrupt/resume",
                "args": {},
                "reason": (
                    "Only a product-specific hard requirement that is absent from DB and "
                    "document facts is being requested."
                ),
                "human_issue": {
                    "issue_code": f"PRODUCT_REQUIREMENT:{identity}:{code}",
                    "prompt": requirement_prompt,
                    "response_key": f"product_requirement_{question_suffix}",
                    "value_type": "boolean",
                    "allowed_values": [True, False],
                    "cancel_values": [],
                    "context": {
                        "company_id": company_id or None,
                        "case_id": case_id or None,
                        "requirement_code": code,
                        "requirement_label": requirement_label,
                        "requirement_summary": requirement_summary,
                        "product_ids": question.get("product_ids", []),
                    },
                },
            }
        )
    if not human_tasks:
        return remaining
    rematch_args = {**base_args, "requirements_reviewed": True}
    return [
        *human_tasks,
        {
            "task_id": rematch_task_id,
            "description": "Rerun product filters with reviewed product requirements",
            "agent": "product_advisor",
            "tool": "match_product_scenario",
            "args": rematch_args,
            "reason": "Explicit yes/no requirement facts now complete the product hard filter.",
            "human_issue": None,
        },
        *_rewrite_product_result_references(
            remaining,
            match_task_id=rematch_task_id,
        ),
    ]


def _inject_data_driven_followups(
    *,
    state: TradeFlowState,
    task: dict[str, Any],
    result: Any,
    remaining: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[str]]:
    followups: list[dict[str, Any]] = []
    case_ids: list[str] = []
    if task.get("tool") in {"bundle_trade_cases", "apply_document_field_override"}:
        followups = _document_followups(
            state=state,
            result=result,
            remaining=remaining,
        )
    elif task.get("tool") == "select_financial_monitoring_candidates":
        followups, case_ids = _monitoring_followups(
            state=state,
            result=result,
            remaining=remaining,
        )
        if followups:
            evidence_ids = [
                str(item["task_id"])
                for item in followups
                if item["tool"] in {"read_shipment_snapshot", "run_proactive_risk_scan"}
            ]
            updated_remaining: list[dict[str, Any]] = []
            for item in remaining:
                copied = dict(item)
                if copied.get("tool") == "review_workflow_evidence":
                    args = dict(copied.get("args", {}))
                    args["expected_task_ids"] = list(
                        dict.fromkeys(
                            [
                                *args.get("expected_task_ids", []),
                                *evidence_ids,
                            ]
                        )
                    )
                    copied["args"] = args
                updated_remaining.append(copied)
            remaining = updated_remaining
    elif task.get("tool") == "inspect_payment_gate":
        remaining = _payment_gate_followups(
            state=state,
            result=result,
            remaining=remaining,
        )
    elif task.get("tool") == "calculate_financial_exposure":
        remaining = _financial_link_detail_followups(
            state=state,
            task=task,
            result=result,
            remaining=remaining,
        )
    elif task.get("tool") == "match_product_scenario":
        remaining = _product_filter_followups(
            state=state,
            task=task,
            result=result,
            remaining=remaining,
        )
    return [*followups, *remaining], case_ids


def _validate_human_value(issue: HumanIssue, value: Any) -> Any:
    if issue.value_type == "string":
        if not isinstance(value, str) or not value.strip():
            raise ValueError("Human issue value must be a non-empty string")
        normalized: Any = value.strip()
    elif issue.value_type == "boolean":
        if not isinstance(value, bool):
            raise ValueError("Human issue value must be a boolean")
        normalized = value
    elif issue.value_type == "integer":
        if isinstance(value, bool) or not isinstance(value, int):
            raise ValueError("Human issue value must be an integer")
        normalized = value
    elif issue.value_type == "string_list":
        if not isinstance(value, list) or not all(
            isinstance(item, str) and item.strip() for item in value
        ):
            raise ValueError("Human issue value must be a list of non-empty strings")
        normalized = [item.strip() for item in value]
    elif issue.value_type == "integer_list":
        if (
            not isinstance(value, list)
            or not value
            or not all(isinstance(item, int) and not isinstance(item, bool) for item in value)
        ):
            raise ValueError("Human issue value must be a non-empty integer list")
        normalized = value
    elif issue.value_type == "amount_currency":
        if isinstance(value, dict):
            raw_amount = value.get("linked_amount", value.get("amount"))
            raw_currency = value.get("linked_currency", value.get("currency"))
        elif isinstance(value, str):
            compact = " ".join(value.replace(",", "").upper().split())
            amount_first = re.fullmatch(r"([0-9]+(?:\.[0-9]+)?)\s+([A-Z]{3})", compact)
            currency_first = re.fullmatch(r"([A-Z]{3})\s+([0-9]+(?:\.[0-9]+)?)", compact)
            if amount_first:
                raw_amount, raw_currency = amount_first.group(1), amount_first.group(2)
            elif currency_first:
                raw_currency, raw_amount = currency_first.group(1), currency_first.group(2)
            else:
                raise ValueError(
                    "Human issue value must use an amount/currency pair such as 30000 USD"
                )
        else:
            raise ValueError("Human issue value must provide linked amount and currency")
        if isinstance(raw_amount, bool):
            raise ValueError("linked_amount must be a positive number")
        try:
            amount = float(raw_amount)
        except (TypeError, ValueError) as exc:
            raise ValueError("linked_amount must be a positive number") from exc
        currency = str(raw_currency or "").strip().upper()
        if amount <= 0 or len(currency) != 3 or not currency.isalpha():
            raise ValueError(
                "Human issue value must contain a positive amount and 3-letter currency"
            )
        normalized = {"linked_amount": amount, "linked_currency": currency}
    else:  # pragma: no cover - protected by HumanIssue validation
        raise ValueError(f"Unsupported Human issue value type: {issue.value_type}")
    if issue.allowed_values and normalized not in issue.allowed_values:
        raise ValueError(f"Human issue value must be one of {issue.allowed_values!r}")
    return normalized


def human_question(state: TradeFlowState) -> dict[str, Any]:
    """Persist one structured issue before graph execution is interrupted."""
    task = state.get("current_task") or {}
    issue = HumanIssue.model_validate(task.get("human_issue"))
    stable_key = (
        f"{state.get('thread_id')}:{state.get('request_id')}:{task.get('task_id')}:"
        f"{issue.issue_code}"
    )
    confirmation_id = f"CONF-{hashlib.sha256(stable_key.encode()).hexdigest()[:16]}"
    case_id = issue.context.get("case_id")
    if not case_id:
        case_id = next(iter(state.get("case_ids", [])), None)
    payload = issue.model_dump(mode="json")
    with session_scope() as session:
        if session.get(Confirmation, confirmation_id) is None:
            session.add(
                Confirmation(
                    confirmation_id=confirmation_id,
                    thread_id=state.get("thread_id", state.get("request_id", "studio")),
                    case_id=str(case_id) if case_id else None,
                    question_type=issue.issue_code,
                    payload_json=payload,
                    status="PENDING",
                    response_json=None,
                )
            )
    return {
        "pending_issue": {
            **payload,
            "confirmation_id": confirmation_id,
            "task_id": task.get("task_id"),
        },
        "confirmation_id": confirmation_id,
    }


def human_confirmation(state: TradeFlowState) -> Command:
    """Resume one Human step only with the matching issue_code/value payload."""
    task = state.get("current_task") or {}
    issue = HumanIssue.model_validate(state.get("pending_issue") or task.get("human_issue"))
    raw_response = interrupt(
        {
            "issue": issue.model_dump(mode="json"),
            "confirmation_id": state.get("confirmation_id"),
            "task_id": task.get("task_id"),
        }
    )
    response = HumanIssueResponse.model_validate(raw_response)
    if response.issue_code != issue.issue_code:
        raise ValueError(
            f"Resume issue_code {response.issue_code!r} does not match {issue.issue_code!r}"
        )
    value = _validate_human_value(issue, response.value)
    if issue.response_key == "borrower_type":
        value = {
            "법인사업자": "CORPORATION",
            "개인사업자": "SOLE_PROPRIETOR",
        }.get(str(value), str(value).strip().upper())
    cancelled = value in issue.cancel_values
    profile_update: dict[str, Any] | None = None
    with session_scope() as session:
        confirmation = session.get(Confirmation, state["confirmation_id"])
        if confirmation is not None:
            confirmation.response_json = {
                "issue_code": response.issue_code,
                "value": value,
            }
            confirmation.status = "CANCELLED" if cancelled else "ANSWERED"
        company_id = str(issue.context.get("company_id") or state.get("company_id") or "")
        if company_id and not cancelled:
            try:
                if issue.response_key == "borrower_type":
                    profile_update = update_company_product_profile(
                        session,
                        company_id=company_id,
                        borrower_type=str(value),
                        actor=f"human:{state.get('thread_id', 'studio')}",
                    )
                elif issue.context.get("requirement_code"):
                    profile_update = update_company_product_profile(
                        session,
                        company_id=company_id,
                        case_id=str(issue.context.get("case_id") or "") or None,
                        requirement_code=str(issue.context["requirement_code"]),
                        requirement_satisfied=bool(value),
                        actor=f"human:{state.get('thread_id', 'studio')}",
                    )
            except KeyError:
                # A standalone Thread can still retain the answer even if no
                # reusable KB customer profile exists yet.
                profile_update = None

    answers = [
        *state.get("human_answers", []),
        {
            "confirmation_id": state["confirmation_id"],
            "task_id": task.get("task_id"),
            "issue_code": issue.issue_code,
            "response_key": issue.response_key,
            "value": value,
            "source": "langgraph_interrupt_resume",
            "profile_persisted": profile_update is not None,
        },
    ]
    context = {
        **state.get("human_context", {}),
        issue.response_key: value,
    }
    past_steps = [
        *state.get("past_steps", []),
        {
            "task_id": task.get("task_id"),
            "description": task.get("description", task.get("reason")),
            "agent": "human",
            "status": "CANCELLED" if cancelled else "SUCCESS",
            "result": {
                "issue_code": issue.issue_code,
                "response_key": issue.response_key,
                "value": value,
            },
        },
    ]
    remaining = list(state.get("plan", []))[1:]
    update: dict[str, Any] = {
        "pending_issue": None,
        "human_answers": answers,
        "human_context": context,
        "past_steps": past_steps,
        "plan": remaining,
        "control_task_queue": remaining,
        "current_task": None,
        "current_agent": "",
        "supervisor_phase": "IDLE",
    }
    if issue.response_key == "company_id":
        update["company_id"] = str(value).strip()
    if issue.response_key == "case_id":
        update["case_ids"] = [str(value).strip()]
    if issue.response_key == "borrower_type":
        update["borrower_type"] = str(value)
    if issue.context.get("requirement_code") and isinstance(value, bool):
        requirement_code = str(issue.context["requirement_code"]).strip().upper()
        known_facts = {str(item).upper() for item in state.get("known_facts", [])}
        not_met_facts = {str(item).upper() for item in state.get("not_met_facts", [])}
        if value:
            known_facts.add(requirement_code)
            not_met_facts.discard(requirement_code)
        else:
            not_met_facts.add(requirement_code)
            known_facts.discard(requirement_code)
        update["known_facts"] = sorted(known_facts)
        update["not_met_facts"] = sorted(not_met_facts)
    if cancelled:
        update.update(
            {
                "plan": [],
                "control_task_queue": [],
                "final_response": {
                    "status": "CANCELLED",
                    "reason": "HUMAN_CANCEL_VALUE",
                    "issue_code": issue.issue_code,
                },
                "final_answer": (
                    "입력된 중단 값에 따라 후속 변경·재실행·보고서 생성을 수행하지 않았습니다."
                ),
            }
        )
    return Command(goto="planning_agent", update=update)


def build_common_control_graph(
    *,
    role_graphs: dict[str, Any],
    structured_trade_graph: Any,
    prepare_role: PrepareRole,
    collect_role: CollectRole,
    checkpointer: Any | None = None,
) -> Any:
    """Build the Planning ↔ Supervisor loop with native Tool-owning roles."""

    missing_roles = set(ROLE_NAMES).difference(role_graphs)
    if missing_roles:
        raise ValueError(f"Missing common-control role graphs: {sorted(missing_roles)}")

    def planning_agent(state: TradeFlowState) -> Command:
        user_query = _latest_human_text(state)
        if not state.get("planning_initialized"):
            if not user_query and not state.get("messages"):
                return Command(goto="structured_trade")

            hints = _request_hints(state)
            model_proposed_plan: dict[str, Any] | None = None
            contract_normalized = False
            if get_settings().tradeflow_mode == "offline":
                decision = make_workflow_contract_decision(
                    user_query=user_query,
                    hints=hints,
                )
            else:
                model_decision = make_initial_decision(
                    messages=list(state.get("messages", [])),
                    request_hints=json.dumps(hints, ensure_ascii=False, default=str),
                )
                contract_decision = make_workflow_contract_decision(
                    user_query=user_query,
                    hints=hints,
                )
                decision, contract_normalized = _prefer_typed_workflow_contract(
                    model_decision=model_decision,
                    contract_decision=contract_decision,
                )
                if not isinstance(model_decision.final_output, ConversationalResponse):
                    model_proposed_plan = model_decision.final_output.model_dump(mode="json")

            if isinstance(decision.final_output, ConversationalResponse):
                return Command(
                    goto=END,
                    update=_direct_response_update(
                        user_query,
                        decision.final_output.response,
                    ),
                )

            live_plan = decision.final_output.model_dump(mode="json")
            tasks = _normalize_generated_steps(live_plan)
            plan_payload = _plan_payload(
                user_query=user_query,
                live_plan=live_plan,
                tasks=tasks,
            )
            if model_proposed_plan is not None:
                plan_payload["model_proposed_plan"] = model_proposed_plan
                plan_payload["contract_normalized"] = contract_normalized
            request_id = state.get("request_id") or (
                f"CONTROL-{hashlib.sha256(user_query.encode()).hexdigest()[:12]}"
            )
            planning_log = [
                {
                    "iteration": 1,
                    "action": "CREATE_PLAN",
                    "workflow_kind": live_plan["mission_type"],
                    "remaining_task_ids": [task["task_id"] for task in tasks],
                    "request_hints": hints,
                    "execution_policy": (
                        "Live Planning proposal normalized by typed workflow contract "
                        "and Tool ownership"
                        if contract_normalized
                        else "Typed request-derived plan validated against Tool ownership"
                    ),
                    "model_mission_type": (
                        model_proposed_plan.get("mission_type")
                        if model_proposed_plan is not None
                        else None
                    ),
                    "contract_normalized": contract_normalized,
                }
            ]
            return Command(
                goto="supervisor_agent",
                update={
                    "planning_initialized": True,
                    "planning_iteration": 1,
                    "planning_log": planning_log,
                    "intent": live_plan["mission_type"],
                    "user_query": user_query,
                    "request_id": request_id,
                    "thread_id": state.get("thread_id") or request_id,
                    "mission_type": live_plan["mission_type"],
                    "case_ids": list(live_plan.get("case_ids", [])),
                    "selected_agents": list(plan_payload["selected_agents"]),
                    "execution_plan": plan_payload,
                    "tool_decisions": list(plan_payload["steps"]),
                    "plan": tasks,
                    "original_plan": tasks,
                    "control_task_queue": tasks,
                    "past_steps": [],
                    "task_results": {},
                    "execution_log": [],
                    "review_log": [],
                    "supervisor_log": [],
                    "human_answers": list(state.get("human_answers", [])),
                    "human_context": dict(state.get("human_context", {})),
                    "pending_issue": None,
                    "current_task": None,
                    "current_agent": "",
                    "supervisor_phase": "IDLE",
                    "final_answer": "",
                    "final_response": {},
                },
            )

        remaining = list(state.get("plan", []))
        next_iteration = int(state.get("planning_iteration", 1)) + 1
        planning_log = list(state.get("planning_log", []))
        if remaining:
            decision_summary = "Remaining validated plan exists."
            decision_action = "CONTINUE"
            settings = get_settings()
            if settings.tradeflow_mode == "live" and settings.live_replanning_model:
                decision = make_replanning_decision(
                    user_query=state.get("user_query", ""),
                    remaining_plan=json.dumps(remaining, ensure_ascii=False, default=str),
                    past_steps=json.dumps(
                        state.get("past_steps", []),
                        ensure_ascii=False,
                        default=str,
                    ),
                )
                decision_summary = decision.summary
                decision_action = decision.action
            planning_log.append(
                {
                    "iteration": next_iteration,
                    "action": "RECHECK_PLAN",
                    "model_action": decision_action,
                    "decision": "CONTINUE",
                    "summary": decision_summary,
                    "remaining_task_ids": [task["task_id"] for task in remaining],
                }
            )
            return Command(
                goto="supervisor_agent",
                update={
                    "planning_iteration": next_iteration,
                    "planning_log": planning_log,
                },
            )

        planning_log.append(
            {
                "iteration": next_iteration,
                "action": "FINAL",
                "remaining_task_ids": [],
            }
        )
        finalized = _finalize_from_planning(
            cast(
                TradeFlowState,
                {
                    **state,
                    "planning_iteration": next_iteration,
                    "planning_log": planning_log,
                },
            )
        )
        return Command(
            goto=END,
            update={
                **finalized,
                "planning_iteration": next_iteration,
                "planning_log": planning_log,
            },
        )

    def supervisor_agent(state: TradeFlowState) -> Command:
        if state.get("supervisor_phase") == "AWAITING_RESULT":
            role = str(state["current_agent"])
            task = dict(state.get("current_task") or {})
            results, cleanup = collect_role(role, state)
            structured = results[role]
            result = _tool_result(structured)
            task_results = dict(state.get("task_results", {}))
            task_results[str(task["task_id"])] = result
            execution_log = [
                *state.get("execution_log", []),
                {
                    "sequence": len(state.get("execution_log", [])) + 1,
                    "task_id": task.get("task_id"),
                    "agent": role,
                    "tool": task.get("tool"),
                    "reason": task.get("reason"),
                    "called_tools": structured.get("called_tools", []),
                    "native_tool_chain": structured.get("native_tool_chain", []),
                    "timing_ms": structured.get("timing_ms", 0.0),
                    "status": structured.get("status", "UNKNOWN"),
                },
            ]
            review = _review_specialist_result(
                state,
                role=role,
                task=task,
                structured=structured,
                result=result,
            )
            review_log = [*state.get("review_log", []), review]
            supervisor_log = [
                *state.get("supervisor_log", []),
                {
                    "sequence": len(state.get("supervisor_log", [])) + 1,
                    "phase": "COLLECT",
                    "task_id": task.get("task_id"),
                    "agent": role,
                    "tool": task.get("tool"),
                    "decision": review["decision"],
                    "returns_to": "planning_agent",
                },
            ]
            common_update: dict[str, Any] = {
                **cleanup,
                "called_agents": [role],
                "agent_results": results,
                "task_results": task_results,
                "execution_log": execution_log,
                "review_log": review_log,
                "supervisor_log": supervisor_log,
                "supervisor_phase": "IDLE",
                "current_task": None,
                "current_agent": "",
            }
            if review["decision"] == "STOP":
                return Command(
                    goto="planning_agent",
                    update={
                        **common_update,
                        "plan": [],
                        "control_task_queue": [],
                        "final_response": {
                            "status": "FAILED",
                            "reason": "SUPERVISOR_REVIEW_STOP",
                            "review": review,
                        },
                        "final_answer": (
                            "Supervisor가 계획된 Tool 실행 또는 Critic 검증 실패를 "
                            "확인해 작업을 중단했습니다."
                        ),
                    },
                )

            remaining = list(state.get("plan", []))[1:]
            remaining, discovered_case_ids = _inject_data_driven_followups(
                state=state,
                task=task,
                result=result,
                remaining=remaining,
            )
            past_steps = [
                *state.get("past_steps", []),
                {
                    "task_id": task.get("task_id"),
                    "description": task.get("description", task.get("reason")),
                    "agent": role,
                    "tool": task.get("tool"),
                    "status": structured.get("status", "UNKNOWN"),
                    "result": result,
                },
            ]
            known_decisions = {str(item.get("task_id")) for item in state.get("tool_decisions", [])}
            dynamic_decisions = [
                {
                    "order": len(state.get("tool_decisions", [])) + index,
                    "task_id": item["task_id"],
                    "agent": item["agent"],
                    "tool": item["tool"],
                    "reason": item["reason"],
                    "human_issue": item.get("human_issue"),
                    "source": "TOOL_RESULT_EXPANSION",
                }
                for index, item in enumerate(remaining, start=1)
                if str(item.get("task_id")) not in known_decisions
            ]
            return Command(
                goto="planning_agent",
                update={
                    **common_update,
                    "plan": remaining,
                    "control_task_queue": remaining,
                    "past_steps": past_steps,
                    "case_ids": list(
                        dict.fromkeys([*state.get("case_ids", []), *discovered_case_ids])
                    ),
                    "tool_decisions": [
                        *state.get("tool_decisions", []),
                        *dynamic_decisions,
                    ],
                },
            )

        remaining = list(state.get("plan", []))
        if not remaining:
            return Command(goto="planning_agent")
        task = dict(remaining[0])
        if task.get("agent") == "human":
            HumanIssue.model_validate(task.get("human_issue"))
            return Command(
                goto="human_question",
                update={
                    "current_task": task,
                    "current_agent": "human",
                    "supervisor_phase": "AWAITING_HUMAN",
                    "supervisor_log": [
                        *state.get("supervisor_log", []),
                        {
                            "sequence": len(state.get("supervisor_log", [])) + 1,
                            "phase": "DISPATCH_HUMAN",
                            "task_id": task.get("task_id"),
                            "issue_code": task["human_issue"]["issue_code"],
                            "returns_to": "planning_agent",
                        },
                    ],
                },
            )

        route = route_current_task(task, past_steps=state.get("past_steps", []))
        role = str(route["next"])
        request = {
            "tool_name": task["tool"],
            "args": _resolve_value(task.get("args", {}), state),
        }
        prepared = cast(TradeFlowState, dict(state))
        prepared["agent_inputs"] = {
            **state.get("agent_inputs", {}),
            role: request,
        }
        role_update = prepare_role(role, prepared)
        supervisor_log = [
            *state.get("supervisor_log", []),
            {
                "sequence": len(state.get("supervisor_log", [])) + 1,
                "phase": "DISPATCH",
                "task_id": task.get("task_id"),
                "tool": task.get("tool"),
                "planned_agent": task.get("agent"),
                "selected_agent": role,
                "reason": route["reason"],
                "model_route": route["model_route"],
                "route_overridden": route["route_overridden"],
            },
        ]
        return Command(
            goto=f"{role}_agent",
            update={
                **role_update,
                "current_task": task,
                "current_agent": role,
                "supervisor_phase": "AWAITING_RESULT",
                "supervisor_route": route,
                "supervisor_log": supervisor_log,
            },
        )

    builder = StateGraph(TradeFlowState)
    builder.add_node(
        "planning_agent",
        planning_agent,
        destinations=("supervisor_agent", "structured_trade", END),
    )
    builder.add_node(
        "supervisor_agent",
        supervisor_agent,
        destinations=(
            "planning_agent",
            "human_question",
            *(f"{role}_agent" for role in ROLE_NAMES),
        ),
    )
    for role in ROLE_NAMES:
        builder.add_node(
            f"{role}_agent",
            role_graphs[role],
            destinations=("supervisor_agent",),
        )
        builder.add_edge(f"{role}_agent", "supervisor_agent")
    builder.add_node(
        "human_question",
        human_question,
        destinations=("human_confirmation",),
    )
    builder.add_node(
        "human_confirmation",
        human_confirmation,
        destinations=("planning_agent",),
    )
    builder.add_node(
        "structured_trade",
        structured_trade_graph,
        destinations=(END,),
    )

    builder.add_edge(START, "planning_agent")
    builder.add_edge("human_question", "human_confirmation")
    builder.add_edge("structured_trade", END)
    return builder.compile(checkpointer=checkpointer)

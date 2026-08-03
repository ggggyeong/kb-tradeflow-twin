from __future__ import annotations

import json
from typing import Any, Literal

from langchain_core.messages import HumanMessage
from langchain_core.prompts import ChatPromptTemplate, MessagesPlaceholder
from pydantic import BaseModel, Field

from app.core.config import get_settings
from app.core.model_factory import ModelProfile, get_model

SupervisorDestination = Literal[
    "document_intelligence",
    "trade_case_manager",
    "financial_calendar",
    "shipment_timeline",
    "financial_exposure",
    "product_advisor",
    "report_writer",
    "critic",
]


class SupervisorRoute(BaseModel):
    """One Specialist delegation selected for the current task only."""

    next: SupervisorDestination
    reason: str = Field(description="Public routing reason")


TOOL_OWNERS: dict[str, SupervisorDestination] = {
    "stage_document_intelligence": "document_intelligence",
    "bundle_trade_cases": "trade_case_manager",
    "apply_document_field_override": "trade_case_manager",
    "commit_trade_cases": "trade_case_manager",
    "validate_financial_calendar": "financial_calendar",
    "import_financial_calendar": "financial_calendar",
    "select_financial_monitoring_candidates": "financial_calendar",
    "update_financial_event_link_details": "financial_calendar",
    "read_shipment_snapshot": "shipment_timeline",
    "record_shipment_delay_scenarios": "shipment_timeline",
    "inspect_payment_gate": "financial_exposure",
    "calculate_financial_exposure": "financial_exposure",
    "run_proactive_risk_scan": "financial_exposure",
    "get_financial_risk_snapshot": "financial_exposure",
    "match_product_scenario": "product_advisor",
    "retrieve_product_evidence": "product_advisor",
    "search_product_knowledge": "product_advisor",
    "finalize_manual_monitoring": "report_writer",
    "build_briefing_payload": "report_writer",
    "render_customer_report": "report_writer",
    "render_rm_report": "report_writer",
    "review_workflow_evidence": "critic",
}


SUPERVISOR_SYSTEM_PROMPT = """
당신은 KB TradeFlow Twin의 Supervisor Agent입니다.
전체 계획을 다시 만들지 말고 현재 작업 하나만 관리합니다.

## 팀
- document_intelligence: 업로드 문서 분류·필드 추출·Core 검증과 page evidence
- trade_case_manager: 문서 매칭, 거래 완결성, 명시적 필드 수정과 Case 반영
- financial_calendar: 세션 회사 기준의 정확한 2-Sheet 금융일정 검증, 기존 TradeCase ID 소유권 확인,
  FinancialEvent·TransactionFinancialEventLink만 반영하고 분석 대상 Case 후보를 선택하며,
  충돌 후 명시적으로 확인된 optional 연결 상세만 보완
- shipment_timeline: Booking 계획, B/L 실제 상태와 지연 시나리오
- financial_exposure: 지급조건 Gate, 금융 일정 조회, 지급일·충돌 계산과 결과 저장
- product_advisor: 상품 후보 필터, 로컬 PDF OCR 근거 검색과 citation 옵션
- report_writer: Chat에서 요청한 금일 위험 내부 PDF 또는 Human 동의 뒤 고객용/RM용 보고서 생성
- critic: Tool evidence, 숫자 출처, source scope, 원본 불변성 최종 검증

## 규칙
- 현재 작업의 목적과 Tool을 보고 정확히 한 Agent만 선택합니다.
- 이미 완료된 현재 작업을 다시 위임하지 않습니다.
- Agent가 완료하면 결과를 정리해 Planning Agent로 제어를 돌려보냅니다.
- Tool 소유권과 다른 Agent를 선택하지 않습니다.
- financial_calendar에는 Company·TradeCase·Timeline·PaymentObligation 생성이나 위험 계산을 위임하지 않습니다.
  이 Agent는 정확한 2-Sheet 검증·이벤트/연결 반영·후보 선택과 Human 확인 연결 상세 보완만 담당합니다.
- 직접 계산, DB 변경, 보고서 작성을 수행하지 않습니다.
- 공개 가능한 routing reason만 반환합니다.
""".strip()


SUPERVISOR_PROMPT = ChatPromptTemplate.from_messages(
    [
        ("system", SUPERVISOR_SYSTEM_PROMPT),
        MessagesPlaceholder(variable_name="messages"),
    ]
)


def route_current_task(
    task: dict[str, Any],
    *,
    past_steps: list[dict[str, Any]],
) -> dict[str, str]:
    """Route one task with a live model, then enforce Tool ownership."""
    tool_name = str(task.get("tool", ""))
    owner = TOOL_OWNERS.get(tool_name)
    if owner is None:
        raise ValueError(f"Supervisor has no owner for Tool: {tool_name}")

    settings = get_settings()
    if settings.tradeflow_mode == "offline" or not settings.live_supervisor_routing_model:
        return {
            "next": owner,
            "reason": f"Validated Tool ownership routes {tool_name} to {owner}",
            "model_route": owner,
            "route_overridden": "false",
        }

    chain = SUPERVISOR_PROMPT | get_model(ModelProfile.SUPERVISOR).with_structured_output(
        SupervisorRoute,
        method="function_calling",
    )
    decision: SupervisorRoute = chain.invoke(
        {
            "messages": [
                HumanMessage(
                    content=(
                        "현재 작업:\n"
                        f"{json.dumps(task, ensure_ascii=False, default=str)}\n\n"
                        "최근 완료 작업:\n"
                        f"{json.dumps(past_steps[-3:], ensure_ascii=False, default=str)}"
                    )
                )
            ]
        }
    )
    selected = decision.next
    overridden = selected != owner
    return {
        "next": owner if overridden else selected,
        "reason": (
            f"{decision.reason} Tool 소유권 검증으로 {owner}에 위임합니다."
            if overridden
            else decision.reason
        ),
        "model_route": selected,
        "route_overridden": str(overridden).lower(),
    }

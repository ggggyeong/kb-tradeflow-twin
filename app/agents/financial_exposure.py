from __future__ import annotations

from typing import Any

from langchain.agents import create_agent as langchain_create_agent
from langchain.agents.structured_output import ToolStrategy
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.prompts import ChatPromptTemplate, MessagesPlaceholder

from app.agents.common import COMMON_TRADEFLOW_AGENT_CONTRACT, requested_tool_contract
from app.core.model_factory import ModelProfile, get_model
from app.schemas.agent import AgentResult
from app.tools.financial_exposure import (
    calculate_financial_exposure,
    get_financial_risk_snapshot,
    inspect_payment_gate,
    run_proactive_risk_scan,
)

SYSTEM_PROMPT = """
# KB TradeFlow Twin - Financial Exposure Specialist
Prompt Version: financial-exposure.v19.0

# 역할
PaymentTerms Gate를 판단하고 금융 일정 DB를 조회해 지급일·충돌을 계산한 뒤
CalculationResult를 저장한다.

# 성공 조건
calculation_allowed와 verified를 분리하고, 미검증 시 계산 0건을 보장하며,
허용된 경우에만 금융 노출 결과를 저장한다.

# Source of Truth
PaymentTerms XLSX Provider와 payment_obligation, 금융일정 2-Sheet의 회사 event 6개 필드와
거래 link 4개 필드, 최소 문서 projection, Shipment State/Timeline Agent가 저장한
Booking ETD 기반 예상 출항일 시나리오, Financial ToolResult다.

# Tool 선택
계산 가능 여부는 inspect_payment_gate로 검사하고, Human 확인 뒤
calculate_financial_exposure로 사용자 지연 위험을 계산·저장한다. 금일 선제 분석에는
run_proactive_risk_scan, 저장 결과의 공통 재조회에는 get_financial_risk_snapshot을 사용한다.

# 절차와 정지 조건
Gate 통과 전 날짜·충돌 계산을 요청하지 않으며 Shipment State/Timeline Agent 내부에서
금융 계산이 수행된 결과는 Source of Truth로 인정하지 않는다.

# 확인·handoff
최소 문서 계약은 계산 가능한 선적 기준일로 On-board date만 저장한다. T/T B/L DATE는
On-board date 적용 여부를 한 번 질문한다. 실제 충돌에서 dependency_scope가 미확정이면
먼저 범위를 묻고, PARTIAL일 때만 linked_amount와 linked_currency를 이어서 확인한다.

# 절대 금지
날짜·금액을 LLM이 직접 계산하거나, Shipment State/Timeline Agent에 금융 계산을 위임하거나,
What-if로 원본 선적 상태를 수정하지 않는다.

# 출력
AgentResult에 gate, missing field, scenario status, evidence를 담는다.

# Debug Trace
raw, parsed, missing, question, verified, calculate_called를 남긴다.

# 예시
정상: AFTER_CONFIRMATION+calculate_called=false. 차단: AT SIGHT 날짜 계산.
""".strip()

AGENT_PROMPT = ChatPromptTemplate.from_messages(
    [
        ("system", f"{SYSTEM_PROMPT}\n\n{COMMON_TRADEFLOW_AGENT_CONTRACT}"),
        MessagesPlaceholder(variable_name="messages"),
    ]
)


def build_agent(model: BaseChatModel | None = None) -> Any:
    """Build the native Financial Exposure Specialist agent."""
    system_message = AGENT_PROMPT.format_messages(messages=[])[0]
    return langchain_create_agent(
        model=model or get_model(ModelProfile.FINANCIAL),
        tools=[
            inspect_payment_gate,
            calculate_financial_exposure,
            run_proactive_risk_scan,
            get_financial_risk_snapshot,
        ],
        system_prompt=system_message,
        response_format=ToolStrategy(AgentResult),
        middleware=[requested_tool_contract],
        name="financial_exposure",
    )

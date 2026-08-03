from __future__ import annotations

from typing import Any

from langchain.agents import create_agent as langchain_create_agent
from langchain.agents.structured_output import ToolStrategy
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.prompts import ChatPromptTemplate, MessagesPlaceholder

from app.agents.common import COMMON_TRADEFLOW_AGENT_CONTRACT, requested_tool_contract
from app.core.model_factory import ModelProfile, get_model
from app.schemas.agent import AgentResult
from app.tools.shipment import read_shipment_snapshot
from app.tools.workflows import record_shipment_delay_scenarios

SYSTEM_PROMPT = """
# KB TradeFlow Twin - Shipment State/Timeline Agent
Prompt Version: shipment-timeline.v18.0

# 역할
Booking planned와 B/L actual을 분리해 문서 lifecycle, 실제 출항 확인 상태,
조건부 예상 출항일 시나리오를 설명한다.

# 성공 조건
확인 사실·미확인 사실·허용 가능한 상태 표현을 분리하고, 금융 계산 없이
선적 시나리오만 저장한다.

# Source of Truth
case_id로 조회한 shipment snapshot, document evidence,
read_shipment_snapshot ToolResult다. transaction_id는 금융일정 연결 식별자이며
case_id와 서로 바꾸어 쓰지 않는다.

# Tool 선택
현재 저장 상태를 확인할 때 read_shipment_snapshot을 호출하고, 사용자 지연 제보를
조건부 시나리오로 남길 때 record_shipment_delay_scenarios를 호출한다.

# 절차와 정지 조건
planned/actual을 비교한다. 저장 status는 문서 lifecycle로 해석하고,
departure_status는 on_board_date가 있을 때만 CONFIRMED_DEPARTED로 해석한다.

# 확인·handoff
ETD 경과+B/L 없음+on_board_date 없음이면 출항 여부를 한 번 질문한다.

# 절대 금지
B/L 없음만으로 미선적을 확정하거나, Booking ETD+delay를 실제 On-board date나
B/L 발행일로 확정하거나, 지급일·금융 충돌·우선순위를 계산하지 않는다.

# 출력
AgentResult에 state, facts, unknowns, evidence를 담는다.

# Debug Trace
planned, actual, status transition, call ID를 남긴다.

# 예시
정상: AWAITING_BILL_OF_LADING + departure_status UNKNOWN.
차단: B/L 없음이므로 NOT_DEPARTED.
""".strip()

AGENT_PROMPT = ChatPromptTemplate.from_messages(
    [
        ("system", f"{SYSTEM_PROMPT}\n\n{COMMON_TRADEFLOW_AGENT_CONTRACT}"),
        MessagesPlaceholder(variable_name="messages"),
    ]
)


def build_agent(model: BaseChatModel | None = None) -> Any:
    """Build the native Shipment State/Timeline Agent."""
    system_message = AGENT_PROMPT.format_messages(messages=[])[0]
    return langchain_create_agent(
        model=model or get_model(ModelProfile.SHIPMENT),
        tools=[
            read_shipment_snapshot,
            record_shipment_delay_scenarios,
        ],
        system_prompt=system_message,
        response_format=ToolStrategy(AgentResult),
        middleware=[requested_tool_contract],
        name="shipment_timeline",
    )

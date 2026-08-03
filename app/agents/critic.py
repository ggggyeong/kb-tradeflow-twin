from __future__ import annotations

from typing import Any

from langchain.agents import create_agent as langchain_create_agent
from langchain.agents.structured_output import ToolStrategy
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.prompts import ChatPromptTemplate, MessagesPlaceholder

from app.agents.common import COMMON_TRADEFLOW_AGENT_CONTRACT, requested_tool_contract
from app.core.model_factory import ModelProfile, get_model
from app.schemas.agent import AgentResult
from app.tools.workflows import review_workflow_evidence

SYSTEM_PROMPT = """
# KB TradeFlow Twin - Independent Critic
Prompt Version: independent-critic.v16.0

# 역할
숫자, Evidence, Source Scope, 조건부 표현, audience 경계를 독립 검증한다.

# 성공 조건
PASS, HUMAN_REQUIRED 또는 최대 한 번의 REPLAN을 근거와 함께 반환한다.

# Source of Truth
Specialist ToolResult와 review_workflow_evidence 결과다.

# Tool 선택
workflow별 필수 결과, evidence와 안전 규칙을 review_workflow_evidence로 검사한다.

# 절차와 정지 조건
한 번 검증하고 동일 오류의 두 번째 재계획을 금지한다.

# 확인·정지
필수 근거가 없으면 HUMAN_REQUIRED로 넘긴다.

# 절대 금지
누락 근거를 스스로 보충하거나 Specialist Tool을 우회하지 않는다.

# 출력
AgentResult에 verdict, reasons, replan_count를 담는다.

# Debug Trace
검사 항목, verdict, evidence gaps, call ID를 남긴다.

# 예시
정상: evidence 존재 PASS. 차단: 출처 없는 날짜를 PASS.
""".strip()

AGENT_PROMPT = ChatPromptTemplate.from_messages(
    [
        ("system", f"{SYSTEM_PROMPT}\n\n{COMMON_TRADEFLOW_AGENT_CONTRACT}"),
        MessagesPlaceholder(variable_name="messages"),
    ]
)


def build_agent(model: BaseChatModel | None = None) -> Any:
    """Build the native Independent Critic agent."""
    system_message = AGENT_PROMPT.format_messages(messages=[])[0]
    return langchain_create_agent(
        model=model or get_model(ModelProfile.CRITIC),
        tools=[review_workflow_evidence],
        system_prompt=system_message,
        response_format=ToolStrategy(AgentResult),
        middleware=[requested_tool_contract],
        name="independent_critic",
    )

from __future__ import annotations

from typing import Any

from langchain.agents import create_agent as langchain_create_agent
from langchain.agents.structured_output import ToolStrategy
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.prompts import ChatPromptTemplate, MessagesPlaceholder

from app.agents.common import COMMON_TRADEFLOW_AGENT_CONTRACT, requested_tool_contract
from app.core.model_factory import ModelProfile, get_model
from app.schemas.agent import AgentResult
from app.tools.reports import (
    build_briefing_payload,
    finalize_manual_monitoring,
    render_customer_report,
    render_rm_report,
)

SYSTEM_PROMPT = """
# KB TradeFlow Twin - Report Writer
Prompt Version: report-writer.v17.0

# 역할
검증이 끝난 동일 calculation basis를 고객용과 KB 직원용 두 audience로 분리해
보고서 payload와 audience별 파일을 별도 Tool로 생성한다.

# 성공 조건
Human consent가 true이고, 고객용/RM용 보고서가 같은 basis_version을 사용하며,
두 파일 경로와 report ID가 ToolResult에 존재해야 한다.

# Source of Truth
build_briefing_payload와 audience render ToolResult, 이전 Specialist의 검증된
task_results, Human consent만 사용한다.

# Tool 선택
payload 구성, 고객용 렌더, KB 직원용 렌더를 각각 소유 Tool로 수행한다.

# Chat 수동 금일 분석
메뉴에서 시작한 금일 분석은 finalize_manual_monitoring으로 Critic PASS 결과만
MonitoringRun·Alert·내부 PDF에 봉인한다. 위험 계산은 다시 하지 않는다.

# 고객용
상황, 확인된 사실, 조건부 시나리오, 우선 확인사항, 상담 다음 단계를 평이하게 쓴다.

# KB 직원용
Case 식별, evidence, 금융 충돌, 계산 basis, 고객 선택, 상담 체크리스트,
주의 문구를 포함한다.

# 절대 금지
고객/RM 보고서의 동의 전 파일 생성, 새로운 날짜·금액 계산,
고객/RM 보고서의 basis 불일치,
검증되지 않은 상품 자격·가격·승인을 확정하지 않는다.

# 출력
AgentResult에 customer_report_path, rm_report_path, report_ids, same_basis 또는
finalize_manual_monitoring 결과의 daily_report_id, daily_summary, pdf_path를 포함한다.
""".strip()


AGENT_PROMPT = ChatPromptTemplate.from_messages(
    [
        ("system", f"{SYSTEM_PROMPT}\n\n{COMMON_TRADEFLOW_AGENT_CONTRACT}"),
        MessagesPlaceholder(variable_name="messages"),
    ]
)


def build_agent(model: BaseChatModel | None = None) -> Any:
    """Build the native Report Writer agent."""
    system_message = AGENT_PROMPT.format_messages(messages=[])[0]
    return langchain_create_agent(
        model=model or get_model(ModelProfile.REPORT_WRITER),
        tools=[
            finalize_manual_monitoring,
            build_briefing_payload,
            render_customer_report,
            render_rm_report,
        ],
        system_prompt=system_message,
        response_format=ToolStrategy(AgentResult),
        middleware=[requested_tool_contract],
        name="report_writer",
    )

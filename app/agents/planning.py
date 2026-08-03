from __future__ import annotations

from typing import Any

from langchain_core.messages import BaseMessage
from langchain_core.prompts import ChatPromptTemplate, MessagesPlaceholder

from app.core.model_factory import ModelProfile, get_model
from app.schemas.planning import (
    ConversationalResponse,
    PlanningResponse,
    ReplanningDecision,
)

TEAM_AND_TOOL_CATALOG = """
- document_intelligence
  - stage_document_intelligence
- trade_case_manager
  - bundle_trade_cases
  - apply_document_field_override
  - commit_trade_cases
- financial_calendar
  - validate_financial_calendar
  - import_financial_calendar
  - select_financial_monitoring_candidates
  - update_financial_event_link_details
- shipment_timeline
  - read_shipment_snapshot
  - record_shipment_delay_scenarios
- financial_exposure
  - inspect_payment_gate
  - calculate_financial_exposure
  - run_proactive_risk_scan
  - get_financial_risk_snapshot
- product_advisor
  - match_product_scenario
  - retrieve_product_evidence
  - search_product_knowledge
- report_writer
  - finalize_manual_monitoring
  - build_briefing_payload
  - render_customer_report
  - render_rm_report
- critic
  - review_workflow_evidence
- human
  - interrupt/resume
""".strip()


PLANNING_SYSTEM_PROMPT = f"""
당신은 KB TradeFlow Twin의 Planning Agent입니다.
사용자의 요청을 분석해 바로 답할지, 여러 전문 Agent가 수행할 계획을 만들지 결정합니다.

## 즉답 규칙
- 인사, 감사, 기능 안내, 짧은 일상 대화는 ConversationalResponse로 즉시 답합니다.
- 거래 DB 조회, 문서 분석, 날짜·금액·충돌 계산, 보고서 생성이 필요한 요청을
  직접 답했다고 가장하지 않습니다.

## 계획 규칙
- 복합 무역 업무는 Plan으로 반환합니다.
- 일반적으로 2~5개의 독립 작업으로 나눕니다.
- Human 확인과 audience별 산출물이 필요한 안전 업무는 최대 12단계까지 허용합니다.
- 각 단계는 하나의 Agent와 하나의 정확한 Tool만 사용해야 합니다.
- DB 변경이나 보고서 생성 전 필요한 Human interrupt/resume 단계를 둡니다.
- Human 단계는 human_issue에 issue_code, response_key, value_type, 허용값을 명시합니다.
- Human 응답으로 계획에 없던 필드를 승인하거나 추론하지 않습니다.
- Tool 실행 결과에서 발견되는 문서 필드 누락, 지급기준 문제, 충돌 후 금융 연결
  상세 보완은 런타임이
  정확한 Human 단계를 삽입하므로 포괄적인 사전 확인 단계를 만들지 않습니다.
- 고객 맞춤 상품 요청은 저장된 Financial Exposure snapshot을 먼저 조회하고, 사용자가
  위험 상황을 다시 설명하게 하지 않습니다.
- 상품 필터의 customer_role은 거래 문서에서 판정하며 borrower_type은 고객 DB에 없을
  때만 한 번, known_facts는 실제 후보의 필수조건만 런타임이 질문합니다.
- 업로드 분석은 stage_document_intelligence → bundle_trade_cases →
  commit_trade_cases → review_workflow_evidence 순서로 계획합니다.
- 복합 workflow의 최종 검증에는 review_workflow_evidence를 사용합니다.
- 최종 업무 단계에는 critic 검증을 포함합니다.
- 이미 완료된 작업을 다시 계획하지 않습니다.
- 숨겨진 사고과정은 쓰지 말고 공개 가능한 reason만 작성합니다.

## 팀과 Tool
{TEAM_AND_TOOL_CATALOG}

## 요청에서 추출된 구조화 힌트
{{request_hints}}
""".strip()


PLANNING_PROMPT = ChatPromptTemplate.from_messages(
    [
        ("system", PLANNING_SYSTEM_PROMPT),
        MessagesPlaceholder(variable_name="messages"),
    ]
)


REPLANNING_PROMPT = ChatPromptTemplate.from_messages(
    [
        (
            "system",
            f"""
당신은 KB TradeFlow Twin의 Planning Agent입니다.
Supervisor가 한 작업을 완료하고 제어를 돌려줬습니다.

사용자 목표, 남은 계획, 완료 결과를 확인해 다음을 선택하세요.
- 남은 계획이 있으면 CONTINUE
- 남은 계획이 없고 결과가 충분하면 FINAL

완료된 작업을 다시 추가하거나 Tool 결과에 없는 사실을 만들지 마세요.
이 노드는 실행 순서를 재확인하는 곳이며 Specialist Tool을 직접 호출하지 않습니다.

팀과 Tool:
{TEAM_AND_TOOL_CATALOG}

사용자 목표:
{{user_query}}

남은 계획:
{{remaining_plan}}

완료된 작업:
{{past_steps}}
""".strip(),
        ),
        MessagesPlaceholder(variable_name="messages"),
    ]
)


FINAL_ANSWER_PROMPT = ChatPromptTemplate.from_messages(
    [
        (
            "system",
            """
당신은 KB TradeFlow Twin의 Planning Agent입니다.
모든 계획이 완료되었습니다. 제공된 Tool 결과만 사용해 사용자의 최종 답변을
간결하고 명확한 한국어로 작성하세요.

- 날짜·금액·상태를 새로 계산하거나 추정하지 마세요.
- UNKNOWN, 조건부, 근거 상태 같은 안전 표시는 유지하세요.
- 상품 상태와 확인 요건은 availability_label과 missing_requirements_display의
  한글 문구만 사용하고, availability·missing_requirements의 내부 영문 코드는
  사용자 답변에 노출하지 마세요.
- Human이 선택하거나 승인한 내용을 반영하세요.
- 고객용/RM용 파일이 생성된 경우 두 산출물을 구분하세요.

사용자 요청:
{user_query}

완료 결과:
{grounded_results}
""".strip(),
        ),
        MessagesPlaceholder(variable_name="messages"),
    ]
)


def make_initial_decision(
    *,
    messages: list[BaseMessage],
    request_hints: str,
) -> PlanningResponse:
    """Run the live structured Planning prompt."""
    chain = PLANNING_PROMPT | get_model(ModelProfile.PLANNING).with_structured_output(
        PlanningResponse,
        method="function_calling",
    )
    return chain.invoke({"messages": messages, "request_hints": request_hints})


def make_replanning_decision(
    *,
    user_query: str,
    remaining_plan: str,
    past_steps: str,
) -> ReplanningDecision:
    """Recheck the remaining plan after one Supervisor cycle."""
    chain = REPLANNING_PROMPT | get_model(ModelProfile.PLANNING).with_structured_output(
        ReplanningDecision,
        method="function_calling",
    )
    return chain.invoke(
        {
            "messages": [],
            "user_query": user_query,
            "remaining_plan": remaining_plan,
            "past_steps": past_steps,
        }
    )


def make_final_answer(*, user_query: str, grounded_results: str) -> str:
    """Synthesize the final answer from Tool-grounded results in live mode."""
    chain = FINAL_ANSWER_PROMPT | get_model(ModelProfile.PLANNING).with_structured_output(
        ConversationalResponse,
        method="function_calling",
    )
    result: ConversationalResponse = chain.invoke(
        {
            "messages": [],
            "user_query": user_query,
            "grounded_results": grounded_results,
        }
    )
    return result.response


def plan_to_dict(response: PlanningResponse) -> dict[str, Any] | None:
    """Serialize a structured plan while keeping direct responses distinct."""
    if isinstance(response.final_output, ConversationalResponse):
        return None
    return response.final_output.model_dump(mode="json")

from __future__ import annotations

from typing import Any

from app.prompts import load_prompt
from app.schemas.orchestration import ExecutionPlan, ModelCall
from app.schemas.portfolio import PortfolioRunRequest
from app.services.llm_controller import RunModel
from app.tools.agent_tools import function_tool, invoke, message, observe


class Supervisor:
    """One agent owns both planning and delegation; there is no separate Planner."""

    def plan(self, request: PortfolioRunRequest, model: RunModel) -> ExecutionPlan:
        messages = [
            message(
                {
                    "user_request": request.user_request,
                    "document_count": len(request.document_paths),
                    "financial_event_count": len(request.financial_events),
                    "has_financial_calendar": request.financial_calendar_path is not None,
                }
            )
        ]
        call = invoke(
            model,
            "supervisor",
            messages,
            function_tool(
                "submit_plan", "요청 범위와 입력에 맞는 실행 계획을 제출합니다.", ExecutionPlan
            ),
        )
        plan = ExecutionPlan.model_validate(call.arguments)
        if "call_document_agent" in plan.steps and not request.document_paths:
            raise ValueError("문서 없는 분석 계획은 실행할 수 없습니다.")
        if "call_finance_agent" in plan.steps and not (
            request.financial_events or request.financial_calendar_path
        ):
            raise ValueError("금융일정 없는 비교 계획은 실행할 수 없습니다.")
        observe(model, messages, call, {"status": plan.status})
        return plan

    def run(
        self, messages: list[dict[str, Any]], tools: list[dict[str, Any]], model: RunModel
    ) -> ModelCall:
        return model.call(instructions=load_prompt("supervisor"), messages=messages, tools=tools)

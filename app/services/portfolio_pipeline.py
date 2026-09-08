from __future__ import annotations

from collections.abc import Callable
from datetime import date
from typing import Any

from app.agents.document_agent import DocumentAgent
from app.agents.finance_advisor import FinanceAdvisorAgent
from app.agents.supervisor import Supervisor
from app.graphs.state import PortfolioState
from app.schemas.orchestration import ToolAudit, ToolName
from app.schemas.portfolio import PortfolioReportPayload, PortfolioRunRequest, PortfolioRunResult
from app.services.llm_controller import (
    LLMError,
    LLMSettings,
    OpenAIToolCallingModel,
    RunModel,
    ToolCallingModel,
)
from app.services.report_generator import PortfolioReportGenerator
from app.services.service_policy import build_service_cards
from app.tools.agent_tools import message
from app.tools.portfolio_tools import tool_definition


class PortfolioPipeline:
    """Three agents, deterministic tools, and per-request state/call budgets."""

    def __init__(
        self,
        *,
        model: ToolCallingModel | None = None,
        settings: LLMSettings | None = None,
        document_agent: DocumentAgent | None = None,
        finance_agent: FinanceAdvisorAgent | None = None,
        report_generator: PortfolioReportGenerator | None = None,
        today: Callable[[], date] = date.today,
    ) -> None:
        self.settings = settings or LLMSettings.from_env()
        self.model = model or OpenAIToolCallingModel(self.settings)
        self.supervisor = Supervisor()
        self.document_agent = document_agent or DocumentAgent()
        self.finance_agent = finance_agent or FinanceAdvisorAgent()
        self.report_generator = report_generator or PortfolioReportGenerator()
        self.today = today

    @staticmethod
    def request(state: PortfolioState) -> PortfolioRunRequest:
        return PortfolioRunRequest.model_validate(state["request"])

    def prepare_step(self, state: PortfolioState) -> dict[str, Any]:
        request = self.request(state)
        return {
            "documents": [],
            "conflicts": [],
            "product_options": [],
            "service_cards": [],
            "retrieval_trace": [],
            "warnings": [],
            "plan": None,
            "pending_call": None,
            "messages": [],
            "completed": {},
            "expected_receipt_date": request.expected_receipt_date,
            "llm_calls": 0,
            "input_tokens": 0,
            "output_tokens": 0,
            "fatal_error": None,
            "trace": ["prepare"],
        }

    @staticmethod
    def _availability(state: PortfolioState) -> list[ToolName]:
        plan = state.get("plan")
        if plan is None or plan.status != "READY":
            return []
        done = state.get("completed", {})
        # Only the next planned stage is callable. Failed document extraction does not
        # prevent checking separately confirmed user schedules, but is carried as a warning.
        return next(([tool] for tool in plan.steps if tool not in done), [])

    def supervisor_step(self, state: PortfolioState) -> dict[str, Any]:
        update: dict[str, Any] = {"trace": ["supervisor"], "next_node": "finalize"}
        if state.get("fatal_error"):
            return update
        runtime = self._runtime(state)
        try:
            if state.get("plan") is None:
                plan = self.supervisor.plan(self.request(state), runtime)
                update["plan"] = plan
                if plan.status != "READY":
                    update["warnings"] = [plan.reason]
                else:
                    update["next_node"] = "supervisor"
                return update
            ready = self._availability(state)
            if not ready:
                return update
            messages = list(state.get("messages", []))
            current_plan = state["plan"]
            assert current_plan is not None
            messages.append(
                message(
                    {
                        "plan": current_plan.model_dump(),
                        "completed": state.get("completed", {}),
                        "allowed_tools": ready,
                    }
                )
            )
            call = self.supervisor.run(messages, [tool_definition(name) for name in ready], runtime)
            messages.extend(call.response_items)
            update.update(pending_call=call, messages=messages, next_node="tools")
        except (LLMError, ValueError) as exc:
            update["fatal_error"] = (
                str(exc) if isinstance(exc, LLMError) else "Supervisor 실행 계획 검증 실패"
            )
        finally:
            update.update(self._usage(runtime))
        return update

    def _runtime(self, state: PortfolioState) -> RunModel:
        runtime = RunModel(self.model, self.settings.max_calls)
        runtime.calls = state.get("llm_calls", 0)
        runtime.input_tokens = state.get("input_tokens", 0)
        runtime.output_tokens = state.get("output_tokens", 0)
        return runtime

    @staticmethod
    def _usage(runtime: RunModel) -> dict[str, Any]:
        return {
            "llm_calls": runtime.calls,
            "input_tokens": runtime.input_tokens,
            "output_tokens": runtime.output_tokens,
            "tool_audit": runtime.audit,
        }

    def tool_step(self, state: PortfolioState) -> dict[str, Any]:
        import json

        call = state.get("pending_call")
        if call is None:
            return {"fatal_error": "실행할 도구 호출이 없습니다."}
        ready = self._availability(state)
        done = dict(state.get("completed", {}))
        warnings = list(state.get("warnings", []))
        update: dict[str, Any] = {"pending_call": None, "trace": [f"tool:{call.name}"]}
        runtime = self._runtime(state)
        if call.name not in ready or call.arguments:
            audit = ToolAudit(
                tool=call.name,
                status="REJECTED",
                call_id=call.call_id,
                message="허용되지 않은 도구·순서·중복 호출 또는 임의 인자를 거부했습니다.",
            )
            warnings.append(audit.message)
        else:
            try:
                result = self._execute(call.name, state, runtime)
                warnings.extend(result.pop("warnings", []))
                update.update(result)
                done[call.name] = "SUCCESS"
                audit = ToolAudit(
                    tool=call.name, status="SUCCESS", call_id=call.call_id, message="도구 실행 완료"
                )
            except Exception as exc:
                done[call.name] = "FAILED"
                audit = ToolAudit(
                    tool=call.name,
                    status="FAILED",
                    call_id=call.call_id,
                    message=f"{call.name} 실행 실패. 입력·모델·출처 검증 상태를 확인해 주세요.",
                )
                warnings.append(audit.message)
                if isinstance(exc, LLMError):
                    update["fatal_error"] = str(exc)
        runtime.audit.append(audit)
        effective = {**state, **update}
        observation = {
            "status": audit.status,
            "document_statuses": [x.status for x in effective.get("documents", [])],
            "conflict_statuses": [x.status for x in effective.get("conflicts", [])],
            "product_count": len(effective.get("product_options", [])),
            "report_created": bool(effective.get("report_path")),
        }
        messages = list(state.get("messages", []))
        messages.append(
            {
                "type": "function_call_output",
                "call_id": call.call_id,
                "output": json.dumps(observation, ensure_ascii=False),
            }
        )
        update.update(completed=done, messages=messages, warnings=warnings)
        update.update(self._usage(runtime))
        return update

    def _execute(self, name: str, state: PortfolioState, runtime: RunModel) -> dict[str, Any]:
        request = self.request(state)
        if name == "call_document_agent":
            return {"documents": self.document_agent.run(request.document_paths, model=runtime)}
        if name == "call_finance_agent":
            effective, receipt, input_warnings = self.finance_agent.prepare_inputs(
                request, state["documents"], model=runtime
            )
            conflicts, options, warnings = self.finance_agent.run(
                effective, state["documents"], model=runtime
            )
            return {
                "conflicts": conflicts,
                "product_options": options,
                "service_cards": build_service_cards(conflicts, options),
                "retrieval_trace": list(
                    getattr(getattr(self.finance_agent, "retrieval", None), "last_search_trace", [])
                ),
                "warnings": [*input_warnings, *warnings],
                "expected_receipt_date": receipt.expected_receipt_date,
                "receipt_resolution": receipt,
            }
        if name == "generate_report":
            payload = PortfolioReportPayload(
                generated_on=self.today(),
                expected_receipt_date=state.get("expected_receipt_date"),
                receipt_resolution=state.get("receipt_resolution"),
                documents=state["documents"],
                conflicts=state["conflicts"],
                product_options=state["product_options"],
                service_cards=state.get("service_cards", []),
                retrieval_trace=state.get("retrieval_trace", []),
                warnings=self._warnings(state),
            )
            report = self.report_generator.generate(payload, request.report_output_path)
            return {"report_path": str(report["report_path"])}
        raise ValueError("등록되지 않은 도구")

    @staticmethod
    def _warnings(state: PortfolioState) -> list[str]:
        warnings = list(state.get("warnings", []))
        for item in state.get("documents", []):
            if item.status != "ANALYZED":
                warnings.append(f"문서 확인 필요: {item.file_name}")
            warnings.extend(item.warnings)
        if any(item.status == "REVIEW_REQUIRED" for item in state.get("conflicts", [])):
            warnings.append("금융일정의 날짜·거래 연결·통화·결제 방향 확인이 필요합니다.")
        if state.get("fatal_error"):
            warnings.append(str(state["fatal_error"]))
        return list(dict.fromkeys(warnings))

    def finalize_step(self, state: PortfolioState) -> dict[str, Any]:
        warnings = self._warnings(state)
        failed = state.get("fatal_error") or any(
            v == "FAILED" for v in state.get("completed", {}).values()
        )
        return {
            "status": "FAILED" if failed else "REVIEW_REQUIRED" if warnings else "SUCCESS",
            "warnings": warnings,
            "trace": ["finalize"],
        }

    @staticmethod
    def result(state: PortfolioState) -> PortfolioRunResult:
        return PortfolioRunResult.model_validate(
            {
                "status": state["status"],
                "expected_receipt_date": state.get("expected_receipt_date"),
                "receipt_resolution": state.get("receipt_resolution"),
                "documents": state.get("documents", []),
                "conflicts": state.get("conflicts", []),
                "product_options": state.get("product_options", []),
                "service_cards": state.get("service_cards", []),
                "retrieval_trace": state.get("retrieval_trace", []),
                "report_path": state.get("report_path"),
                "warnings": state.get("warnings", []),
                "trace": state.get("trace", []),
                "plan": state.get("plan"),
                "tool_audit": state.get("tool_audit", []),
                "llm_calls": state.get("llm_calls", 0),
                "input_tokens": state.get("input_tokens", 0),
                "output_tokens": state.get("output_tokens", 0),
            }
        )

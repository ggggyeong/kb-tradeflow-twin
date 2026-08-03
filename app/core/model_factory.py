from __future__ import annotations

import json
from enum import StrEnum
from typing import Any

from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, ToolMessage
from langchain_core.outputs import ChatGeneration, ChatResult
from langchain_openai import ChatOpenAI
from pydantic import PrivateAttr

from app.core.config import get_settings


class ModelProfile(StrEnum):
    """Role-specific model profiles used by planning, routing, and specialists."""

    PLANNING = "planning"
    SUPERVISOR = "supervisor"
    DOCUMENT = "document"
    SHIPMENT = "shipment"
    FINANCIAL = "financial"
    PRODUCT = "product"
    REPORT_WRITER = "report_writer"
    CRITIC = "critic"


class ScriptedToolCallingModel(BaseChatModel):
    """Offline verification model that emits real AIMessage tool calls."""

    role_profile: str
    _bound_tool_names: list[str] = PrivateAttr(default_factory=list)

    @property
    def _llm_type(self) -> str:
        return "tradeflow-scripted-tool-calling"

    @property
    def _identifying_params(self) -> dict[str, Any]:
        return {"role_profile": self.role_profile}

    def bind_tools(
        self,
        tools: Any,
        *,
        tool_choice: str | None = None,
        **kwargs: Any,
    ) -> ScriptedToolCallingModel:
        """Bind LangChain tools while preserving their provider-facing names."""
        del tool_choice, kwargs
        clone = self.model_copy(deep=True)
        names: list[str] = []
        for item in tools:
            if isinstance(item, dict):
                names.append(str(item.get("name") or item.get("function", {}).get("name")))
            else:
                names.append(str(getattr(item, "name", getattr(item, "__name__", ""))))
        clone._bound_tool_names = [name for name in names if name]
        return clone

    def _generate(
        self,
        messages: list[BaseMessage],
        stop: list[str] | None = None,
        run_manager: Any | None = None,
        **kwargs: Any,
    ) -> ChatResult:
        del stop, run_manager, kwargs
        if not self._bound_tool_names:
            latest_human = next(
                (
                    str(message.content).strip()
                    for message in reversed(messages)
                    if isinstance(message, HumanMessage)
                ),
                "",
            )
            normalized = latest_human.lower().replace(" ", "")
            if normalized in {
                "안녕",
                "안녕하세요",
                "하이",
                "hello",
                "hi",
                "반가워",
                "반갑습니다",
            }:
                content = (
                    "안녕하세요! KB TradeFlow Twin입니다. "
                    "편하게 말씀해 주세요. 무역 거래나 금융 일정도 함께 확인해 드릴게요."
                )
            else:
                content = (
                    "네, 말씀해 주세요. 일반적인 대화는 바로 답하고, "
                    "거래 분석이 필요하면 Case ID와 확인할 내용을 알려주세요."
                )
            return ChatResult(generations=[ChatGeneration(message=AIMessage(content=content))])

        tool_messages = [message for message in messages if isinstance(message, ToolMessage)]
        if not tool_messages:
            requested = self._parse_requested_tool(messages)
            tool_name = requested.get("tool_name") or self._first_business_tool()
            args = requested.get("args", {})
            call_id = f"call_{self.role_profile}_{len(messages)}"
            message = AIMessage(
                content="",
                tool_calls=[
                    {
                        "name": str(tool_name),
                        "args": args if isinstance(args, dict) else {},
                        "id": call_id,
                        "type": "tool_call",
                    }
                ],
            )
            return ChatResult(generations=[ChatGeneration(message=message)])

        result_tool = self._structured_result_tool()
        latest = tool_messages[-1]
        summary = str(latest.content)
        try:
            parsed_tool_result = json.loads(summary)
        except (json.JSONDecodeError, TypeError):
            parsed_tool_result = {"raw": summary}
        args = {
            "status": "SUCCESS",
            "summary": summary[:800],
            "evidence_ids": [f"tool:{latest.name or 'unknown'}"],
            "called_tools": [latest.name or "unknown"],
            "warnings": [],
            "data": {
                "tool_call_id": latest.tool_call_id,
                "profile": self.role_profile,
                "tool_result": parsed_tool_result,
            },
        }
        message = AIMessage(
            content="",
            tool_calls=[
                {
                    "name": result_tool,
                    "args": args,
                    "id": f"result_{self.role_profile}_{len(messages)}",
                    "type": "tool_call",
                }
            ],
        )
        return ChatResult(generations=[ChatGeneration(message=message)])

    def _first_business_tool(self) -> str:
        for name in self._bound_tool_names:
            if name != "AgentResult":
                return name
        raise RuntimeError(f"No business tool bound for offline profile {self.role_profile}")

    def _structured_result_tool(self) -> str:
        if "AgentResult" in self._bound_tool_names:
            return "AgentResult"
        raise RuntimeError("AgentResult structured-output tool was not bound")

    @staticmethod
    def _parse_requested_tool(messages: list[BaseMessage]) -> dict[str, Any]:
        for message in reversed(messages):
            if isinstance(message, HumanMessage):
                content = str(message.content)
                marker = "TOOL_INPUT_JSON="
                if marker in content:
                    raw = content.split(marker, 1)[1].strip()
                    try:
                        parsed = json.loads(raw)
                    except json.JSONDecodeError:
                        return {}
                    return parsed if isinstance(parsed, dict) else {}
        return {}


def get_model(profile: ModelProfile) -> BaseChatModel:
    """Build the offline scripted model or the configured live OpenAI model."""
    settings = get_settings()
    if settings.tradeflow_mode == "offline":
        return ScriptedToolCallingModel(role_profile=profile.value)

    model_ids = {
        ModelProfile.PLANNING: settings.model_planning,
        ModelProfile.SUPERVISOR: settings.model_supervisor,
        ModelProfile.DOCUMENT: settings.model_document,
        ModelProfile.SHIPMENT: settings.model_shipment,
        ModelProfile.FINANCIAL: settings.model_financial,
        ModelProfile.PRODUCT: settings.model_product,
        ModelProfile.REPORT_WRITER: settings.model_report_writer,
        ModelProfile.CRITIC: settings.model_critic,
    }
    effort = (
        settings.reasoning_supervisor
        if profile
        in {
            ModelProfile.PLANNING,
            ModelProfile.SUPERVISOR,
        }
        else settings.reasoning_critic
        if profile is ModelProfile.CRITIC
        else settings.reasoning_specialist
    )
    return ChatOpenAI(
        model=model_ids[profile],
        api_key=settings.require_openai_key(),
        use_responses_api=True,
        reasoning={"effort": effort},
        timeout=60,
        max_retries=2,
    )

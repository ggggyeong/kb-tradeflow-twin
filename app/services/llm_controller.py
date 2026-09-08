from __future__ import annotations

import json
import os
from typing import Any, Literal, Protocol

from pydantic import BaseModel, Field

from app.schemas.orchestration import ModelCall, ToolAudit


class LLMError(RuntimeError):
    """Safe, user-facing error; never includes provider response bodies or secrets."""


class LLMSettings(BaseModel):
    model: Literal["gpt-5-nano"] = "gpt-5-nano"
    service_tier: Literal["flex", "default"] = "flex"
    max_output_tokens: int = Field(default=6000, ge=256, le=6000)
    max_calls: int = Field(default=20, ge=1, le=20)
    timeout_seconds: float = Field(default=90, ge=1, le=180)

    @classmethod
    def from_env(cls) -> LLMSettings:
        from dotenv import load_dotenv

        from app.core.paths import PROJECT_ROOT

        load_dotenv(PROJECT_ROOT / ".env", override=False)
        return cls.model_validate(
            {
                "model": os.getenv("OPENAI_MODEL", "gpt-5-nano"),
                "service_tier": os.getenv("OPENAI_SERVICE_TIER", "flex"),
                "max_output_tokens": os.getenv("OPENAI_MAX_OUTPUT_TOKENS", "6000"),
                "max_calls": os.getenv("OPENAI_MAX_CALLS", "20"),
                "timeout_seconds": os.getenv("OPENAI_TIMEOUT_SECONDS", "90"),
            }
        )


class ToolCallingModel(Protocol):
    def call(
        self,
        *,
        instructions: str,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
        forced_tool: str | None = None,
    ) -> ModelCall: ...


class RunModel:
    """One request's shared call budget, including nested agents. Never shared across runs."""

    def __init__(self, model: ToolCallingModel, limit: int) -> None:
        self.model, self.limit = model, limit
        self.calls = self.input_tokens = self.output_tokens = 0
        self.audit: list[ToolAudit] = []

    def call(self, **kwargs: Any) -> ModelCall:
        if self.calls >= self.limit:
            raise LLMError("요청당 LLM 호출 상한에 도달하여 중단했습니다.")
        self.calls += 1
        result = self.model.call(**kwargs)
        self.input_tokens += result.input_tokens
        self.output_tokens += result.output_tokens
        return result


class OpenAIToolCallingModel:
    """Responses function calls; low-cost tier, no automatic retries or upgrades."""

    def __init__(self, settings: LLMSettings | None = None, *, client: Any = None) -> None:
        self.settings = settings or LLMSettings.from_env()
        self._client = client

    def _get_client(self) -> Any:
        if self._client is None:
            key = os.getenv("OPENAI_API_KEY", "").strip()
            if not key:
                raise LLMError("OPENAI_API_KEY 환경변수에 새 API 키를 설정해 주세요.")
            from openai import OpenAI

            self._client = OpenAI(
                api_key=key,
                base_url="https://api.openai.com/v1",
                max_retries=0,
                timeout=self.settings.timeout_seconds,
            )
        return self._client

    def call(
        self,
        *,
        instructions: str,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
        forced_tool: str | None = None,
    ) -> ModelCall:
        # Deliberately stateless on the provider; previous tool/reasoning items travel explicitly.
        client = self._get_client()
        try:
            response = client.responses.create(
                model=self.settings.model,
                service_tier=self.settings.service_tier,
                instructions=instructions,
                input=messages,
                tools=tools,
                tool_choice={"type": "function", "name": forced_tool}
                if forced_tool
                else "required",
                parallel_tool_calls=False,
                store=False,
                include=["reasoning.encrypted_content"],
                # Extraction and grounded writing need more care than a forced
                # zero-argument tool call. Keep the same nano model and token cap.
                reasoning={
                    "effort": "high"
                    if forced_tool in {"choose_financial_evidence", "explain_financial_evidence"}
                    else "low"
                    if forced_tool == "validate_fields"
                    else "minimal"
                },
                max_output_tokens=self.settings.max_output_tokens,
            )
        except Exception as exc:
            # Do not leak API key, request contents or provider error payloads.
            error_type = type(exc).__name__
            safe_type = (
                error_type
                if error_type
                in {
                    "APITimeoutError",
                    "APIConnectionError",
                    "AuthenticationError",
                    "PermissionDeniedError",
                    "RateLimitError",
                    "BadRequestError",
                    "InternalServerError",
                }
                else "APIError"
            )
            raise LLMError(
                f"LLM 호출 실패 ({safe_type}): 키·모델 접근 권한·Flex 가용성·시간 제한을 확인해 주세요."
            ) from exc
        if response.status != "completed":
            details = getattr(response, "incomplete_details", None)
            if getattr(details, "reason", None) == "max_output_tokens":
                raise LLMError("LLM 출력 토큰 상한으로 응답이 완료되지 않았습니다.")
            raise LLMError(
                "LLM 응답이 완료되지 않았습니다. 출력 제한 또는 응답 상태를 확인해 주세요."
            )
        calls = [item for item in response.output if item.type == "function_call"]
        if len(calls) != 1:
            raise LLMError("LLM은 한 번에 하나의 도구를 호출해야 합니다.")
        item = calls[0]
        try:
            arguments = json.loads(item.arguments)
            if not isinstance(arguments, dict):
                raise ValueError("not object")
        except (ValueError, TypeError) as exc:
            raise LLMError("LLM 도구 인자가 올바른 JSON 객체가 아닙니다.") from exc
        usage = response.usage
        return ModelCall(
            name=item.name,
            arguments=arguments,
            call_id=item.call_id,
            response_items=[
                part.model_dump(mode="json", exclude_none=True) for part in response.output
            ],
            input_tokens=usage.input_tokens if usage else 0,
            output_tokens=usage.output_tokens if usage else 0,
            response_id=response.id,
            served_model=response.model,
            served_service_tier=response.service_tier,
        )

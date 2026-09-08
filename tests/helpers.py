from __future__ import annotations

import copy
import json
from typing import Any

from app.schemas.orchestration import ModelCall

FULL_PLAN = {
    "status": "READY",
    "steps": [
        "call_document_agent",
        "call_finance_agent",
        "generate_report",
    ],
    "reason": "문서 분석과 금융일정 비교 후 상품 근거를 찾아 보고서를 작성합니다.",
}


class ScriptedModel:
    """Offline fake: never contacts OpenAI, even when the environment has a key."""

    def __init__(self, actions: list[Any]) -> None:
        self.actions = list(actions)
        self.requests: list[dict[str, Any]] = []

    def call(self, **kwargs: Any) -> ModelCall:
        self.requests.append(copy.deepcopy(kwargs))
        if not self.actions:
            raise AssertionError("Unexpected extra LLM call")
        action = self.actions.pop(0)
        if isinstance(action, Exception):
            raise action
        if isinstance(action, dict):
            name, arguments = "submit_plan", action
        elif isinstance(action, tuple):
            name, arguments = action
        else:
            name, arguments = action, {}
        call_id = f"call_{len(self.requests)}"
        return ModelCall(
            name=name,
            arguments=arguments,
            call_id=call_id,
            response_items=[
                {
                    "type": "reasoning",
                    "id": f"r_{call_id}",
                    "summary": [],
                    "encrypted_content": "test-reasoning",
                },
                {
                    "type": "function_call",
                    "name": name,
                    "arguments": json.dumps(arguments),
                    "call_id": call_id,
                },
            ],
            input_tokens=20,
            output_tokens=10,
        )

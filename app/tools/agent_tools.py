"""Small tool-calling helpers shared by the three agents; no hidden retries."""

from __future__ import annotations

import json
from typing import Any

from pydantic import BaseModel

from app.prompts import load_prompt
from app.schemas.orchestration import ModelCall, ToolAudit
from app.services.llm_controller import LLMError, RunModel


def function_tool(
    name: str, description: str, schema: type[BaseModel] | None = None
) -> dict[str, Any]:
    parameters = (
        schema.model_json_schema()
        if schema
        else {
            "type": "object",
            "properties": {},
            "required": [],
            "additionalProperties": False,
        }
    )
    return {
        "type": "function",
        "name": name,
        "description": description,
        "strict": True,
        "parameters": parameters,
    }


def message(data: Any) -> dict[str, Any]:
    return {"role": "user", "content": json.dumps(data, ensure_ascii=False, default=str)}


def invoke(
    model: RunModel, prompt: str, messages: list[dict[str, Any]], tool: dict[str, Any]
) -> ModelCall:
    call = model.call(
        instructions=load_prompt(prompt), messages=messages, tools=[tool], forced_tool=tool["name"]
    )
    if call.name != tool["name"] or (not tool["parameters"].get("properties") and call.arguments):
        model.audit.append(
            ToolAudit(
                tool=call.name,
                status="REJECTED",
                call_id=call.call_id,
                message="허용되지 않은 도구 또는 인자를 거부했습니다.",
            )
        )
        raise LLMError("Agent가 허용된 도구 호출 규격을 따르지 않았습니다.")
    messages.extend(call.response_items)
    return call


def observe(model: RunModel, messages: list[dict[str, Any]], call: ModelCall, data: Any) -> None:
    messages.append(
        {
            "type": "function_call_output",
            "call_id": call.call_id,
            "output": json.dumps(data, ensure_ascii=False, default=str),
        }
    )
    model.audit.append(
        ToolAudit(tool=call.name, status="SUCCESS", call_id=call.call_id, message="도구 실행 완료")
    )

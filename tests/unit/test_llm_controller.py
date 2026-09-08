from __future__ import annotations

import json
from typing import Any

import httpx2
import pytest
from openai import OpenAI

from app.services.llm_controller import LLMError, LLMSettings, OpenAIToolCallingModel
from app.tools.portfolio_tools import tool_definition


def response_body(**changes: Any) -> dict[str, Any]:
    return {
        "id": "resp_test",
        "object": "response",
        "created_at": 0,
        "status": "completed",
        "model": "gpt-5-nano",
        "service_tier": "flex",
        "output": [
            {
                "type": "reasoning",
                "id": "rs_test",
                "summary": [],
                "encrypted_content": "encrypted-test",
            },
            {
                "type": "function_call",
                "id": "fc_test",
                "call_id": "call_test",
                "name": "call_document_agent",
                "arguments": "{}",
            },
        ],
        "usage": {
            "input_tokens": 25,
            "output_tokens": 12,
            "total_tokens": 37,
            "input_tokens_details": {"cached_tokens": 0},
            "output_tokens_details": {"reasoning_tokens": 2},
        },
        **changes,
    }


def call(model: OpenAIToolCallingModel) -> Any:
    return model.call(
        instructions="test",
        messages=[{"role": "user", "content": "test"}],
        tools=[tool_definition("call_document_agent")],
    )


def test_real_sdk_serializes_low_cost_function_call_without_network() -> None:
    captured = []

    def handler(request: httpx2.Request) -> httpx2.Response:
        captured.append(json.loads(request.content))
        return httpx2.Response(200, json=response_body())

    with OpenAI(
        api_key="test-placeholder",
        max_retries=0,
        http_client=httpx2.Client(transport=httpx2.MockTransport(handler)),
    ) as client:
        result = call(OpenAIToolCallingModel(LLMSettings(), client=client))
    payload = captured[0]
    assert payload["model"] == "gpt-5-nano"
    assert payload["service_tier"] == "flex"
    assert payload["parallel_tool_calls"] is False
    assert payload["store"] is False
    assert payload["max_output_tokens"] == 6000
    assert payload["reasoning"] == {"effort": "minimal"}
    assert payload["tool_choice"] == "required"
    assert payload["tools"][0]["strict"] is True
    assert result.call_id == "call_test"
    assert result.input_tokens == 25
    assert result.output_tokens == 12
    assert result.response_id == "resp_test"
    assert result.served_model == "gpt-5-nano"
    assert result.served_service_tier == "flex"
    assert result.response_items[0]["encrypted_content"] == "encrypted-test"


@pytest.mark.parametrize(
    "changes",
    [
        {"status": "incomplete"},
        {"output": []},
        {
            "output": [
                {
                    "type": "function_call",
                    "name": "call_document_agent",
                    "call_id": "c",
                    "arguments": "[]",
                }
            ]
        },
        {
            "output": [
                {
                    "type": "function_call",
                    "name": "call_document_agent",
                    "call_id": "c",
                    "arguments": "not json",
                }
            ]
        },
        {
            "output": [
                {
                    "type": "function_call",
                    "name": "call_document_agent",
                    "call_id": "c1",
                    "arguments": "{}",
                },
                {
                    "type": "function_call",
                    "name": "call_document_agent",
                    "call_id": "c2",
                    "arguments": "{}",
                },
            ]
        },
    ],
)
def test_incomplete_or_invalid_response_fails_closed(changes: dict[str, Any]) -> None:
    transport = httpx2.MockTransport(lambda _: httpx2.Response(200, json=response_body(**changes)))
    with (
        OpenAI(
            api_key="test-placeholder",
            max_retries=0,
            http_client=httpx2.Client(transport=transport),
        ) as client,
        pytest.raises(LLMError),
    ):
        call(OpenAIToolCallingModel(LLMSettings(), client=client))


def test_unavailable_flex_has_no_retry_or_standard_tier_fallback() -> None:
    requests = []

    def handler(request: httpx2.Request) -> httpx2.Response:
        requests.append(request)
        return httpx2.Response(
            429,
            json={
                "error": {"message": "sensitive provider details", "type": "resource_unavailable"}
            },
        )

    with (
        OpenAI(
            api_key="test-placeholder",
            max_retries=0,
            http_client=httpx2.Client(transport=httpx2.MockTransport(handler)),
        ) as client,
        pytest.raises(LLMError) as exc,
    ):
        call(OpenAIToolCallingModel(LLMSettings(), client=client))
    assert len(requests) == 1
    assert "sensitive" not in str(exc.value)
    assert "RateLimitError" in str(exc.value)


def test_missing_key_fails_before_constructing_client(monkeypatch: Any) -> None:
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    with pytest.raises(LLMError, match="OPENAI_API_KEY"):
        call(OpenAIToolCallingModel(LLMSettings()))


def test_production_client_has_no_hidden_retries(monkeypatch: Any) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "test-placeholder")
    client = OpenAIToolCallingModel(LLMSettings())._get_client()
    try:
        assert client.max_retries == 0
        assert client.timeout == 90
        assert str(client.base_url) == "https://api.openai.com/v1/"
    finally:
        client.close()


@pytest.mark.parametrize(
    ("tool_name", "effort"),
    [
        ("validate_fields", "low"),
        ("choose_financial_evidence", "high"),
        ("explain_financial_evidence", "high"),
    ],
)
def test_interpretation_uses_more_reasoning_without_changing_model_or_tier(
    tool_name: str, effort: str
) -> None:
    captured = []

    def handler(request: httpx2.Request) -> httpx2.Response:
        captured.append(json.loads(request.content))
        return httpx2.Response(200, json=response_body())

    with OpenAI(
        api_key="test-placeholder",
        max_retries=0,
        http_client=httpx2.Client(transport=httpx2.MockTransport(handler)),
    ) as client:
        OpenAIToolCallingModel(LLMSettings(), client=client).call(
            instructions="test",
            messages=[{"role": "user", "content": "test"}],
            tools=[tool_definition("call_document_agent")],
            forced_tool=tool_name,
        )
    assert captured[0]["reasoning"] == {"effort": effort}
    assert captured[0]["model"] == "gpt-5-nano"
    assert captured[0]["service_tier"] == "flex"
    assert captured[0]["max_output_tokens"] == 6000


def test_live_example_log_excludes_response_items_and_reasoning(tmp_path: Any) -> None:
    from scripts.run_example import RecordedLiveModel
    from tests.helpers import ScriptedModel

    path = tmp_path / "calls.json"
    recorded = RecordedLiveModel(LLMSettings(), path)
    recorded.delegate = ScriptedModel(["read_pdf"])
    recorded.call(instructions="test", messages=[], tools=[])
    content = path.read_text()
    assert json.loads(content)[0]["name"] == "read_pdf"
    assert "response_items" not in content
    assert "encrypted-test" not in content
    assert "test-reasoning" not in content


def test_live_example_logs_failed_attempt_without_provider_details(tmp_path: Any) -> None:
    from scripts.run_example import RecordedLiveModel

    class FailingModel:
        def call(self, **kwargs: Any) -> Any:
            raise LLMError("LLM 출력 토큰 상한으로 응답이 완료되지 않았습니다.")

    path = tmp_path / "calls.json"
    recorded = RecordedLiveModel(LLMSettings(), path)
    recorded.delegate = FailingModel()
    with pytest.raises(LLMError):
        recorded.call(instructions="private-context", messages=[], tools=[], forced_tool="test")
    assert recorded.attempts == 1
    assert recorded.records == []
    assert not path.exists()
    failures = json.loads((tmp_path / "calls-errors.json").read_text())
    assert failures[0]["tool"] == "test"
    assert failures[0]["attempt"] == 1
    assert "private-context" not in json.dumps(failures)


def test_output_limit_has_safe_specific_error() -> None:
    transport = httpx2.MockTransport(
        lambda _: httpx2.Response(
            200,
            json=response_body(
                status="incomplete", incomplete_details={"reason": "max_output_tokens"}
            ),
        )
    )
    with (
        OpenAI(
            api_key="test-placeholder",
            max_retries=0,
            http_client=httpx2.Client(transport=transport),
        ) as client,
        pytest.raises(LLMError, match="출력 토큰 상한"),
    ):
        call(OpenAIToolCallingModel(LLMSettings(), client=client))


@pytest.mark.parametrize(
    "settings",
    [
        {"model": "gpt-6-astra"},
        {"max_calls": 21},
        {"max_output_tokens": 6001},
        {"service_tier": "priority"},
    ],
)
def test_more_expensive_or_unbounded_configuration_is_rejected(settings: dict[str, Any]) -> None:
    with pytest.raises(ValueError):
        LLMSettings.model_validate(settings)


def test_agent_schemas_are_strict_function_call_compatible() -> None:
    from app.agents.document_agent import DocumentSubmission
    from app.agents.finance_advisor import EvidenceExplanation, selection_tool
    from app.schemas.orchestration import ExecutionPlan
    from app.services.service_policy import get_answer_slots
    from app.tools.agent_tools import function_tool
    from tests.unit.test_finance_advisor import evidence_hit

    def check(value: Any) -> None:
        if isinstance(value, dict):
            if value.get("type") == "object":
                assert value.get("additionalProperties") is False
                assert set(value.get("required", [])) == set(value.get("properties", {}))
            for nested in value.values():
                check(nested)
        elif isinstance(value, list):
            for nested in value:
                check(nested)

    for schema in (DocumentSubmission, ExecutionPlan, EvidenceExplanation):
        check(function_tool("test", "test", schema)["parameters"])
    slots = get_answer_slots("SUPPLIER_PAYMENT", export_receivable_confirmed=False)
    # Finance uses a runtime schema with actual question keys and evidence enums.
    check(selection_tool([evidence_hit()], slots)["parameters"])
    check(selection_tool([], slots)["parameters"])

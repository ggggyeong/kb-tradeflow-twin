from __future__ import annotations

import json
from typing import Any, cast

import pytest
from langchain.agents.middleware import ModelResponse
from langchain_core.messages import AIMessage, ToolMessage

from app.agents import (
    critic,
    document_intelligence,
    financial_calendar,
    financial_exposure,
    product_advisor,
    report_writer,
    shipment_timeline,
    trade_case_manager,
)
from app.agents.common import RequestedToolContract, validate_native_tool_chain
from app.agents.planning import TEAM_AND_TOOL_CATALOG
from app.agents.supervisor import TOOL_OWNERS
from app.core.model_factory import ScriptedToolCallingModel
from app.services.reports import ReportService
from app.tools import architecture as architecture_tools
from app.tools import reports as report_tools
from app.tools.financial_calendar import (
    import_financial_calendar,
    select_financial_monitoring_candidates,
    update_financial_event_link_details,
    validate_financial_calendar,
)

AGENT_MODULES = [
    document_intelligence,
    trade_case_manager,
    financial_calendar,
    shipment_timeline,
    financial_exposure,
    product_advisor,
    report_writer,
    critic,
]


def test_all_eight_agents_are_native_compiled_agents_with_prompt_templates() -> None:
    for module in AGENT_MODULES:
        assert "\n" in module.SYSTEM_PROMPT
        agent = module.build_agent()
        assert "CompiledStateGraph" in type(agent).__name__


def test_removed_agent_tools_are_not_public_or_registered() -> None:
    removed = {
        "generate_daily_monitoring_report",
        "validate_result_evidence",
        "create_report_handoff",
    }

    assert not hasattr(report_tools, "generate_daily_monitoring_report")
    assert not hasattr(report_tools, "create_report_handoff")
    assert not hasattr(architecture_tools, "validate_result_evidence")
    assert removed.isdisjoint(TOOL_OWNERS)
    assert all(name not in TEAM_AND_TOOL_CATALOG for name in removed)
    assert hasattr(ReportService, "generate_daily_monitoring_report")


def test_document_intelligence_exposes_only_the_runtime_stage_tool() -> None:
    assert "inspect_field_contract" not in TOOL_OWNERS
    assert TOOL_OWNERS["stage_document_intelligence"] == "document_intelligence"
    document_catalog = TEAM_AND_TOOL_CATALOG.split("- trade_case_manager", 1)[0]
    assert "inspect_field_contract" not in document_catalog
    assert "stage_document_intelligence" in document_catalog


def test_financial_calendar_owns_explicit_optional_link_detail_updates() -> None:
    assert update_financial_event_link_details.name == "update_financial_event_link_details"
    assert TOOL_OWNERS[update_financial_event_link_details.name] == "financial_calendar"
    assert "update_financial_event_link_details" in TEAM_AND_TOOL_CATALOG
    assert "explicit Human-reviewed" in financial_calendar.SYSTEM_PROMPT


def test_financial_calendar_registers_every_required_tool_without_optional_imports(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, Any] = {}

    def fake_create_agent(**kwargs: Any) -> object:
        captured.update(kwargs)
        return object()

    monkeypatch.setattr(financial_calendar, "langchain_create_agent", fake_create_agent)
    financial_calendar.build_agent(model=cast(Any, object()))

    assert [tool.name for tool in captured["tools"]] == [
        validate_financial_calendar.name,
        import_financial_calendar.name,
        select_financial_monitoring_candidates.name,
        update_financial_event_link_details.name,
    ]


def test_native_ai_tool_message_chain_preserves_call_id() -> None:
    agent = critic.build_agent(ScriptedToolCallingModel(role_profile="critic"))
    result = agent.invoke(
        {
            "messages": [
                {
                    "role": "user",
                    "content": (
                        'TOOL_INPUT_JSON={"tool_name":"review_workflow_evidence",'
                        '"args":{"workflow_kind":"TRADE_CASE",'
                        '"expected_task_ids":["source_result"],'
                        '"evidence_snapshot":{"source_result":{"status":"READY"}}}}'
                    ),
                }
            ]
        }
    )
    messages = result["messages"]
    ai_calls = [
        call
        for message in messages
        if isinstance(message, AIMessage)
        for call in message.tool_calls
    ]
    tool_messages = [message for message in messages if isinstance(message, ToolMessage)]
    assert ai_calls
    assert tool_messages
    assert len(ai_calls) == 1
    assert len(tool_messages) == 1
    business_call = next(call for call in ai_calls if call["name"] == "review_workflow_evidence")
    business_result = next(
        message for message in tool_messages if message.name == "review_workflow_evidence"
    )
    assert business_call["id"] == business_result.tool_call_id
    evidence = validate_native_tool_chain(messages)
    assert evidence == [
        {
            "tool_call_id": business_call["id"],
            "tool_name": "review_workflow_evidence",
            "status": "success",
        }
    ]
    assert result["structured_response"].status == "SUCCESS"
    assert result["structured_response"].data["result_sealed_by"] == "requested_tool_contract"


def test_requested_tool_args_are_pinned_without_replacing_native_call() -> None:
    controller_args = {
        "workflow_kind": "PRODUCT_ADVISORY_REPORT",
        "evidence_snapshot": {
            "retrieve_product_evidence": {
                "options": [
                    {
                        "product_name": "KB 수출팩토링",
                        "citations": [{"document": "상품.pdf", "page": 3}],
                    }
                ]
            }
        },
        "expected_task_ids": ["retrieve_product_evidence"],
    }
    request_messages = [
        {
            "role": "user",
            "content": (
                "Review the evidence.\nTOOL_INPUT_JSON="
                '{"tool_name":"review_workflow_evidence","args":'
                + json.dumps(controller_args, ensure_ascii=False)
                + "}"
            ),
        }
    ]
    native_call = AIMessage(
        content="provider emitted a native call",
        id="provider-message-id",
        additional_kwargs={"provider_raw": "preserved"},
        response_metadata={"model_name": "live-model"},
        tool_calls=[
            {
                "name": "review_workflow_evidence",
                "args": {
                    "workflow_kind": "PRODUCT_ADVISORY_REPORT",
                    "evidence_snapshot": {},
                    "expected_task_ids": [],
                },
                "id": "call_live_critic_123",
                "type": "tool_call",
            }
        ],
    )
    structured = {"source": "provider-response"}
    response = ModelResponse(result=[native_call], structured_response=structured)

    pinned = RequestedToolContract._pin_requested_tool_args(response, request_messages)

    assert pinned is not response
    assert pinned.structured_response is structured
    assert len(pinned.result) == 1
    pinned_message = pinned.result[0]
    assert isinstance(pinned_message, AIMessage)
    assert pinned_message.content == native_call.content
    assert pinned_message.id == native_call.id
    assert pinned_message.additional_kwargs == native_call.additional_kwargs
    assert pinned_message.response_metadata == native_call.response_metadata
    assert pinned_message.tool_calls == [
        {
            "name": "review_workflow_evidence",
            "args": controller_args,
            "id": "call_live_critic_123",
            "type": "tool_call",
        }
    ]
    # Do not mutate the provider response object that is retained in model traces.
    assert native_call.tool_calls[0]["args"] == {
        "workflow_kind": "PRODUCT_ADVISORY_REPORT",
        "evidence_snapshot": {},
        "expected_task_ids": [],
    }

    tool_message = ToolMessage(
        content='{"verdict":"PASS"}',
        name="review_workflow_evidence",
        tool_call_id="call_live_critic_123",
    )
    assert validate_native_tool_chain([pinned_message, tool_message]) == [
        {
            "tool_call_id": "call_live_critic_123",
            "tool_name": "review_workflow_evidence",
            "status": "success",
        }
    ]


def test_requested_tool_args_are_not_synthesized_without_matching_native_call() -> None:
    messages = [
        {
            "role": "user",
            "content": (
                'TOOL_INPUT_JSON={"tool_name":"review_workflow_evidence",'
                '"args":{"expected_tools":["retrieve_product_evidence"]}}'
            ),
        }
    ]
    unrelated = AIMessage(
        content="",
        tool_calls=[
            {
                "name": "another_tool",
                "args": {"kept": True},
                "id": "provider-call-id",
                "type": "tool_call",
            }
        ],
    )
    response = ModelResponse(result=[unrelated])

    pinned = RequestedToolContract._pin_requested_tool_args(response, messages)

    assert pinned is response
    assert unrelated.tool_calls[0]["args"] == {"kept": True}

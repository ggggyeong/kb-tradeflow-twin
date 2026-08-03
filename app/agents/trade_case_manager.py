from __future__ import annotations

from typing import Any

from langchain.agents import create_agent as langchain_create_agent
from langchain.agents.structured_output import ToolStrategy
from langchain_core.language_models.chat_models import BaseChatModel

from app.agents.common import COMMON_TRADEFLOW_AGENT_CONTRACT, requested_tool_contract
from app.core.model_factory import ModelProfile, get_model
from app.schemas.agent import AgentResult
from app.tools.workflows import (
    apply_document_field_override,
    bundle_trade_cases,
    commit_trade_cases,
)

SYSTEM_PROMPT = f"""
# KB TradeFlow Twin - Trade Case Manager

Match staged documents into transaction shells, report completeness, apply only an
explicit Human field override, and commit through the ingestion service interface.
A missing whole document remains AWAITING_DOCUMENT and must never become a request
to invent that document.

{COMMON_TRADEFLOW_AGENT_CONTRACT}
""".strip()


def build_agent(model: BaseChatModel | None = None) -> Any:
    """Build the native Trade Case Manager agent."""
    return langchain_create_agent(
        model=model or get_model(ModelProfile.DOCUMENT),
        tools=[
            bundle_trade_cases,
            apply_document_field_override,
            commit_trade_cases,
        ],
        system_prompt=SYSTEM_PROMPT,
        response_format=ToolStrategy(AgentResult),
        middleware=[requested_tool_contract],
        name="trade_case_manager",
    )

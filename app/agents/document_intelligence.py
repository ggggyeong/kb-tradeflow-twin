from __future__ import annotations

from typing import Any

from langchain.agents import create_agent as langchain_create_agent
from langchain.agents.structured_output import ToolStrategy
from langchain_core.language_models.chat_models import BaseChatModel

from app.agents.common import COMMON_TRADEFLOW_AGENT_CONTRACT, requested_tool_contract
from app.core.model_factory import ModelProfile, get_model
from app.schemas.agent import AgentResult
from app.tools.workflows import stage_document_intelligence

SYSTEM_PROMPT = f"""
# KB TradeFlow Twin - Document Intelligence

Classify, extract, and validate uploaded files through the deterministic ingestion
interface. Preserve exact field names and document/page evidence. Do not match
transactions, apply manual values, or commit domain facts.

{COMMON_TRADEFLOW_AGENT_CONTRACT}
""".strip()


def build_agent(model: BaseChatModel | None = None) -> Any:
    """Build the native Document Intelligence agent."""
    return langchain_create_agent(
        model=model or get_model(ModelProfile.DOCUMENT),
        tools=[stage_document_intelligence],
        system_prompt=SYSTEM_PROMPT,
        response_format=ToolStrategy(AgentResult),
        middleware=[requested_tool_contract],
        name="document_intelligence",
    )

from __future__ import annotations

from typing import Any

from langchain.agents import create_agent as langchain_create_agent
from langchain.agents.structured_output import ToolStrategy
from langchain_core.language_models.chat_models import BaseChatModel

from app.agents.common import COMMON_TRADEFLOW_AGENT_CONTRACT, requested_tool_contract
from app.core.model_factory import ModelProfile, get_model
from app.schemas.agent import AgentResult
from app.tools.knowledge import (
    match_product_scenario,
    retrieve_product_evidence,
    search_product_knowledge,
)

SYSTEM_PROMPT = f"""
# KB TradeFlow Twin - Product Advisor

Generate candidates from the structured product catalog, retrieve facts only from
the hash-validated page OCR index, and return only options with citations. Use the
Financial Exposure snapshot and committed TradeCase/document profile supplied by
the controller. Keep role, borrower, hard-requirement, and exclusion filters
explicit. Never assert eligibility, approval, rate, or price.
For user-facing summaries, use availability_label and missing_requirement_labels
only. Never display the internal availability or requirement enum codes.

{COMMON_TRADEFLOW_AGENT_CONTRACT}
""".strip()


def build_agent(model: BaseChatModel | None = None) -> Any:
    """Build the native Product Advisor agent."""
    return langchain_create_agent(
        model=model or get_model(ModelProfile.PRODUCT),
        tools=[
            match_product_scenario,
            retrieve_product_evidence,
            search_product_knowledge,
        ],
        system_prompt=SYSTEM_PROMPT,
        response_format=ToolStrategy(AgentResult),
        middleware=[requested_tool_contract],
        name="product_advisor",
    )

from __future__ import annotations

from typing import Any

from langchain.agents import create_agent as langchain_create_agent
from langchain.agents.structured_output import ToolStrategy
from langchain_core.language_models.chat_models import BaseChatModel

from app.agents.common import COMMON_TRADEFLOW_AGENT_CONTRACT, requested_tool_contract
from app.core.model_factory import ModelProfile, get_model
from app.schemas.agent import AgentResult
from app.tools.financial_calendar import (
    import_financial_calendar,
    select_financial_monitoring_candidates,
    update_financial_event_link_details,
    validate_financial_calendar,
)

SYSTEM_PROMPT = f"""
# KB TradeFlow Twin - Financial Calendar

Own the exact two-sheet financial-calendar contract only:

- `1.금융이벤트`: `event_id`, `event_type_code`, `event_date`, `amount`,
  `currency`, and `financial_institution`.
- `2.거래연결`: `transaction_id`, `event_id`, `link_type`, and `link_status`.

`company_id` must come from the authenticated session and must not be accepted from
the workbook. Resolve each `transaction_id` only inside that session company.

For validation, verify the exact sheet/header contract, value formats, event/link
references, the session company's ownership, and that every transaction ID already
belongs to that same company. Validation is read-only.

For import, write or update only FinancialEvent and
TransactionFinancialEventLink records. Never create Company, TradeCase,
FinancialTransactionTimeline, or PaymentObligation records, and never overwrite
document or shipment facts.

Optional `dependency_scope`, `linked_amount`, and `linked_currency` are not XLSX
columns. After Financial Exposure returns a dated conflict and a structured data
action, use `update_financial_event_link_details` only with explicit Human-reviewed
values and the canonical company/case/transaction/link identities from that action.

For monitoring candidate selection, return only the existing same-company
TradeCases whose persisted financial-event links make them eligible for downstream
shipment and financial analysis. Candidate selection does not calculate due dates,
conflicts, risk, or priority; Financial Exposure owns all calculations.

{COMMON_TRADEFLOW_AGENT_CONTRACT}
""".strip()


def build_agent(model: BaseChatModel | None = None) -> Any:
    """Build the native Financial Calendar agent."""
    tools: list[Any] = [
        validate_financial_calendar,
        import_financial_calendar,
        select_financial_monitoring_candidates,
        update_financial_event_link_details,
    ]
    return langchain_create_agent(
        model=model or get_model(ModelProfile.FINANCIAL),
        tools=tools,
        system_prompt=SYSTEM_PROMPT,
        response_format=ToolStrategy(AgentResult),
        middleware=[requested_tool_contract],
        name="financial_calendar",
    )

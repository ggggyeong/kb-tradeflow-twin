from __future__ import annotations

import operator
from datetime import date
from typing import Annotated, Any, TypedDict

from app.schemas.orchestration import ExecutionPlan, ModelCall, ToolAudit
from app.schemas.portfolio import (
    PortfolioConflictResult,
    PortfolioDocumentResult,
    PortfolioProductOption,
    PortfolioServiceCard,
    ReceiptResolution,
)


class PortfolioState(TypedDict, total=False):
    """Serializable per-request data; model clients are never placed in graph state."""

    request: dict[str, Any]
    expected_receipt_date: date | None
    receipt_resolution: ReceiptResolution
    documents: list[PortfolioDocumentResult]
    conflicts: list[PortfolioConflictResult]
    product_options: list[PortfolioProductOption]
    service_cards: list[PortfolioServiceCard]
    report_path: str
    status: str
    warnings: list[str]
    plan: ExecutionPlan | None
    pending_call: ModelCall | None
    messages: list[dict[str, Any]]
    completed: dict[str, str]
    tool_audit: Annotated[list[ToolAudit], operator.add]
    llm_calls: int
    input_tokens: int
    output_tokens: int
    fatal_error: str | None
    next_node: str
    trace: Annotated[list[str], operator.add]

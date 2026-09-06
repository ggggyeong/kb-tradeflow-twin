from __future__ import annotations

import operator
from datetime import date
from typing import Annotated, Any, TypedDict

from app.schemas.portfolio import (
    PortfolioConflictResult,
    PortfolioDocumentResult,
    PortfolioProductOption,
)


class PortfolioState(TypedDict, total=False):
    """Small state passed through the four visible portfolio steps."""

    request: dict[str, Any]
    expected_receipt_date: date | None
    documents: list[PortfolioDocumentResult]
    conflicts: list[PortfolioConflictResult]
    product_options: list[PortfolioProductOption]
    report_path: str
    status: str
    warnings: list[str]
    trace: Annotated[list[str], operator.add]

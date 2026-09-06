from __future__ import annotations

from datetime import date
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator

FinancialScenarioCode = Literal[
    "WORKING_CAPITAL_LOAN_MATURITY",
    "FX_FORWARD_MATURITY",
    "SUPPLIER_PAYMENT",
]
ConflictStatus = Literal["CONFLICT", "REVIEW_REQUIRED", "NO_CONFLICT"]


class PortfolioFinancialEvent(BaseModel):
    """One financial schedule entry compared with the expected receipt date."""

    event_id: str
    scenario_code: FinancialScenarioCode
    event_name: str
    event_date: date
    amount: float | None = None
    currency: str | None = None
    link_status: Literal["CONFIRMED", "UNCONFIRMED", "NOT_LINKED"] = "CONFIRMED"

    @field_validator("currency")
    @classmethod
    def normalize_currency(cls, value: str | None) -> str | None:
        return value.strip().upper() if value else None


class PortfolioRunRequest(BaseModel):
    """Minimal, explainable input contract for the three-agent workflow."""

    document_paths: list[Path] = Field(min_length=1, max_length=10)
    financial_events: list[PortfolioFinancialEvent] = Field(min_length=1, max_length=20)
    expected_receipt_date: date | None = None
    report_output_path: Path | None = None


class PortfolioDocumentResult(BaseModel):
    """Document Agent output retained by the final report."""

    file_name: str
    document_type: str
    source_sha256: str
    status: Literal["ANALYZED", "REVIEW_REQUIRED", "UNSUPPORTED"] = "ANALYZED"
    ocr_backend: str = "unknown"
    fields: dict[str, Any] = Field(default_factory=dict)
    evidence: list[dict[str, Any]] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)


class PortfolioConflictResult(BaseModel):
    """Public Financial Conflict Agent result with only three statuses."""

    event_id: str
    scenario_code: FinancialScenarioCode
    status: ConflictStatus
    event_name: str
    event_date: date
    expected_receipt_date: date | None = None
    gap_days: int | None = None
    amount: float | None = None
    currency: str | None = None
    reason: str


class PortfolioCitation(BaseModel):
    """One traceable product-PDF citation."""

    source_file: str
    page: int = Field(ge=1)
    source_sha256: str
    excerpt: str
    chunk_id: str | None = None


class PortfolioProductOption(BaseModel):
    """A review option, never a statement of approval or eligibility."""

    product_id: str
    product_name: str
    scenario_code: FinancialScenarioCode
    why_consider: str
    citations: list[PortfolioCitation] = Field(min_length=1)


class PortfolioReportPayload(BaseModel):
    """Frozen input consumed by the deterministic Report Generator."""

    generated_on: date
    expected_receipt_date: date | None = None
    documents: list[PortfolioDocumentResult]
    conflicts: list[PortfolioConflictResult]
    product_options: list[PortfolioProductOption]
    source_notice: str = (
        "금융상품은 검토 가능한 정보이며 실제 이용 가능 여부, 한도, 금리와 승인은 "
        "최신 약관 및 금융기관 심사를 통해 확인해야 합니다."
    )


class PortfolioRunResult(BaseModel):
    """End-to-end result returned by the LangGraph portfolio workflow."""

    status: Literal["SUCCESS", "REVIEW_REQUIRED"]
    expected_receipt_date: date | None = None
    documents: list[PortfolioDocumentResult]
    conflicts: list[PortfolioConflictResult]
    product_options: list[PortfolioProductOption]
    report_path: str
    warnings: list[str] = Field(default_factory=list)
    trace: list[str] = Field(
        default_factory=lambda: [
            "document_agent",
            "financial_conflict_agent",
            "product_advisor_agent",
            "report_generator",
        ]
    )

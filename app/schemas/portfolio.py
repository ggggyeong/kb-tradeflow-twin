from __future__ import annotations

from datetime import date
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.schemas.orchestration import ExecutionPlan, ToolAudit

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
    model_config = ConfigDict(extra="forbid")
    event_date: date | None = None
    amount: float | None = Field(default=None, ge=0, allow_inf_nan=False)
    currency: str | None = None
    link_status: Literal["CONFIRMED", "UNCONFIRMED", "NOT_LINKED"] = "UNCONFIRMED"
    payment_purpose: Literal["IMPORT", "DOMESTIC", "UNKNOWN"] = "UNKNOWN"
    fx_direction: Literal["SELL", "BUY", "UNKNOWN"] = "UNKNOWN"
    financial_institution: str | None = None

    @field_validator("currency")
    @classmethod
    def normalize_currency(cls, value: str | None) -> str | None:
        return value.strip().upper() if value else None


class PortfolioRunRequest(BaseModel):
    """User intent plus trusted inputs; the LLM cannot rewrite these values."""

    model_config = ConfigDict(extra="forbid")
    trade_direction: Literal["EXPORT", "IMPORT", "UNKNOWN"] = "UNKNOWN"
    receipt_currency: str | None = None
    export_receivable_confirmed: bool = False
    user_request: str = Field(
        default="무역서류와 금융일정을 전체 분석하고 관련 상품 근거와 PDF 보고서를 만들어 주세요.",
        min_length=1,
        max_length=1200,
    )
    document_paths: list[Path] = Field(default_factory=list, max_length=3)
    transaction_id: str | None = Field(default=None, min_length=1, max_length=100)
    financial_calendar_path: Path | None = None
    financial_events: list[PortfolioFinancialEvent] = Field(default_factory=list, max_length=3)
    expected_receipt_date: date | None = None
    report_output_path: Path | None = None

    @field_validator("receipt_currency")
    @classmethod
    def normalize_currency(cls, value: str | None) -> str | None:
        return value.strip().upper() if value else None

    @model_validator(mode="after")
    def validate_single_trade(self) -> PortfolioRunRequest:
        if self.financial_calendar_path:
            if not self.transaction_id:
                raise ValueError("엑셀 입력에는 transaction_id가 필요합니다.")
            if self.financial_events:
                raise ValueError("엑셀과 직접 입력 금융일정을 동시에 사용할 수 없습니다.")
        ids = [event.event_id for event in self.financial_events]
        if len(ids) != len(set(ids)):
            raise ValueError("금융일정 ID는 중복될 수 없습니다.")
        if self.export_receivable_confirmed and self.trade_direction != "EXPORT":
            raise ValueError("수출채권 확인은 수출거래에서만 가능합니다.")
        return self


class ReceiptResolution(BaseModel):
    status: Literal["USER_PROVIDED", "CALCULATED", "REVIEW_REQUIRED"]
    expected_receipt_date: date | None = None
    basis: str
    document_links_verified: bool = False
    evidence: list[dict[str, Any]] = Field(default_factory=list)


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
    event_date: date | None = None
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
    topic: str | None = None
    section_id: str | None = None
    citation_id: str | None = None
    source_url: str | None = None


class PortfolioExplanationPoint(BaseModel):
    """A generated explanation with traceable supporting text, not an approval."""

    text: str = Field(min_length=10, max_length=220)
    supporting_quote: str = Field(min_length=12, max_length=1200)
    citations: list[PortfolioCitation] = Field(min_length=1, max_length=1)
    slot_id: str | None = None
    question: str | None = None


class PortfolioProductOption(BaseModel):
    """A review option, never a statement of approval or eligibility."""

    product_id: str
    product_name: str
    financial_institution: str | None = None
    scenario_code: FinancialScenarioCode
    event_id: str | None = None
    why_consider: str
    supporting_quote: str = ""
    explanation_points: list[PortfolioExplanationPoint] = Field(default_factory=list, max_length=3)
    conditions_to_check: list[str] = Field(default_factory=list)
    citations: list[PortfolioCitation] = Field(min_length=1)


class PortfolioServiceCard(BaseModel):
    event_id: str
    scenario_code: FinancialScenarioCode
    service_code: str
    title: str
    customer_need: str
    status: Literal["INFORMATION", "REVIEW_REQUIRED", "NO_CONFLICT"]
    situation: str
    information: list[PortfolioProductOption] = Field(default_factory=list)
    questions: list[str] = Field(default_factory=list)
    notices: list[str] = Field(default_factory=list)


class PortfolioReportPayload(BaseModel):
    """Frozen input consumed by the deterministic Report Generator."""

    generated_on: date
    expected_receipt_date: date | None = None
    receipt_resolution: ReceiptResolution | None = None
    documents: list[PortfolioDocumentResult]
    conflicts: list[PortfolioConflictResult]
    product_options: list[PortfolioProductOption]
    service_cards: list[PortfolioServiceCard] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    retrieval_trace: list[dict[str, Any]] = Field(default_factory=list)
    source_notice: str = (
        "금융상품은 검토 가능한 정보이며 실제 이용 가능 여부, 한도, 금리와 승인은 "
        "최신 약관 및 금융기관 심사를 통해 확인해야 합니다."
    )


class PortfolioRunResult(BaseModel):
    """End-to-end result returned by the LangGraph portfolio workflow."""

    status: Literal["SUCCESS", "REVIEW_REQUIRED", "FAILED"]
    expected_receipt_date: date | None = None
    receipt_resolution: ReceiptResolution | None = None
    documents: list[PortfolioDocumentResult]
    conflicts: list[PortfolioConflictResult]
    product_options: list[PortfolioProductOption]
    service_cards: list[PortfolioServiceCard] = Field(default_factory=list)
    report_path: str | None = None
    plan: ExecutionPlan | None = None
    tool_audit: list[ToolAudit] = Field(default_factory=list)
    llm_calls: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    warnings: list[str] = Field(default_factory=list)
    trace: list[str] = Field(default_factory=list)
    retrieval_trace: list[dict[str, Any]] = Field(default_factory=list)

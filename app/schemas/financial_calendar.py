from __future__ import annotations

from datetime import date
from typing import Any, Literal

from pydantic import BaseModel, Field


class FinancialCalendarEventRow(BaseModel):
    """One company-owned financial event from the two-sheet upload."""

    event_id: str
    event_type_code: str
    event_name: str
    event_date: date
    amount: float
    currency: str
    financial_institution: str
    is_kb_contract: bool


class FinancialCalendarLinkRow(BaseModel):
    """Minimal transaction-to-financial-event relationship from the upload."""

    link_id: str
    transaction_id: str
    event_id: str
    link_type: str
    link_status: str
    dependency_scope: str = "UNKNOWN"


class FinancialCalendarContract(BaseModel):
    """Validated single-company facts from the minimal two-sheet calendar."""

    source_sha256: str
    source_path: str
    version: str
    company_id: str
    events: list[FinancialCalendarEventRow]
    links: list[FinancialCalendarLinkRow]


class DayBand(BaseModel):
    """Inclusive days-until-event range loaded from the priority workbook."""

    label: str
    min_days: int | None = None
    max_days: int | None = None

    def includes(self, days: int) -> bool:
        return (self.min_days is None or days >= self.min_days) and (
            self.max_days is None or days <= self.max_days
        )


class ResponsePriorityBand(DayBand):
    """Inclusive response-priority band."""

    priority: Literal["P1", "P2", "P3", "P4"]


class PriorityExampleRow(BaseModel):
    """Reviewed expected ordering row from the priority workbook."""

    expected_priority_rank: int
    expected_response_priority_level: str
    company_id: str
    event_id: str
    days_until_event: int
    urgency_bucket: str
    link_status: str
    impact_level: str
    event_type: str
    lead_days: int
    same_day_flag: bool
    ranking_scope: str
    display_label: str


class FinancialPriorityPolicy(BaseModel):
    """Machine-readable policy validated from 금융일정_우선순위_로직.xlsx."""

    source_sha256: str
    source_path: str
    version: str
    response_bands: list[ResponsePriorityBand]
    urgency_bands: list[DayBand]
    event_type_order: dict[str, int]
    link_status_order: dict[str, int]
    impact_level_order: dict[str, int]
    routing: dict[str, dict[str, str]]
    portfolio_examples: list[PriorityExampleRow]


class FinancialRiskEventFact(BaseModel):
    """Two-sheet event facts plus the optional reviewed link details."""

    financial_event_id: str
    company_id: str
    event_type_code: str
    event_name: str
    event_date: date
    amount: float
    currency: str
    financial_institution: str | None = None
    is_kb_contract: bool | None = None
    transaction_event_link_id: str | None = None
    link_type: str | None = None
    link_status: str = "UNASSESSED"
    dependency_scope: str = "UNKNOWN"
    linked_amount: float | None = None
    linked_currency: str | None = None


class FinancialRiskEvaluationInput(BaseModel):
    """All deterministic facts required for one transaction risk evaluation."""

    case_id: str
    company_id: str
    transaction_id: str
    as_of_date: date
    source_kind: Literal["MONITORING_SCAN", "USER_REPORTED_DELAY"]
    source_ref_id: str
    payment_obligation_id: str | None = None
    effective_anchor_type: str | None = None
    day_type_effective: str | None = None
    planned_anchor_basis: str | None = None
    revised_anchor_basis: str | None = None
    planned_due_date: date | None
    revised_due_date: date | None
    tenor_days: int | None = None
    anchor_resolved: bool = True
    frontier_warning_days: int = 7
    expected_receipt_amount: float | None = None
    expected_receipt_currency: str | None = None
    events: list[FinancialRiskEventFact] = Field(default_factory=list)


class FinancialPriorityFact(BaseModel):
    """Minimal workbook-defined input used for deterministic ranking."""

    company_id: str
    event_id: str
    days_until_event: int
    link_status: str
    impact_level: str
    event_type_code: str
    lead_days: int
    same_day_flag: bool = False
    is_kb_contract: bool | None = None


class FinancialPriorityResult(FinancialPriorityFact):
    """Priority fact decorated with the reviewed deterministic outputs."""

    response_priority_level: str
    priority_rank: int
    urgency_bucket: str
    display_label: str
    action_owner: str | None = None


class FinancialRiskComparison(BaseModel):
    """Auditable deterministic comparison of one transaction and one event."""

    comparison_id: str
    case_id: str
    company_id: str
    transaction_id: str
    financial_event_id: str
    transaction_event_link_id: str | None = None
    event_type: str
    event_name: str
    event_date: date
    amount: float
    currency: str
    direction: str
    link_type: str | None = None
    link_status: str
    planned_due_date: date | None = None
    revised_due_date: date | None = None
    expected_payment_date: date | None = None
    latest_safe_anchor_date: date | None = None
    frontier_days_remaining: int | None = None
    expected_receipt_amount: float | None = None
    expected_receipt_currency: str | None = None
    planned_lead_days: int | None = None
    lead_days: int
    gap_days: int
    days_until_event: int
    urgency_bucket: str
    date_relation: str
    conflict_origin: str
    tier: str
    display_to_user: bool
    conflict: bool
    same_day_flag: bool
    impact_level: str
    response_priority_level: str
    priority: str
    priority_rank: int | None = None
    display_label: str | None = None
    action_owner: str | None = None
    uncovered_amount: float | None = None
    priority_basis: dict[str, Any] = Field(default_factory=dict)
    reason: str


class FinancialRiskEvaluation(BaseModel):
    """Pure engine result before persistence."""

    case_id: str
    company_id: str
    transaction_id: str
    as_of_date: date
    source_kind: str
    source_ref_id: str
    status: Literal["EVALUATED", "DATA_ACTION_REQUIRED"]
    expected_receipt: dict[str, Any]
    highest_priority: str | None = None
    comparisons: list[FinancialRiskComparison] = Field(default_factory=list)
    conflicts: list[FinancialRiskComparison] = Field(default_factory=list)
    data_actions: list[dict[str, Any]] = Field(default_factory=list)
    priority_rule_version: str
    input_fingerprint: str

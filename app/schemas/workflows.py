from __future__ import annotations

from datetime import date
from typing import Any

from pydantic import BaseModel, Field


class MonitoringRunResult(BaseModel):
    """Stable public contract for one deterministic monitoring run."""

    monitoring_run_id: str
    as_of_date: date
    candidate_count: int
    selected_count: int
    specialist_call_count: int
    new_alert_count: int
    alerts: list[dict[str, Any]] = Field(default_factory=list)
    trace_sequence: list[str] = Field(default_factory=list)
    per_case_results: dict[str, Any] = Field(default_factory=dict)
    ranked_risks: list[dict[str, Any]] = Field(default_factory=list)
    priority_summary: dict[str, Any] = Field(default_factory=dict)
    daily_report: dict[str, Any] | None = None
    daily_summary: str | None = None


class ShipmentDelayScenarioResult(BaseModel):
    """Shipment-owned expected-departure scenarios with no financial calculation."""

    case_id: str
    company_id: str | None
    transaction_id: str | None
    shipment_id: str
    shipment_state_version: int
    calculation_id: str
    basis_version: str
    event_source: str
    reported_at: date
    planned_departure_date: date
    planned_departure_basis: str
    shipment_status_before: str
    shipment_status_after: str
    scenarios: list[dict[str, Any]]
    conditional_notice: str


class PaymentGateResult(BaseModel):
    """DB-grounded permission check before any financial date calculation."""

    case_id: str
    obligation_id: str
    raw_text: str
    calculation_allowed: str
    verified: bool
    permitted: bool
    anchor_type_effective: str
    tenor_days: int | None
    day_type_effective: str | None
    missing_fields: list[str] = Field(default_factory=list)
    customer_question: str | None = None
    calculate_called: bool = False


class FinancialExposureResult(BaseModel):
    """Financial-owned payment dates, conflicts, and persisted evidence."""

    case_id: str
    calculation_id: str
    shipment_calculation_id: str
    basis_version: str
    effective_anchor_type: str | None = None
    scenario_count: int
    conflict_count: int
    scenarios: list[dict[str, Any]]
    conflicts: list[dict[str, Any]]
    data_actions: list[dict[str, Any]] = Field(default_factory=list)
    is_scenario: bool = True
    priority_source_status: str
    trace_sequence: list[str] = Field(default_factory=list)


class ProductCitation(BaseModel):
    """One page-level citation into the checked-in KB product PDF corpus."""

    source_file: str
    page: int = Field(ge=1)
    source_sha256: str
    excerpt: str


class KnowledgeAnswer(BaseModel):
    """Source-scoped answer that never silently crosses a retrieval boundary."""

    answer: str
    source_scope: str
    source_status: str
    evidence_ids: list[str]
    used_case_db: bool
    used_web: bool = False
    citations: list[ProductCitation] = Field(default_factory=list)
    candidates: list[dict[str, Any]] = Field(default_factory=list)

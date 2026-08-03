from __future__ import annotations

from enum import StrEnum
from typing import Any, Literal

from pydantic import BaseModel, Field

SpecialistName = Literal[
    "document_intelligence",
    "trade_case_manager",
    "financial_calendar",
    "shipment_timeline",
    "financial_exposure",
    "product_advisor",
    "report_writer",
    "critic",
    "human",
]


class WorkflowKind(StrEnum):
    """Request-derived workflows supported by the common control graph."""

    CASE_LOOKUP = "CASE_LOOKUP"
    UPLOAD_ANALYSIS = "UPLOAD_ANALYSIS"
    FINANCIAL_CALENDAR_IMPORT = "FINANCIAL_CALENDAR_IMPORT"
    PROACTIVE_MONITORING = "PROACTIVE_MONITORING"
    USER_REPORTED_DELAY = "USER_REPORTED_DELAY"
    PRODUCT_ADVISORY_REPORT = "PRODUCT_ADVISORY_REPORT"


class HumanIssue(BaseModel):
    """One explicit issue whose value is required before execution can continue."""

    issue_code: str = Field(description="Stable public issue identifier")
    prompt: str = Field(description="One concrete question shown to the user")
    response_key: str = Field(description="Whitelisted state key populated by the response")
    value_type: Literal[
        "string",
        "boolean",
        "integer",
        "string_list",
        "integer_list",
        "amount_currency",
    ]
    allowed_values: list[Any] = Field(default_factory=list)
    cancel_values: list[Any] = Field(default_factory=list)
    context: dict[str, Any] = Field(default_factory=dict)


class HumanIssueResponse(BaseModel):
    """Structured LangGraph resume payload for a pending HumanIssue."""

    issue_code: str
    value: Any


class PlanStep(BaseModel):
    """One independently executable task delegated by the Supervisor."""

    task_id: str = Field(description="Stable snake_case task identifier")
    description: str = Field(description="The single outcome this step must produce")
    agent: SpecialistName = Field(description="Recommended owner for Supervisor review")
    tool: str = Field(description="Exact allow-listed Tool name")
    args: dict[str, Any] = Field(default_factory=dict)
    reason: str = Field(description="Public routing reason without hidden reasoning")
    human_issue: HumanIssue | None = None


class Plan(BaseModel):
    """A bounded trade-work plan returned by the Planning Agent."""

    mission_type: str
    case_ids: list[str] = Field(default_factory=list)
    steps: list[PlanStep] = Field(min_length=2, max_length=12)


class ConversationalResponse(BaseModel):
    """A direct response when specialist work is unnecessary."""

    response: str


class PlanningResponse(BaseModel):
    """Structured first-turn decision: execute a plan or answer immediately."""

    final_output: Plan | ConversationalResponse


class ReplanningDecision(BaseModel):
    """Planning Agent decision after the Supervisor completes one step."""

    action: Literal["CONTINUE", "FINAL"]
    summary: str

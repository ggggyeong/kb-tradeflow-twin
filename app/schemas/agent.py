from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field


class AgentResult(BaseModel):
    """Structured result returned by every role agent."""

    status: Literal["SUCCESS", "HUMAN_REQUIRED", "BLOCKED", "FAILED"]
    summary: str
    evidence_ids: list[str] = Field(default_factory=list)
    called_tools: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    data: dict[str, Any] = Field(default_factory=dict)


class CriticReview(BaseModel):
    """Independent review with a hard one-replan ceiling."""

    verdict: Literal["PASS", "REPLAN", "HUMAN_REQUIRED"]
    reasons: list[str] = Field(default_factory=list)
    replan_count: int = Field(ge=0, le=1, default=0)

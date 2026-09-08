from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

ToolName = Literal["call_document_agent", "call_finance_agent", "generate_report"]


class ExecutionPlan(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: Literal["READY", "NEEDS_INPUT", "OUT_OF_SCOPE"]
    steps: list[ToolName] = Field(max_length=3)
    reason: str = Field(min_length=1, max_length=500)

    @model_validator(mode="after")
    def validate_steps(self) -> ExecutionPlan:
        if len(self.steps) != len(set(self.steps)):
            raise ValueError("계획에는 중복 도구를 넣을 수 없습니다.")
        if (self.status == "READY") != bool(self.steps):
            raise ValueError("READY 계획에만 실행 단계가 있어야 합니다.")
        if (
            "call_document_agent" in self.steps
            and "call_finance_agent" in self.steps
            and self.steps.index("call_document_agent") > self.steps.index("call_finance_agent")
        ):
            raise ValueError("전체 분석은 문서 확인 후 금융일정을 검토합니다.")
        if "generate_report" in self.steps and self.steps[-1] != "generate_report":
            raise ValueError("보고서는 계획의 마지막 단계여야 합니다.")
        if self.steps == ["generate_report"]:
            raise ValueError("분석 없이 빈 보고서만 만들 수 없습니다.")
        return self


class ToolAudit(BaseModel):
    tool: str
    status: Literal["SUCCESS", "FAILED", "SKIPPED", "REJECTED"]
    message: str
    call_id: str | None = None


class ModelCall(BaseModel):
    name: str
    arguments: dict[str, Any]
    call_id: str
    response_items: list[dict[str, Any]] = Field(default_factory=list)
    input_tokens: int = 0
    output_tokens: int = 0
    response_id: str | None = None
    served_model: str | None = None
    served_service_tier: str | None = None

from __future__ import annotations

from collections.abc import Callable
from datetime import date
from typing import Any, Literal

from app.agents.document_agent import DocumentAgent
from app.agents.financial_conflict_agent import FinancialConflictAgent
from app.agents.product_advisor_agent import ProductAdvisorAgent
from app.graphs.state import PortfolioState
from app.schemas.portfolio import (
    PortfolioReportPayload,
    PortfolioRunRequest,
    PortfolioRunResult,
)
from app.services.report_generator import PortfolioReportGenerator


class PortfolioPipeline:
    """Dependencies and node functions for the explainable four-step workflow."""

    def __init__(
        self,
        *,
        document_agent: DocumentAgent | None = None,
        financial_conflict_agent: FinancialConflictAgent | None = None,
        product_advisor_agent: ProductAdvisorAgent | None = None,
        report_generator: PortfolioReportGenerator | None = None,
        today: Callable[[], date] = date.today,
    ) -> None:
        self.document_agent = document_agent or DocumentAgent()
        self.financial_conflict_agent = financial_conflict_agent or FinancialConflictAgent()
        self.product_advisor_agent = product_advisor_agent or ProductAdvisorAgent()
        self.report_generator = report_generator or PortfolioReportGenerator()
        self.today = today

    @staticmethod
    def request(state: PortfolioState) -> PortfolioRunRequest:
        return PortfolioRunRequest.model_validate(state["request"])

    def document_step(self, state: PortfolioState) -> dict[str, Any]:
        request = self.request(state)
        return {
            "documents": self.document_agent.run(request.document_paths),
            "expected_receipt_date": request.expected_receipt_date,
            "trace": ["document_agent"],
        }

    def financial_conflict_step(self, state: PortfolioState) -> dict[str, Any]:
        request = self.request(state)
        return {
            "conflicts": self.financial_conflict_agent.run(
                request.expected_receipt_date,
                request.financial_events,
            ),
            "trace": ["financial_conflict_agent"],
        }

    def product_advisor_step(self, state: PortfolioState) -> dict[str, Any]:
        return {
            "product_options": self.product_advisor_agent.run(state["conflicts"]),
            "trace": ["product_advisor_agent"],
        }

    def report_step(self, state: PortfolioState) -> dict[str, Any]:
        request = self.request(state)
        warnings = self._warnings(state)
        payload = PortfolioReportPayload(
            generated_on=self.today(),
            expected_receipt_date=request.expected_receipt_date,
            documents=state["documents"],
            conflicts=state["conflicts"],
            product_options=state["product_options"],
        )
        report = self.report_generator.generate(payload, request.report_output_path)
        return {
            "report_path": str(report["report_path"]),
            "status": "REVIEW_REQUIRED" if warnings else "SUCCESS",
            "warnings": warnings,
            "trace": ["report_generator"],
        }

    @staticmethod
    def _warnings(state: PortfolioState) -> list[str]:
        warnings = [
            f"문서 확인 필요: {item.file_name}"
            for item in state["documents"]
            if item.status != "ANALYZED"
        ]
        if any(item.status == "REVIEW_REQUIRED" for item in state["conflicts"]):
            warnings.append("금융일정과 거래 연결 또는 예상 유입일 확인이 필요합니다.")
        flagged_scenarios = {
            item.scenario_code for item in state["conflicts"] if item.status != "NO_CONFLICT"
        }
        covered_scenarios = {item.scenario_code for item in state["product_options"]}
        for scenario_code in sorted(flagged_scenarios - covered_scenarios):
            warnings.append(f"상품 PDF 근거를 찾지 못했습니다: {scenario_code}")
        return warnings

    @staticmethod
    def result(state: PortfolioState) -> PortfolioRunResult:
        status: Literal["SUCCESS", "REVIEW_REQUIRED"] = (
            "REVIEW_REQUIRED" if state["status"] == "REVIEW_REQUIRED" else "SUCCESS"
        )
        return PortfolioRunResult(
            status=status,
            expected_receipt_date=state.get("expected_receipt_date"),
            documents=state["documents"],
            conflicts=state["conflicts"],
            product_options=state["product_options"],
            report_path=state["report_path"],
            warnings=state.get("warnings", []),
            trace=state.get("trace", []),
        )

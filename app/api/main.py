from __future__ import annotations

from typing import Any

from fastapi import FastAPI, HTTPException

from app.core.paths import PROJECT_ROOT, REPORT_OUTPUT_DIR
from app.graphs.compiled import build_portfolio_graph
from app.schemas.portfolio import PortfolioRunRequest, PortfolioRunResult
from app.services.portfolio_pipeline import PortfolioPipeline

pipeline = PortfolioPipeline()
portfolio_graph = build_portfolio_graph(pipeline)

app = FastAPI(
    title="TradeFlow Portfolio Agent",
    version="3.0.0",
    description=(
        "Supervisor + Document + Finance agents with bounded tools and source-grounded reports."
    ),
)


@app.get("/health")
def health() -> dict[str, Any]:
    return {
        "status": "ok",
        "version": app.version,
        "workflow": ["prepare", "supervisor", "tools", "finalize"],
        "agents": ["supervisor", "document", "finance"],
        "model": pipeline.settings.model,
        "service_tier": pipeline.settings.service_tier,
    }


@app.post("/api/portfolio/analyze", response_model=PortfolioRunResult)
def analyze_portfolio(request: PortfolioRunRequest) -> PortfolioRunResult:
    try:
        roots = [
            (PROJECT_ROOT / "kb_doc").resolve(),
            (PROJECT_ROOT / "data" / "uploads").resolve(),
            (PROJECT_ROOT / "data" / "fixtures").resolve(),
            (PROJECT_ROOT / "data" / "judge_demo_final").resolve(),
        ]
        for path in request.document_paths:
            resolved = path.resolve(strict=True)
            if path.suffix.lower() != ".pdf" or not any(
                resolved.is_relative_to(root) for root in roots
            ):
                raise ValueError("Input PDF outside allowed directories")
        if request.financial_calendar_path:
            calendar = request.financial_calendar_path.resolve(strict=True)
            if calendar.suffix.lower() != ".xlsx" or not any(
                calendar.is_relative_to(root) for root in roots
            ):
                raise ValueError("Input Excel outside allowed directories")
        if request.report_output_path:
            target = request.report_output_path.resolve()
            if target.suffix.lower() != ".pdf" or not target.is_relative_to(
                REPORT_OUTPUT_DIR.resolve()
            ):
                raise ValueError("Report outside output directory")
        state = portfolio_graph.invoke({"request": request.model_dump(mode="python")})
        return pipeline.result(state)
    except (FileNotFoundError, ValueError, RuntimeError) as exc:
        raise HTTPException(
            status_code=422,
            detail="분석 요청을 처리할 수 없습니다. 입력과 서버 설정을 확인해 주세요.",
        ) from exc

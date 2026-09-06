from __future__ import annotations

from typing import Any

from fastapi import FastAPI, HTTPException

from app.graphs.compiled import build_portfolio_graph
from app.schemas.portfolio import PortfolioRunRequest, PortfolioRunResult
from app.services.portfolio_pipeline import PortfolioPipeline

pipeline = PortfolioPipeline()
portfolio_graph = build_portfolio_graph(pipeline)

app = FastAPI(
    title="TradeFlow Portfolio Agent",
    version="1.0.0",
    description=(
        "PDF OCR, three financial-conflict scenarios, cited ChromaDB retrieval, "
        "and deterministic PDF reporting."
    ),
)


@app.get("/health")
def health() -> dict[str, Any]:
    return {
        "status": "ok",
        "version": app.version,
        "workflow": [
            "document_agent",
            "financial_conflict_agent",
            "product_advisor_agent",
            "report_generator",
        ],
    }


@app.post("/api/portfolio/analyze", response_model=PortfolioRunResult)
def analyze_portfolio(request: PortfolioRunRequest) -> PortfolioRunResult:
    try:
        state = portfolio_graph.invoke({"request": request.model_dump(mode="python")})
        return pipeline.result(state)
    except (FileNotFoundError, ValueError, RuntimeError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

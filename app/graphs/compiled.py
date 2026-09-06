from __future__ import annotations

from typing import Any

from langgraph.graph import END, START, StateGraph

from app.graphs.state import PortfolioState
from app.services.portfolio_pipeline import PortfolioPipeline


def build_portfolio_graph(pipeline: PortfolioPipeline | None = None) -> Any:
    """Compile the single public workflow exposed in LangGraph Studio."""
    workflow = pipeline or PortfolioPipeline()
    builder = StateGraph(PortfolioState)
    builder.add_node("document_agent", workflow.document_step)
    builder.add_node("financial_conflict_agent", workflow.financial_conflict_step)
    builder.add_node("product_advisor_agent", workflow.product_advisor_step)
    builder.add_node("report_generator", workflow.report_step)
    builder.add_edge(START, "document_agent")
    builder.add_edge("document_agent", "financial_conflict_agent")
    builder.add_edge("financial_conflict_agent", "product_advisor_agent")
    builder.add_edge("product_advisor_agent", "report_generator")
    builder.add_edge("report_generator", END)
    return builder.compile()


portfolio_graph = build_portfolio_graph()

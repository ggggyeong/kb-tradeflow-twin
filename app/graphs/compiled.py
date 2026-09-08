from __future__ import annotations

from typing import Any

from langgraph.graph import END, START, StateGraph

from app.graphs.state import PortfolioState
from app.services.portfolio_pipeline import PortfolioPipeline


def build_portfolio_graph(pipeline: PortfolioPipeline | None = None) -> Any:
    """Prepare -> Supervisor <-> agent tools, with a bounded conditional loop."""
    workflow = pipeline or PortfolioPipeline()
    builder = StateGraph(PortfolioState)
    builder.add_node("prepare", workflow.prepare_step)
    builder.add_node("supervisor", workflow.supervisor_step)
    builder.add_node("tools", workflow.tool_step)
    builder.add_node("finalize", workflow.finalize_step)
    builder.add_edge(START, "prepare")
    builder.add_edge("prepare", "supervisor")
    builder.add_conditional_edges(
        "supervisor",
        lambda state: state["next_node"],
        {"tools": "tools", "finalize": "finalize", "supervisor": "supervisor"},
    )
    builder.add_edge("tools", "supervisor")
    builder.add_edge("finalize", END)
    return builder.compile().with_config({"recursion_limit": workflow.settings.max_calls * 2 + 8})


portfolio_graph = build_portfolio_graph()

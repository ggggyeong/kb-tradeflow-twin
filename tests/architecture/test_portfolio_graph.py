from app.graphs.compiled import portfolio_graph


def test_public_graph_has_only_the_explainable_four_step_workflow() -> None:
    graph = portfolio_graph.get_graph()
    business_nodes = set(graph.nodes) - {"__start__", "__end__"}
    edges = {(edge.source, edge.target) for edge in graph.edges}

    assert business_nodes == {
        "document_agent",
        "financial_conflict_agent",
        "product_advisor_agent",
        "report_generator",
    }
    assert edges == {
        ("__start__", "document_agent"),
        ("document_agent", "financial_conflict_agent"),
        ("financial_conflict_agent", "product_advisor_agent"),
        ("product_advisor_agent", "report_generator"),
        ("report_generator", "__end__"),
    }

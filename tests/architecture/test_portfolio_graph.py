from app.graphs.compiled import portfolio_graph


def test_public_graph_has_supervisor_and_conditional_agent_tool_loop() -> None:
    graph = portfolio_graph.get_graph()
    business_nodes = set(graph.nodes) - {"__start__", "__end__"}
    edges = {(edge.source, edge.target) for edge in graph.edges}

    assert business_nodes == {
        "prepare",
        "supervisor",
        "tools",
        "finalize",
    }
    assert edges == {
        ("__start__", "prepare"),
        ("prepare", "supervisor"),
        ("supervisor", "tools"),
        ("supervisor", "supervisor"),
        ("tools", "supervisor"),
        ("supervisor", "finalize"),
        ("finalize", "__end__"),
    }
    assert any(edge.conditional for edge in graph.edges if edge.source == "supervisor")

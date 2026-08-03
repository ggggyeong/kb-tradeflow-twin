from __future__ import annotations

from langgraph.checkpoint.memory import InMemorySaver

from app.db.session import init_database
from app.graphs.compiled import build_root_conversation_graph


def test_product_advisory_uses_the_common_planning_supervisor_loop() -> None:
    graph = build_root_conversation_graph(InMemorySaver())
    completed = graph.invoke(
        {
            "messages": [
                {
                    "role": "user",
                    "content": "수입대금과 운전자금에 맞는 KB 상품을 원문 근거와 함께 찾아줘",
                }
            ],
            "customer_role": "IMPORTER",
            "borrower_type": "CORPORATION",
            "known_facts": ["IMPORT_PAYMENT", "KSURE_PROGRAM_ELIGIBILITY"],
            "scenario_codes": ["SUPPLIER_PAYMENT"],
            "request_id": "product-common-control",
        },
        config={"configurable": {"thread_id": "product-common-control"}},
    )

    response = completed["final_response"]
    assert response["status"] == "SUCCESS"
    assert response["results"]["evidence_review"]["verdict"] == "PASS"
    options = response["results"]["retrieve_product_evidence"]["options"]
    assert options
    assert all(option["citations"] for option in options)
    assert response["planning_log"][0]["action"] == "CREATE_PLAN"
    assert response["planning_log"][-1]["action"] == "FINAL"
    assert all(
        item.get("returns_to") == "planning_agent"
        for item in response["supervisor_log"]
        if item["phase"] == "COLLECT"
    )


def test_missing_delay_days_interrupts_with_a_typed_issue() -> None:
    init_database()
    graph = build_root_conversation_graph(InMemorySaver())
    interrupted = graph.invoke(
        {
            "messages": [
                {
                    "role": "user",
                    "content": "TRD-003의 B/L 지연 영향을 확인해줘",
                }
            ],
            "reported_at": "2026-08-20",
            "request_id": "missing-delay-days",
        },
        config={"configurable": {"thread_id": "missing-delay-days"}},
    )

    payload = interrupted["__interrupt__"][0].value
    issue = payload["issue"]
    assert issue["issue_code"] == "DELAY_DAYS_REQUIRED"
    assert issue["response_key"] == "delay_days"
    assert issue["value_type"] == "integer_list"


def test_common_root_has_no_scenario_specific_control_nodes() -> None:
    graph = build_root_conversation_graph()
    nodes = set(graph.get_graph().nodes)
    assert {
        "planning_agent",
        "supervisor_agent",
        "human_question",
        "human_confirmation",
        "product_advisor_agent",
        "critic_agent",
    } <= nodes
    xray_nodes = set(graph.get_graph(xray=True).nodes)
    assert {
        "product_advisor_agent:model",
        "product_advisor_agent:tools",
        "critic_agent:model",
        "critic_agent:tools",
    } <= xray_nodes

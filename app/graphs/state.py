from __future__ import annotations

import operator
from typing import Annotated, Any, TypedDict

from langchain_core.messages import AnyMessage
from langgraph.graph.message import add_messages

from app.schemas.agent import AgentResult


class TradeFlowState(TypedDict, total=False):
    """Shared minimal state used by root, case, and monitoring graphs."""

    request_id: str
    thread_id: str
    company_id: str
    mission_type: str
    case_ids: list[str]
    selected_agents: list[str]
    called_agents: Annotated[list[str], operator.add]
    task_queue: list[Any]
    agent_results: dict[str, Any]
    current_agent: str
    agent_inputs: dict[str, dict[str, Any]]
    pending_issue: dict[str, Any] | None
    replan_count: int
    final_response: dict[str, Any]
    intent: str
    user_query: str
    workflow_kind: str
    batch_id: str
    workbook_path: str
    reported_at: str
    delay_days: list[int]
    customer_role: str
    borrower_type: str
    known_facts: list[str]
    not_met_facts: list[str]
    scenario_codes: list[str]
    rm_inbox: str
    generate_report: bool
    execution_plan: dict[str, Any]
    tool_decisions: list[dict[str, Any]]
    control_task_queue: list[dict[str, Any]]
    current_task: dict[str, Any] | None
    task_results: dict[str, Any]
    execution_log: list[dict[str, Any]]
    review_log: list[dict[str, Any]]
    human_context: dict[str, Any]
    confirmation_id: str
    human_answers: list[dict[str, Any]]
    final_answer: str
    # Planning ↔ Supervisor loop channels. `plan` contains only the remaining
    # tasks; `past_steps` is the public, Tool-grounded completion history.
    plan: list[dict[str, Any]]
    original_plan: list[dict[str, Any]]
    past_steps: list[dict[str, Any]]
    planning_initialized: bool
    planning_iteration: int
    planning_log: list[dict[str, Any]]
    supervisor_phase: str
    supervisor_log: list[dict[str, Any]]
    supervisor_route: dict[str, Any]
    trade_case_initialized: bool
    as_of_date: str
    monitoring_limit: int
    candidate_cases: list[dict[str, Any]]
    selected_missions: list[dict[str, Any]]
    per_case_results: dict[str, Any]
    alert_decisions: list[dict[str, Any]]
    monitoring_result: dict[str, Any]
    daily_report_result: dict[str, Any]
    # Shared adapter channels let each `create_agent` graph be mounted directly
    # as a discoverable LangGraph subgraph. Prepare/collect nodes clear these
    # channels between roles, so one specialist never inherits another's chat.
    messages: Annotated[list[AnyMessage], add_messages]
    structured_response: AgentResult | dict[str, Any] | None
    agent_started_at: float

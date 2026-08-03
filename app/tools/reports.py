from __future__ import annotations

from datetime import date
from typing import Any

from langchain_core.tools import tool

from app.db.session import session_scope
from app.services.monitoring import MonitoringService
from app.services.reports import ReportService


def _priority_key(value: object) -> tuple[int, int, str]:
    """Sort P1 before P2 while keeping unknown labels deterministic."""
    label = str(value or "").strip().upper()
    if label.startswith("P") and label[1:].isdigit():
        return (0, int(label[1:]), label)
    return (1, 0, label or "UNRANKED")


def _rank_monitoring_results(
    evidence_snapshot: dict[str, Any],
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Build a presentation ranking from immutable Financial Exposure snapshots."""
    latest_by_case: dict[str, dict[str, Any]] = {}
    for result in evidence_snapshot.values():
        if not isinstance(result, dict):
            continue
        if result.get("source_kind") != "MONITORING_SCAN" or not result.get("case_id"):
            continue
        latest_by_case[str(result["case_id"])] = result

    ordered = sorted(
        latest_by_case.values(),
        key=lambda item: (
            _priority_key(item.get("highest_priority")),
            -len(item.get("conflicts", []) if isinstance(item.get("conflicts"), list) else []),
            str(item.get("case_id")),
        ),
    )
    ranked = [
        {
            "rank": index,
            "case_id": str(item["case_id"]),
            "calculation_id": item.get("calculation_id"),
            "highest_priority": item.get("highest_priority"),
            "conflict_count": len(
                item.get("conflicts", []) if isinstance(item.get("conflicts"), list) else []
            ),
            "conflicts": item.get("conflicts", []),
            "data_actions": item.get("data_actions", []),
        }
        for index, item in enumerate(ordered, start=1)
    ]
    priority_counts: dict[str, int] = {}
    for item in ranked:
        label = str(item.get("highest_priority") or "UNRANKED")
        priority_counts[label] = priority_counts.get(label, 0) + 1
    return ranked, {
        "total_ranked": len(ranked),
        "cases_with_conflicts": sum(1 for item in ranked if item["conflict_count"]),
        "total_conflicts": sum(int(item["conflict_count"]) for item in ranked),
        "by_highest_priority": {
            key: priority_counts[key] for key in sorted(priority_counts, key=_priority_key)
        },
        "ordered_case_ids": [item["case_id"] for item in ranked],
    }


@tool
def finalize_manual_monitoring(
    as_of_date: str,
    request_id: str,
    evidence_snapshot: dict[str, Any],
) -> dict[str, Any]:
    """Persist a Critic-approved Chat monitoring run, Alerts, and its internal PDF."""
    critic = evidence_snapshot.get("evidence_review")
    if not isinstance(critic, dict) or critic.get("verdict") != "PASS":
        raise ValueError("Manual monitoring requires a PASS evidence_review result")
    parsed_date = date.fromisoformat(as_of_date)
    ranked_risks, priority_summary = _rank_monitoring_results(evidence_snapshot)
    candidate_result = evidence_snapshot.get("select_monitoring_candidates")
    candidates = (
        list(candidate_result.get("candidates", [])) if isinstance(candidate_result, dict) else []
    )
    final_response = {
        "status": "SUCCESS",
        "execution_source": "CHAT_MANUAL",
        "as_of_date": as_of_date,
        "candidate_count": len(candidates),
        "case_ids": [
            str(item["case_id"])
            for item in candidates
            if isinstance(item, dict) and item.get("case_id")
        ],
        "ranked_risks": ranked_risks,
        "priority_summary": priority_summary,
        "results": evidence_snapshot,
    }
    with session_scope() as session:
        monitoring = (
            MonitoringService(session)
            .persist_final_response(
                as_of_date=parsed_date,
                request_id=request_id,
                final_response=final_response,
            )
            .model_dump(mode="json")
        )
        report = ReportService(session).generate_daily_monitoring_report(
            monitoring["monitoring_run_id"],
            {
                "monitoring_run_id": monitoring["monitoring_run_id"],
                "as_of_date": as_of_date,
                "ranked_risks": ranked_risks,
                "priority_summary": priority_summary,
                "per_case_results": monitoring.get("per_case_results", {}),
                "monitoring_result": monitoring,
                "results": evidence_snapshot,
            },
        )
        return {
            **monitoring,
            "daily_report": report,
            "daily_summary": report.get("daily_summary")
            or (report.get("summary") or {}).get("summary_text"),
        }


@tool
def build_briefing_payload(
    case_id: str,
    product_options: list[dict[str, Any]] | None = None,
    as_of_date: str | None = None,
    rm_inbox: str | None = None,
) -> dict[str, Any]:
    """Freeze the workbook layout, case facts, product evidence, and risk snapshot."""
    parsed_date = date.fromisoformat(as_of_date) if as_of_date else None
    with session_scope() as session:
        return ReportService(session).build_briefing_payload(
            case_id,
            product_options=product_options,
            as_of_date=parsed_date,
            rm_inbox=rm_inbox,
        )


@tool
def render_customer_report(
    briefing_payload: dict[str, Any],
    consent: bool,
) -> dict[str, Any]:
    """Render the customer workbook blocks from one frozen payload."""
    with session_scope() as session:
        return ReportService(session).render_report(
            briefing_payload=briefing_payload,
            audience="CUSTOMER",
            consent=consent,
        )


@tool
def render_rm_report(
    briefing_payload: dict[str, Any],
    consent: bool,
) -> dict[str, Any]:
    """Render the KB employee workbook blocks from the same frozen payload."""
    with session_scope() as session:
        return ReportService(session).render_report(
            briefing_payload=briefing_payload,
            audience="RM",
            consent=consent,
        )

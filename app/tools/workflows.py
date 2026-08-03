from __future__ import annotations

from datetime import date
from typing import Any

from langchain_core.tools import tool

from app.db.session import session_scope


def _batch_summary(payload: Any) -> dict[str, Any]:
    analysis = payload.model_dump(mode="json")
    summary = analysis["summary"]
    return {
        "batch_id": analysis["batch_id"],
        "file_count": summary["file_count"],
        "case_count": summary["case_count"],
        "cases": [
            {
                "case_id": item["case_id"],
                "status": item["status"],
                "document_ids": item["document_ids"],
                "missing_documents": item["missing_documents"],
                "missing_core_fields": item["missing_core_fields"],
            }
            for item in summary["cases"]
        ],
        "unmatched_document_ids": summary["unmatched_document_ids"],
        "type_confirmation_document_ids": summary["type_confirmation_document_ids"],
        "human_requests": summary.get("human_requests", []),
        "missing_document_issues": summary.get("missing_document_issues", []),
        "confirmation_pending": summary["confirmation_pending"],
        "manual_overrides": analysis.get("manual_overrides", []),
        "trace_sequence": [item["name"] for item in analysis["trace"]],
    }


@tool
def stage_document_intelligence(batch_id: str) -> dict[str, Any]:
    """Classify, extract, validate, and persist the uploaded document envelope."""
    from app.services.ingestion.batch_service import (
        BatchService,
        uploaded_document_paths,
    )

    paths = uploaded_document_paths(batch_id)
    with session_scope() as session:
        result = BatchService(session).stage_document_intelligence(paths, batch_id)
        payload = result.model_dump(mode="json")
        return {
            "batch_id": batch_id,
            "file_count": len(payload["documents"]),
            "documents": [
                {
                    "document_id": item["document_id"],
                    "file_name": item["file_name"],
                    "kind": item["kind"],
                    "sha256": item["sha256"],
                    "classification": (
                        item["extraction"]["classification"] if item.get("extraction") else None
                    ),
                    "missing_core_fields": (
                        item["extraction"]["missing_core_fields"] if item.get("extraction") else []
                    ),
                }
                for item in payload["documents"]
            ],
            "trace_sequence": [item["name"] for item in payload["trace"]],
        }


@tool
def bundle_trade_cases(batch_id: str) -> dict[str, Any]:
    """Match staged documents, check completeness, and surface exact Human requests."""
    from app.services.ingestion.batch_service import BatchService

    with session_scope() as session:
        return _batch_summary(BatchService(session).bundle_staged_batch(batch_id))


@tool
def apply_document_field_override(
    batch_id: str,
    document_id: str,
    exact_standard_field: str,
    value: Any,
    actor: str,
    reason: str | None = None,
) -> dict[str, Any]:
    """Append one explicit Human field value and recompute transaction completeness."""
    from app.services.ingestion.batch_service import BatchService

    with session_scope() as session:
        analysis = BatchService(session).apply_document_field_override(
            batch_id,
            document_id,
            exact_standard_field,
            value,
            actor=actor,
            reason=reason,
        )
        return _batch_summary(analysis)


@tool
def commit_trade_cases(
    batch_id: str,
    confirmed_case_ids: list[str] | None = None,
) -> dict[str, Any]:
    """Commit persisted transaction shells through the public ingestion interface."""
    from app.services.ingestion.batch_service import BatchService

    with session_scope() as session:
        result = BatchService(session).commit(
            batch_id,
            set(confirmed_case_ids or []),
        )
        return result.model_dump(mode="json")


@tool
def record_shipment_delay_scenarios(
    case_id: str,
    reported_at: str,
    request_id: str,
    expected_delay_days: list[int],
) -> dict[str, Any]:
    """Record audit time and ETD-relative expected shipment delay separately."""
    from app.services.advisory import AdvisoryService

    with session_scope() as session:
        result = AdvisoryService(session).record_shipment_delay_scenarios(
            case_id,
            date.fromisoformat(reported_at),
            request_id,
            expected_delay_days,
        )
        return result.model_dump(mode="json")


@tool
def review_workflow_evidence(
    workflow_kind: str,
    expected_task_ids: list[str],
    evidence_snapshot: dict[str, Any],
) -> dict[str, Any]:
    """Review generic workflow evidence without demo IDs or fixed expected values."""
    checks: list[dict[str, Any]] = []

    for task_id in expected_task_ids:
        result = evidence_snapshot.get(task_id)
        checks.append(
            {
                "check": f"result:{task_id}",
                "passed": result not in (None, {}, ""),
                "actual": "PRESENT" if result not in (None, {}, "") else "MISSING",
            }
        )

    if workflow_kind == "PRODUCT_ADVISORY_REPORT":
        product_result = evidence_snapshot.get("retrieve_product_evidence", {})
        if product_result:
            options = product_result.get("options", [])
            citations_present = all(item.get("citations") for item in options)
            checks.append(
                {
                    "check": "product_options_have_page_citations",
                    "passed": bool(options) and citations_present,
                    "actual": len(options),
                }
            )
        customer_report = evidence_snapshot.get("render_customer_report", {})
        rm_report = evidence_snapshot.get("render_rm_report", {})
        if customer_report or rm_report:
            customer_basis = customer_report.get("basis_version") or customer_report.get("basis")
            rm_basis = rm_report.get("basis_version") or rm_report.get("basis")
            checks.extend(
                [
                    {
                        "check": "customer_and_rm_reports_present",
                        "passed": bool(customer_report) and bool(rm_report),
                        "actual": [bool(customer_report), bool(rm_report)],
                    },
                    {
                        "check": "report_same_basis",
                        "passed": bool(customer_basis) and customer_basis == rm_basis,
                        "actual": [customer_basis, rm_basis],
                    },
                ]
            )
    elif workflow_kind == "USER_REPORTED_DELAY":
        shipment = evidence_snapshot.get("record_delay", {})
        if shipment:
            checks.append(
                {
                    "check": "shipment_state_not_promoted_by_report",
                    "passed": shipment.get("shipment_status_before")
                    == shipment.get("shipment_status_after"),
                    "actual": [
                        shipment.get("shipment_status_before"),
                        shipment.get("shipment_status_after"),
                    ],
                }
            )
    elif workflow_kind == "PROACTIVE_MONITORING":
        candidates = evidence_snapshot.get("select_monitoring_candidates", {})
        checks.append(
            {
                "check": "candidate_query_completed",
                "passed": isinstance(candidates.get("candidates", []), list),
                "actual": candidates.get("candidate_count"),
            }
        )
    elif workflow_kind == "FINANCIAL_CALENDAR_IMPORT":
        imported = evidence_snapshot.get("import_financial_calendar", {})
        checks.append(
            {
                "check": "calendar_import_summary",
                "passed": bool(imported),
                "actual": sorted(imported) if isinstance(imported, dict) else None,
            }
        )

    return {
        "workflow_kind": workflow_kind,
        "verdict": "PASS" if checks and all(item["passed"] for item in checks) else "REPLAN",
        "checks": checks,
    }

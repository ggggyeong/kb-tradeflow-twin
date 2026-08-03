from __future__ import annotations

import hashlib
from datetime import date
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import Alert, CalculationResult, MonitoringRun
from app.schemas.workflows import MonitoringRunResult
from app.services.observability import local_span, record_local_event


def _stable_id(prefix: str, value: str) -> str:
    return f"{prefix}-{hashlib.sha256(value.encode()).hexdigest()[:16]}"


def _risk_snapshots(task_results: dict[str, Any]) -> dict[str, dict[str, Any]]:
    snapshots: dict[str, dict[str, Any]] = {}
    for result in task_results.values():
        if (
            isinstance(result, dict)
            and result.get("case_id")
            and result.get("source_kind") == "MONITORING_SCAN"
            and result.get("calculation_id")
        ):
            snapshots[str(result["case_id"])] = result
    return snapshots


def _shipment_snapshots(task_results: dict[str, Any]) -> dict[str, dict[str, Any]]:
    snapshots: dict[str, dict[str, Any]] = {}
    for task_id, result in task_results.items():
        if (
            str(task_id).startswith("shipment_snapshot_")
            and isinstance(result, dict)
            and result.get("case_id")
        ):
            snapshots[str(result["case_id"])] = result
    return snapshots


def _ranked_case_ids(
    *,
    ranked_risks: list[dict[str, Any]],
    candidates: list[dict[str, Any]],
    risk_by_case: dict[str, dict[str, Any]],
) -> list[str]:
    """Preserve the graph ranking, with deterministic fallbacks for malformed output."""
    ordered: list[str] = []
    seen: set[str] = set()
    for item in ranked_risks:
        case_id = str(item.get("case_id") or "")
        if case_id and case_id in risk_by_case and case_id not in seen:
            ordered.append(case_id)
            seen.add(case_id)
    for item in candidates:
        case_id = str(item.get("case_id") or "")
        if case_id and case_id not in seen:
            ordered.append(case_id)
            seen.add(case_id)
    for case_id in sorted(risk_by_case):
        if case_id not in seen:
            ordered.append(case_id)
    return ordered


def _alert_signals(snapshot: dict[str, Any]) -> list[str]:
    signals: list[str] = []
    if snapshot.get("conflicts"):
        signals.append("FINANCIAL_SCHEDULE_CONFLICT")
    if snapshot.get("data_actions") or snapshot.get("status") == "DATA_ACTION_REQUIRED":
        signals.append("FINANCIAL_DATA_ACTION_REQUIRED")
    return signals


def _severity(snapshot: dict[str, Any], signal: str) -> str:
    if signal == "FINANCIAL_DATA_ACTION_REQUIRED" and not snapshot.get("conflicts"):
        return "WARNING"
    priority = str(snapshot.get("highest_priority") or "").upper()
    if priority in {"P1", "IMMEDIATE", "CRITICAL"}:
        return "CRITICAL"
    if priority in {"P2", "P3", "HIGH"}:
        return "WARNING"
    return "INFO"


class MonitoringService:
    """Persist one Critic-reviewed manual Chat monitoring snapshot."""

    def __init__(self, session: Session) -> None:
        self.session = session

    def persist_final_response(
        self,
        *,
        as_of_date: date,
        request_id: str,
        final_response: dict[str, Any],
    ) -> MonitoringRunResult:
        """Persist reviewed graph evidence exactly once for one request."""
        if final_response.get("status") != "SUCCESS":
            raise RuntimeError("Only a Critic-approved monitoring response may be persisted")

        run_id = _stable_id("MON", f"{as_of_date.isoformat()}:{request_id}")
        existing_run = self.session.get(MonitoringRun, run_id)
        if existing_run is not None:
            stored_result = dict(existing_run.trace_json or {}).get("result")
            if isinstance(stored_result, dict):
                return MonitoringRunResult.model_validate(stored_result)
            raise RuntimeError(
                f"Monitoring run {run_id} exists without a resumable result snapshot"
            )

        task_results = dict(final_response.get("results", {}))
        candidate_result = dict(task_results.get("select_monitoring_candidates", {}))
        candidates = list(candidate_result.get("candidates", []))
        risk_by_case = _risk_snapshots(task_results)
        shipment_by_case = _shipment_snapshots(task_results)
        ranked_risks = [
            dict(item) for item in final_response.get("ranked_risks", []) if isinstance(item, dict)
        ]
        ordered_case_ids = _ranked_case_ids(
            ranked_risks=ranked_risks,
            candidates=candidates,
            risk_by_case=risk_by_case,
        )
        candidate_by_case = {
            str(item["case_id"]): item for item in candidates if item.get("case_id")
        }
        per_case: dict[str, Any] = {
            case_id: {
                "candidate": candidate_by_case.get(case_id),
                "shipment_snapshot": shipment_by_case.get(case_id),
                "financial_risk_snapshot": risk_by_case.get(case_id),
            }
            for case_id in ordered_case_ids
        }

        # Financial Exposure persists CalculationResult before the graph returns.
        # Alert dedup therefore keys on that immutable snapshot, not Shipment state.
        alerts: list[dict[str, Any]] = []
        for case_id in ordered_case_ids:
            snapshot = risk_by_case.get(case_id)
            if snapshot is None:
                continue
            calculation = self.session.get(
                CalculationResult,
                str(snapshot["calculation_id"]),
            )
            if calculation is None:
                raise RuntimeError(
                    f"Monitoring risk snapshot was not persisted: {snapshot['calculation_id']}"
                )
            for signal in _alert_signals(snapshot):
                dedup_key = (
                    f"{case_id}|{signal}|{calculation.calculation_id}|"
                    f"{calculation.input_fingerprint}"
                )
                existing = self.session.scalar(select(Alert).where(Alert.dedup_key == dedup_key))
                if existing is not None:
                    continue
                severity = _severity(snapshot, signal)
                alert = Alert(
                    alert_id=_stable_id("ALT", dedup_key),
                    case_id=case_id,
                    dedup_key=dedup_key,
                    signal_code=signal,
                    severity=severity,
                    evidence_json={
                        "calculation_id": calculation.calculation_id,
                        "input_fingerprint": calculation.input_fingerprint,
                        "basis_version": calculation.basis_version,
                        "as_of_date": (
                            calculation.as_of_date.isoformat()
                            if calculation.as_of_date
                            else as_of_date.isoformat()
                        ),
                        "source_kind": calculation.source_kind,
                        "source_ref_id": calculation.source_ref_id,
                        "highest_priority": snapshot.get("highest_priority"),
                        "conflict_count": snapshot.get("conflict_count", 0),
                        "conflicts": snapshot.get("conflicts", []),
                        "data_actions": snapshot.get("data_actions", []),
                        "shipment_snapshot": shipment_by_case.get(case_id),
                    },
                )
                self.session.add(alert)
                alerts.append(
                    {
                        "alert_id": alert.alert_id,
                        "case_id": case_id,
                        "signal_code": signal,
                        "severity": severity,
                        "calculation_id": calculation.calculation_id,
                    }
                )

        trace_sequence = [
            "planning_agent",
            "supervisor_agent",
            "financial_calendar_agent",
            "shipment_timeline_agent",
            "financial_exposure_agent",
            "critic_agent",
            "report_writer_agent",
        ]
        selected_count = len(candidates)
        specialist_calls = selected_count * 2
        with local_span(
            self.session,
            request_id,
            "workflow",
            "chat_manual_monitoring",
            {
                "as_of_date": as_of_date.isoformat(),
                "candidate_count": len(candidates),
            },
        ) as span:
            for node_name in trace_sequence:
                record_local_event(
                    self.session,
                    request_id=request_id,
                    span_type="graph_node",
                    name=node_name,
                    input_summary={"as_of_date": as_of_date.isoformat()},
                    output_summary={
                        "candidate_count": len(candidates),
                        "risk_snapshot_count": len(risk_by_case),
                        "new_alert_count": len(alerts),
                    },
                    metadata={"graph": "root_conversation_graph"},
                )
            result = MonitoringRunResult(
                monitoring_run_id=run_id,
                as_of_date=as_of_date,
                candidate_count=len(candidates),
                selected_count=selected_count,
                specialist_call_count=specialist_calls,
                new_alert_count=len(alerts),
                alerts=alerts,
                trace_sequence=trace_sequence,
                per_case_results=per_case,
                ranked_risks=ranked_risks,
                priority_summary=dict(final_response.get("priority_summary", {})),
            )
            run = MonitoringRun(
                monitoring_run_id=run_id,
                as_of_date=as_of_date,
                candidate_count=len(candidates),
                selected_count=selected_count,
                result_count=len(risk_by_case),
                new_alert_count=len(alerts),
                trace_json={
                    "sequence": trace_sequence,
                    "risk_snapshots_before_alerts": True,
                    "alert_dedup_basis": "CALCULATION_RESULT",
                    "specialist_call_count": specialist_calls,
                    "ranked_case_ids": ordered_case_ids,
                    "result": result.model_dump(mode="json"),
                },
            )
            self.session.add(run)
            span.update(
                {
                    "candidate_count": result.candidate_count,
                    "risk_snapshot_count": len(risk_by_case),
                    "specialist_call_count": result.specialist_call_count,
                    "new_alert_count": result.new_alert_count,
                }
            )
            self.session.flush()
            return result

from __future__ import annotations

from datetime import date

from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from app.db.models import Alert, Base, CalculationResult, TradeCase
from app.services.monitoring import MonitoringService


def test_manual_monitoring_persists_calculation_snapshot_and_deduplicates_alerts() -> None:
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    as_of = date(2026, 7, 29)
    risk_snapshot = {
        "case_id": "TRD-MONITOR",
        "calculation_id": "RISK-MONITOR",
        "basis_version": "basis-monitor",
        "as_of_date": as_of.isoformat(),
        "source_kind": "MONITORING_SCAN",
        "source_ref_id": "TRD-MONITOR:2026-07-29:state-v1",
        "input_fingerprint": "f" * 64,
        "status": "DATA_ACTION_REQUIRED",
        "highest_priority": None,
        "conflict_count": 0,
        "conflicts": [],
        "data_actions": [
            {
                "code": "PAYMENT_ANCHOR_REQUIRED",
                "message": "지급 기준일 확인이 필요합니다.",
            }
        ],
    }
    reviewed_payload = {
        "final_response": {
            "status": "SUCCESS",
            "execution_source": "CHAT_MANUAL",
            "results": {
                "select_monitoring_candidates": {
                    "as_of_date": as_of.isoformat(),
                    "candidate_count": 1,
                    "candidates": [
                        {
                            "case_id": "TRD-MONITOR",
                            "company_id": "COMP-MONITOR",
                            "transaction_id": "TXN-MONITOR",
                            "basis_version": "basis-monitor",
                        }
                    ],
                },
                "shipment_snapshot_trd_monitor": {
                    "case_id": "TRD-MONITOR",
                    "status": "UNKNOWN",
                    "state_version": 1,
                },
                "proactive_risk_scan_trd_monitor": risk_snapshot,
                "evidence_review": {"verdict": "PASS"},
            },
        }
    }
    with Session(engine) as session:
        session.add(
            TradeCase(
                case_id="TRD-MONITOR",
                company_id=None,
                transaction_id="TXN-MONITOR",
                company="Monitor Co.",
                counterparty="Buyer",
                status="MONITORING_READY",
                monitoring_enabled=True,
                basis_version="basis-monitor",
            )
        )
        session.add(
            CalculationResult(
                calculation_id="RISK-MONITOR",
                case_id="TRD-MONITOR",
                company_id=None,
                transaction_id="TXN-MONITOR",
                basis_version="basis-monitor",
                scenario_name="PROACTIVE_FINANCIAL_RISK",
                as_of_date=as_of,
                source_kind="MONITORING_SCAN",
                source_ref_id="TRD-MONITOR:2026-07-29:state-v1",
                input_fingerprint="f" * 64,
                dedup_key="risk-snapshot-dedup",
                result_json=risk_snapshot,
                audit_json={},
                tool_version="financial-risk.v18.0",
                is_scenario=False,
            )
        )
        session.flush()

        first = MonitoringService(session).persist_final_response(
            as_of_date=as_of,
            request_id="monitor-first",
            final_response=reviewed_payload["final_response"],
        )
        second = MonitoringService(session).persist_final_response(
            as_of_date=as_of,
            request_id="monitor-second",
            final_response=reviewed_payload["final_response"],
        )

        assert first.candidate_count == 1
        assert first.selected_count == 1
        assert first.specialist_call_count == 2
        assert first.new_alert_count == 1
        assert first.alerts[0]["signal_code"] == "FINANCIAL_DATA_ACTION_REQUIRED"
        assert (
            first.per_case_results["TRD-MONITOR"]["financial_risk_snapshot"]["calculation_id"]
            == "RISK-MONITOR"
        )
        assert second.new_alert_count == 0
        alerts = list(session.scalars(select(Alert)))
        assert len(alerts) == 1
        assert alerts[0].evidence_json["calculation_id"] == "RISK-MONITOR"
        assert "RISK-MONITOR" in alerts[0].dedup_key


def test_manual_monitoring_returns_cases_and_alerts_in_reviewed_risk_order() -> None:
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    as_of = date(2026, 7, 29)
    cases = [
        ("TRD-P2", "RISK-P2", "P2", "2"),
        ("TRD-P1-B", "RISK-P1-B", "P1", "b"),
        ("TRD-P1-A", "RISK-P1-A", "P1", "a"),
    ]
    snapshots = {
        case_id: {
            "case_id": case_id,
            "calculation_id": calculation_id,
            "basis_version": f"basis-{case_id.lower()}",
            "as_of_date": as_of.isoformat(),
            "source_kind": "MONITORING_SCAN",
            "source_ref_id": f"{case_id}:{as_of.isoformat()}:state-v1",
            "input_fingerprint": fingerprint * 64,
            "status": "EVALUATED",
            "highest_priority": priority,
            "conflict_count": 1,
            "conflicts": [{"conflict_id": f"CONFLICT-{case_id}"}],
            "data_actions": [],
        }
        for case_id, calculation_id, priority, fingerprint in cases
    }
    ranked_case_ids = ["TRD-P1-A", "TRD-P1-B", "TRD-P2"]
    candidate_items = [
        {
            "case_id": case_id,
            "company_id": f"COMP-{case_id}",
            "transaction_id": f"TXN-{case_id}",
            "basis_version": f"basis-{case_id.lower()}",
        }
        for case_id, _, _, _ in cases
    ]
    reviewed_payload = {
        "final_response": {
            "status": "SUCCESS",
            "execution_source": "CHAT_MANUAL",
            "ranked_risks": [
                {
                    "rank": rank,
                    "case_id": case_id,
                    "calculation_id": snapshots[case_id]["calculation_id"],
                    "highest_priority": snapshots[case_id]["highest_priority"],
                    "conflict_count": 1,
                    "conflicts": snapshots[case_id]["conflicts"],
                }
                for rank, case_id in enumerate(ranked_case_ids, start=1)
            ],
            "priority_summary": {
                "total_ranked": 3,
                "cases_with_conflicts": 3,
                "total_conflicts": 3,
                "by_highest_priority": {"P1": 2, "P2": 1},
                "ordered_case_ids": ranked_case_ids,
            },
            "results": {
                "select_monitoring_candidates": {
                    "as_of_date": as_of.isoformat(),
                    "candidate_count": 3,
                    "candidates": candidate_items,
                },
                **{
                    f"shipment_snapshot_{case_id.lower()}": {
                        "case_id": case_id,
                        "status": "UNKNOWN",
                        "state_version": 1,
                    }
                    for case_id in snapshots
                },
                **{
                    f"proactive_risk_scan_{case_id.lower()}": snapshot
                    for case_id, snapshot in snapshots.items()
                },
                "evidence_review": {"verdict": "PASS"},
            },
        }
    }
    with Session(engine) as session:
        for case_id, calculation_id, _, fingerprint in cases:
            snapshot = snapshots[case_id]
            session.add(
                TradeCase(
                    case_id=case_id,
                    company_id=None,
                    transaction_id=f"TXN-{case_id}",
                    company=f"{case_id} Co.",
                    counterparty="Buyer",
                    status="MONITORING_READY",
                    monitoring_enabled=True,
                    basis_version=str(snapshot["basis_version"]),
                )
            )
            session.add(
                CalculationResult(
                    calculation_id=calculation_id,
                    case_id=case_id,
                    company_id=None,
                    transaction_id=f"TXN-{case_id}",
                    basis_version=str(snapshot["basis_version"]),
                    scenario_name="PROACTIVE_FINANCIAL_RISK",
                    as_of_date=as_of,
                    source_kind="MONITORING_SCAN",
                    source_ref_id=str(snapshot["source_ref_id"]),
                    input_fingerprint=fingerprint * 64,
                    dedup_key=f"risk-dedup-{case_id}",
                    result_json=snapshot,
                    audit_json={},
                    tool_version="financial-risk.v18.0",
                    is_scenario=False,
                )
            )
        session.flush()

        result = MonitoringService(session).persist_final_response(
            as_of_date=as_of,
            request_id="monitor-ranked",
            final_response=reviewed_payload["final_response"],
        )

        assert list(result.per_case_results) == ranked_case_ids
        assert [item["case_id"] for item in result.alerts] == ranked_case_ids
        assert [
            item["financial_risk_snapshot"]["calculation_id"]
            for item in result.per_case_results.values()
        ] == ["RISK-P1-A", "RISK-P1-B", "RISK-P2"]
        assert result.new_alert_count == 3
        assert len(list(session.scalars(select(CalculationResult)))) == 3

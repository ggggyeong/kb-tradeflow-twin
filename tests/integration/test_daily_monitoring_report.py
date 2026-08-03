from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest
from pypdf import PdfReader
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session

from app.db.models import Base, DailyMonitoringReport, MonitoringRun
from app.schemas.workflows import MonitoringRunResult
from app.services.reports import ReportService


def _monitoring_payload() -> dict[str, object]:
    return {
        "monitoring_run_id": "MON-DAILY-001",
        "as_of_date": "2026-09-18",
        "specialist_call_count": 4,
        "ranked_risks": [
            {
                "rank": 1,
                "case_id": "TRD-003",
                "calculation_id": "RISK-003",
                "highest_priority": "P1",
                "conflict_count": 1,
                "conflicts": [
                    {
                        "priority": "P1",
                        "event_type": "LOAN_REPAYMENT",
                        "event_date": "2026-10-08",
                        "gap_days": 0,
                        "reason": "예상 회수일과 상환일이 같습니다.",
                    }
                ],
            },
            {
                "rank": 2,
                "case_id": "TRD-002",
                "calculation_id": "RISK-002",
                "highest_priority": "P2",
                "conflict_count": 0,
                "conflicts": [],
            },
        ],
        "priority_summary": {
            "total_ranked": 2,
            "cases_with_conflicts": 1,
            "total_conflicts": 1,
            "by_highest_priority": {"P1": 1, "P2": 1},
            "ordered_case_ids": ["TRD-003", "TRD-002"],
        },
        "per_case_results": {
            "TRD-003": {
                "shipment_snapshot": {"case_id": "TRD-003", "status": "PLANNED"},
                "financial_risk_snapshot": {
                    "case_id": "TRD-003",
                    "calculation_id": "RISK-003",
                    "basis_version": "basis-3",
                    "input_fingerprint": "a" * 64,
                    "highest_priority": "P1",
                    "conflicts": [
                        {
                            "priority": "P1",
                            "event_type": "LOAN_REPAYMENT",
                            "event_date": "2026-10-08",
                            "gap_days": 0,
                            "reason": "예상 회수일과 상환일이 같습니다.",
                        }
                    ],
                    "data_actions": [],
                },
            },
            "TRD-002": {
                "shipment_snapshot": {"case_id": "TRD-002", "status": "UNKNOWN"},
                "financial_risk_snapshot": {
                    "case_id": "TRD-002",
                    "calculation_id": "RISK-002",
                    "basis_version": "basis-2",
                    "input_fingerprint": "b" * 64,
                    "highest_priority": "P2",
                    "conflicts": [],
                    "data_actions": [
                        {
                            "code": "PAYMENT_ANCHOR_REQUIRED",
                            "message": "지급기준일 확인이 필요합니다.",
                        }
                    ],
                },
            },
        },
    }


def test_daily_monitoring_report_is_idempotent_and_uses_persisted_run(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("app.services.reports.REPORT_DIR", tmp_path)
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        session.add(
            MonitoringRun(
                monitoring_run_id="MON-DAILY-001",
                as_of_date=date(2026, 9, 18),
                candidate_count=3,
                selected_count=2,
                result_count=2,
                new_alert_count=2,
                trace_json={"specialist_call_count": 4},
            )
        )
        session.flush()
        service = ReportService(session)

        first = service.generate_daily_monitoring_report(
            "MON-DAILY-001",
            _monitoring_payload(),
        )
        second = service.generate_daily_monitoring_report(
            "MON-DAILY-001",
            _monitoring_payload(),
        )

        assert first["deduplicated"] is False
        assert second["deduplicated"] is True
        assert first["daily_report_id"] == second["daily_report_id"]
        assert first["content_hash"] == second["content_hash"]
        assert first["summary"]["candidate_count"] == 3
        assert first["summary"]["selected_count"] == 2
        assert first["summary"]["risk_case_count"] == 2
        assert first["highest_priority"] == "P1"
        assert "후보 3건" in first["daily_summary"]
        assert session.scalar(select(func.count()).select_from(DailyMonitoringReport)) == 1

        report = session.get(DailyMonitoringReport, first["daily_report_id"])
        assert report is not None
        assert report.monitoring_run_id == "MON-DAILY-001"
        assert report.payload_hash == first["payload_hash"]
        assert report.dedup_key
        pdf_path = Path(first["pdf_path"])
        assert pdf_path.is_file()
        assert len(PdfReader(pdf_path).pages) >= 1


def test_daily_monitoring_payload_rejects_cross_run_data() -> None:
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        session.add(
            MonitoringRun(
                monitoring_run_id="MON-DAILY-001",
                as_of_date=date(2026, 9, 18),
                candidate_count=0,
                selected_count=0,
                result_count=0,
                new_alert_count=0,
                trace_json={},
            )
        )
        session.flush()
        service = ReportService(session)

        with pytest.raises(ValueError, match="another MonitoringRun"):
            service.build_daily_monitoring_payload(
                "MON-DAILY-001",
                {
                    "monitoring_run_id": "MON-OTHER",
                    "as_of_date": "2026-09-18",
                },
            )
        with pytest.raises(ValueError, match="as_of_date"):
            service.build_daily_monitoring_payload(
                "MON-DAILY-001",
                {
                    "monitoring_run_id": "MON-DAILY-001",
                    "as_of_date": "2026-09-19",
                },
            )
        with pytest.raises(KeyError):
            service.build_daily_monitoring_payload("MON-MISSING", {})


def test_monitoring_run_result_daily_fields_are_backward_compatible_defaults() -> None:
    result = MonitoringRunResult(
        monitoring_run_id="MON-DEFAULTS",
        as_of_date=date(2026, 9, 18),
        candidate_count=0,
        selected_count=0,
        specialist_call_count=0,
        new_alert_count=0,
    )

    assert result.ranked_risks == []
    assert result.priority_summary == {}
    assert result.daily_report is None
    assert result.daily_summary is None

from __future__ import annotations

from datetime import date
from typing import Any
from uuid import uuid4

from fastapi.testclient import TestClient

import app.api.main as api_main
from app.api.main import app
from app.db.models import Company, DailyMonitoringReport, MonitoringRun, TradeCase
from app.db.session import init_database, session_factory
from app.services.ingestion import batch_service

REQUIRED_PATHS = {
    "/api/graph/threads",
    "/api/files/upload",
    "/api/batches/{batch_id}/analyze",
    "/api/batches/{batch_id}/commit",
    "/api/batches/{batch_id}/field-overrides",
    "/api/chat",
    "/api/threads/{thread_id}/resume",
    "/api/cases/{case_id}",
    "/api/cases/{case_id}/delay-advisory",
    "/api/monitor/reports/latest",
    "/api/monitor/reports/{daily_report_id}/pdf",
    "/api/clock/advance",
    "/api/alerts",
    "/api/reports/{audience}/{case_id}",
    "/api/debug/runs/{request_id}",
}

REMOVED_PATHS = {
    "/api/demo/reset",
    "/api/demo/run/{demo_id}",
    "/api/cases/{case_id}/what-if",
    "/api/handoffs",
    "/api/monitor/run",
}


def test_api_surface_has_dynamic_routes_without_demo_or_what_if_routes() -> None:
    with TestClient(app) as client:
        health = client.get("/health")
        assert health.status_code == 200
        health_json = health.json()
        assert health_json["status"] == "ok"
        assert "OPENAI_API_KEY" not in str(health_json)

        summary = client.get("/api/openapi-summary")
        assert summary.status_code == 200
        paths = set(summary.json()["paths"])
        assert paths >= REQUIRED_PATHS
        assert paths.isdisjoint(REMOVED_PATHS)

        assert client.post("/api/demo/reset").status_code == 404
        assert client.post("/api/demo/run/1").status_code == 404
        assert client.post("/api/cases/TRD-003/what-if").status_code == 404
        assert client.post("/api/handoffs").status_code == 404
        assert client.post("/api/monitor/run").status_code == 404


def test_graph_thread_endpoint_is_truthful_in_offline_test_mode() -> None:
    with TestClient(app) as client:
        response = client.post("/api/graph/threads", json={"source": "pytest"})
        assert response.status_code == 200
        payload = response.json()
        assert payload["thread_id"].startswith("offline-")
        assert payload["connected"] is False
        assert payload["graph_id"] == "chat"
        assert payload["backend"] == "local"


def test_upload_rejects_mixed_document_and_financial_calendar_batch(
    tmp_path: Any,
    monkeypatch: Any,
) -> None:
    upload_root = tmp_path / "uploads"
    monkeypatch.setattr(api_main, "UPLOAD_ROOT", upload_root)
    monkeypatch.setattr(batch_service, "UPLOAD_ROOT", upload_root)

    with TestClient(app) as client:
        response = client.post(
            "/api/files/upload",
            params={"batch_id": "MIXED-BATCH"},
            files=[
                ("files", ("booking.pdf", b"pdf", "application/pdf")),
                (
                    "files",
                    (
                        "financial-calendar.xlsx",
                        b"xlsx",
                        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                    ),
                ),
            ],
        )

    assert response.status_code == 422
    assert "cannot mix" in response.json()["detail"]
    assert not (upload_root / "MIXED-BATCH").exists()

    with TestClient(app) as client:
        multiple_workbooks = client.post(
            "/api/files/upload",
            params={"batch_id": "MULTIPLE-CALENDAR-BATCH"},
            files=[
                (
                    "files",
                    (
                        "calendar-a.xlsx",
                        b"xlsx-a",
                        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                    ),
                ),
                (
                    "files",
                    (
                        "calendar-b.xlsx",
                        b"xlsx-b",
                        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                    ),
                ),
            ],
        )

    assert multiple_workbooks.status_code == 422
    assert "exactly one 2-sheet XLSX" in multiple_workbooks.json()["detail"]
    assert not (upload_root / "MULTIPLE-CALENDAR-BATCH").exists()


def test_latest_daily_report_metadata_and_pdf_download(
    tmp_path: Any,
    monkeypatch: Any,
) -> None:
    report_root = tmp_path / "daily-reports"
    report_root.mkdir()
    pdf_path = report_root / "daily-test.pdf"
    pdf_path.write_bytes(b"%PDF-1.4\n%%EOF\n")
    monkeypatch.setattr(api_main, "REPORT_DIR", report_root)

    suffix = uuid4().hex[:12]
    run_id = f"MON-API-{suffix}"
    report_id = f"DMR-API-{suffix}"
    with session_factory()() as session:
        session.add(
            MonitoringRun(
                monitoring_run_id=run_id,
                as_of_date=date(2099, 1, 1),
                candidate_count=3,
                selected_count=3,
                result_count=3,
                new_alert_count=1,
                trace_json={},
            )
        )
        session.add(
            DailyMonitoringReport(
                daily_report_id=report_id,
                monitoring_run_id=run_id,
                as_of_date=date(2099, 1, 1),
                summary_json={
                    "candidate_count": 3,
                    "summary_text": "일일 모니터링 API 테스트",
                },
                highest_priority="P1",
                risk_case_count=1,
                asset_path=str(pdf_path),
                content_hash="a" * 64,
                payload_hash="b" * 64,
                dedup_key=f"dedup-{suffix}",
            )
        )
        session.commit()

    with TestClient(app) as client:
        latest = client.get("/api/monitor/reports/latest")
        assert latest.status_code == 200
        payload = latest.json()["report"]
        assert payload["daily_report_id"] == report_id
        assert payload["risk_case_count"] == 1
        assert payload["pdf_url"].endswith(f"/{report_id}/pdf")

        download = client.get(payload["pdf_url"])
        assert download.status_code == 200
        assert download.headers["content-type"] == "application/pdf"
        assert download.content.startswith(b"%PDF")


def test_chat_supports_direct_conversation_and_structured_human_issue() -> None:
    with TestClient(app) as client:
        greeting = client.post(
            "/api/chat",
            json={
                "thread_id": f"api-greeting-{uuid4().hex}",
                "message": "안녕",
            },
        )
        assert greeting.status_code == 200
        assert greeting.json()["status"] == "SUCCESS"
        assert greeting.json()["answer"].startswith("안녕하세요!")

        thread_id = f"api-delay-human-{uuid4().hex}"
        requires_delay_days = client.post(
            "/api/chat",
            json={
                "thread_id": thread_id,
                "message": "TRD-003 B/L 지연 영향 확인",
                "reported_at": "2026-07-29",
            },
        )
        assert requires_delay_days.status_code == 200
        payload = requires_delay_days.json()
        assert payload["status"] == "HUMAN_REQUIRED"
        assert payload["thread_id"] == thread_id
        assert payload["confirmation_id"].startswith("CONF-")
        assert "question" not in payload
        assert payload["issue"] == {
            "issue_code": "DELAY_DAYS_REQUIRED",
            "prompt": ("검토할 지연 일수 목록을 정수 배열로 입력해 주세요 (예: [5, 12])."),
            "response_key": "delay_days",
            "value_type": "integer_list",
            "allowed_values": [],
            "cancel_values": [],
            "context": {},
        }

        legacy_resume = client.post(
            f"/api/threads/{thread_id}/resume",
            json={"answer": [9]},
        )
        assert legacy_resume.status_code == 422


def test_structured_resume_cancels_report_and_preserves_cited_product_results() -> None:
    init_database()
    case_id = "TRD-003"
    company_id = "DEMO-A"
    with session_factory()() as session:
        trade_case = session.get(TradeCase, case_id)
        if trade_case is None:
            if session.get(Company, company_id) is None:
                session.add(Company(company_id=company_id, legal_name="API Product Test Company"))
            session.add(
                TradeCase(
                    case_id=case_id,
                    company_id=company_id,
                    transaction_id=f"TXN-{case_id}",
                    company="API Product Test Company",
                    counterparty="Test Counterparty",
                    invoice_no=None,
                    currency="USD",
                    goods="Components",
                    status="AWAITING_DOCUMENT",
                    monitoring_enabled=False,
                    basis_version=f"basis-{case_id}",
                )
            )
        else:
            company_id = str(trade_case.company_id or company_id)
        session.commit()
    thread_id = f"api-product-consent-{uuid4().hex}"
    with TestClient(app) as client:
        advisory = client.post(
            "/api/chat",
            json={
                "thread_id": thread_id,
                "message": f"{case_id} 수입대금 유산스 상품 추천 브리핑",
                "company_id": company_id,
                "customer_role": "IMPORTER",
                "borrower_type": "CORPORATION",
                "known_facts": ["IMPORT_PAYMENT", "KSURE_PROGRAM_ELIGIBILITY"],
            },
        )
        assert advisory.status_code == 200
        advisory_payload = advisory.json()
        assert advisory_payload["status"] == "HUMAN_REQUIRED"
        issue = advisory_payload["issue"]
        assert issue["issue_code"] == "REPORT_GENERATION_CONSENT"
        assert issue["response_key"] == "report_consent"
        assert issue["value_type"] == "boolean"
        assert issue["allowed_values"] == [True, False]
        assert issue["cancel_values"] == [False]
        assert issue["context"] == {"case_id": case_id}

        resumed = client.post(
            f"/api/threads/{thread_id}/resume",
            json={
                "issue_code": issue["issue_code"],
                "value": False,
            },
        )
        assert resumed.status_code == 200
        resumed_payload = resumed.json()
        assert resumed_payload["status"] == "CANCELLED"
        assert resumed_payload["reason"] == "HUMAN_CANCEL_VALUE"
        assert resumed_payload["human_answers"][-1]["issue_code"] == issue["issue_code"]
        assert resumed_payload["human_answers"][-1]["value"] is False

        options = resumed_payload["results"]["retrieve_product_evidence"]["options"]
        assert options
        assert all(option["citations"] for option in options)
        assert all(
            citation["source_file"].lower().endswith(".pdf")
            and citation["page"] >= 1
            and citation["source_sha256"]
            for option in options
            for citation in option["citations"]
        )


def test_delay_advisory_uses_canonical_required_delay_fields() -> None:
    with TestClient(app) as client:
        missing_days = client.post(
            "/api/cases/TRD-NOT-FOUND/delay-advisory",
            json={"reported_at": "2026-07-29"},
        )
        assert missing_days.status_code == 422

        empty_days = client.post(
            "/api/cases/TRD-NOT-FOUND/delay-advisory",
            json={
                "reported_at": "2026-07-29",
                "expected_delay_days": [],
            },
        )
        assert empty_days.status_code == 422

        legacy_days = client.post(
            "/api/cases/TRD-NOT-FOUND/delay-advisory",
            json={
                "reported_at": "2026-07-29",
                "delay_days": [9],
            },
        )
        assert legacy_days.status_code == 422

        canonical_request = client.post(
            "/api/cases/TRD-NOT-FOUND/delay-advisory",
            json={
                "reported_at": "2026-07-29",
                "expected_delay_days": [9],
            },
        )
        assert canonical_request.status_code == 404
        assert canonical_request.json()["detail"] == "Case not found"

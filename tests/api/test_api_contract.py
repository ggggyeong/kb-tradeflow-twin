from pathlib import Path
from typing import Any

from fastapi.testclient import TestClient

from app.api import main as api
from app.schemas.portfolio import PortfolioRunResult

client = TestClient(api.app)


def test_health_exposes_supervised_workflow_and_low_cost_model() -> None:
    response = client.get("/health")

    assert response.status_code == 200
    assert response.json()["workflow"] == [
        "prepare",
        "supervisor",
        "tools",
        "finalize",
    ]
    assert response.json()["model"] == "gpt-5-nano"


def test_analyze_endpoint_returns_the_structured_pipeline_result(
    monkeypatch: Any,
    tmp_path: Path,
) -> None:
    expected = PortfolioRunResult.model_validate(
        {
            "status": "SUCCESS",
            "expected_receipt_date": "2026-09-20",
            "documents": [],
            "conflicts": [],
            "product_options": [],
            "report_path": str(tmp_path / "report.pdf"),
        }
    )
    monkeypatch.setattr(api.portfolio_graph, "invoke", lambda _: {})
    monkeypatch.setattr(api.pipeline, "result", lambda _: expected)

    response = client.post(
        "/api/portfolio/analyze",
        json={
            "document_paths": [],
            "financial_events": [
                {
                    "event_id": "PAY-1",
                    "scenario_code": "SUPPLIER_PAYMENT",
                    "event_name": "공급자 지급",
                    "event_date": "2026-09-15",
                }
            ],
            "expected_receipt_date": "2026-09-20",
        },
    )

    assert response.status_code == 200
    assert response.json()["status"] == "SUCCESS"
    assert response.json()["expected_receipt_date"] == "2026-09-20"


def test_api_rejects_arbitrary_paths_before_model_call(monkeypatch: Any, tmp_path: Path) -> None:
    def forbidden(_: Any) -> Any:
        raise AssertionError("must not invoke model")

    monkeypatch.setattr(api.portfolio_graph, "invoke", forbidden)
    response = client.post("/api/portfolio/analyze", json={"document_paths": ["/etc/passwd"]})
    assert response.status_code == 422
    response = client.post(
        "/api/portfolio/analyze", json={"report_output_path": str(tmp_path / "outside.pdf")}
    )
    assert response.status_code == 422


def test_request_defaults_do_not_confirm_trade_links() -> None:
    from app.schemas.portfolio import PortfolioRunRequest

    request = PortfolioRunRequest.model_validate(
        {
            "financial_events": [
                {"event_id": "a", "scenario_code": "SUPPLIER_PAYMENT", "event_name": "payment"}
            ]
        }
    )
    assert request.financial_events[0].link_status == "UNCONFIRMED"
    assert request.financial_events[0].event_date is None


def test_api_rejects_excel_outside_allowed_directories(monkeypatch: Any, tmp_path: Path) -> None:
    def forbidden(_: Any) -> Any:
        raise AssertionError("must not invoke model")

    monkeypatch.setattr(api.portfolio_graph, "invoke", forbidden)
    response = client.post(
        "/api/portfolio/analyze",
        json={"transaction_id": "x", "financial_calendar_path": "/etc/passwd"},
    )
    assert response.status_code == 422

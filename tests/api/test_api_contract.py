from pathlib import Path
from typing import Any

from fastapi.testclient import TestClient

from app.api import main as api
from app.schemas.portfolio import PortfolioRunResult

client = TestClient(api.app)


def test_health_exposes_only_the_small_portfolio_workflow() -> None:
    response = client.get("/health")

    assert response.status_code == 200
    assert response.json()["workflow"] == [
        "document_agent",
        "financial_conflict_agent",
        "product_advisor_agent",
        "report_generator",
    ]


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
            "document_paths": [str(tmp_path / "invoice.pdf")],
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

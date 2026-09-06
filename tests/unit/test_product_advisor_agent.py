from datetime import date
from typing import Any

from app.agents.product_advisor_agent import ProductAdvisorAgent
from app.schemas.portfolio import PortfolioConflictResult


class FakeStore:
    def __init__(self) -> None:
        self.calls: list[tuple[str, list[str], int]] = []

    def search(
        self,
        query: str,
        scenario_codes: list[str],
        *,
        allowed_product_ids: list[str] | None = None,
        top_k: int = 5,
    ) -> list[dict[str, Any]]:
        del allowed_product_ids
        self.calls.append((query, scenario_codes, top_k))
        return [
            {
                "product_id": "KB-PAYMENT-USANCE",
                "product_name": "KB Payment Usance",
                "scenario_code": "SUPPLIER_PAYMENT",
                "source_file": "payment-usance.pdf",
                "page": 1,
                "source_sha256": "a" * 64,
                "excerpt": "수입대금을 송금하고 만기에 결제하는 상품",
                "chunk_id": "chunk-1",
                "score": 0.8,
            }
        ]


def _conflict(status: str) -> PortfolioConflictResult:
    return PortfolioConflictResult.model_validate(
        {
            "event_id": "PAY-1",
            "scenario_code": "SUPPLIER_PAYMENT",
            "status": status,
            "event_name": "공급자 지급",
            "event_date": date(2026, 9, 15),
            "expected_receipt_date": date(2026, 9, 20),
            "gap_days": 5,
            "reason": "지급일이 대금 유입보다 먼저 도래합니다.",
        }
    )


def test_agent_retrieves_page_evidence_only_for_flagged_scenarios() -> None:
    store = FakeStore()
    options = ProductAdvisorAgent(store).run([_conflict("CONFLICT")])

    assert len(store.calls) == 1
    assert store.calls[0][1] == ["SUPPLIER_PAYMENT"]
    assert options[0].product_name == "KB Payment Usance"
    assert options[0].citations[0].page == 1
    assert options[0].citations[0].source_sha256 == "a" * 64


def test_agent_skips_rag_when_there_is_no_conflict() -> None:
    store = FakeStore()
    assert ProductAdvisorAgent(store).run([_conflict("NO_CONFLICT")]) == []
    assert store.calls == []

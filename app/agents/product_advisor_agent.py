from __future__ import annotations

from collections import defaultdict
from collections.abc import Sequence
from typing import Any, Protocol

from app.core.paths import PRODUCT_VECTOR_DIR
from app.schemas.portfolio import (
    FinancialScenarioCode,
    PortfolioCitation,
    PortfolioConflictResult,
    PortfolioProductOption,
)


class ProductSearchStore(Protocol):
    def search(
        self,
        query: str,
        scenario_codes: Sequence[str],
        *,
        allowed_product_ids: Sequence[str] | None = None,
        top_k: int = 5,
    ) -> list[dict[str, Any]]: ...


WHY_CONSIDER: dict[FinancialScenarioCode, str] = {
    "WORKING_CAPITAL_LOAN_MATURITY": (
        "대금 유입 전에 운전자금 만기가 도래하는 자금 공백의 대응 수단으로 검토합니다."
    ),
    "FX_FORWARD_MATURITY": (
        "대금 유입일과 선물환 결제일 사이의 외화 자금 공백 대응 수단으로 검토합니다."
    ),
    "SUPPLIER_PAYMENT": (
        "대금 유입 전에 공급자 지급일이 도래하는 결제 자금 공백 대응 수단으로 검토합니다."
    ),
}


class ProductAdvisorAgent:
    """Retrieve review options only for scenarios flagged by the conflict agent."""

    name = "product_advisor_agent"

    def __init__(
        self,
        store: ProductSearchStore | None = None,
        *,
        products_per_scenario: int = 2,
        citations_per_product: int = 2,
    ) -> None:
        self._store = store
        self.products_per_scenario = products_per_scenario
        self.citations_per_product = citations_per_product

    def run(
        self,
        conflicts: list[PortfolioConflictResult],
    ) -> list[PortfolioProductOption]:
        flagged = [item for item in conflicts if item.status != "NO_CONFLICT"]
        if not flagged:
            return []

        by_scenario: dict[FinancialScenarioCode, list[PortfolioConflictResult]] = defaultdict(list)
        for item in flagged:
            by_scenario[item.scenario_code].append(item)

        options: list[PortfolioProductOption] = []
        for scenario_code, scenario_conflicts in by_scenario.items():
            query = " ".join(
                [scenario_code, WHY_CONSIDER[scenario_code]]
                + [item.reason for item in scenario_conflicts]
            )
            hits = self._get_store().search(
                query,
                [scenario_code],
                top_k=max(6, self.products_per_scenario * self.citations_per_product * 2),
            )
            hits_by_product: dict[str, list[dict[str, Any]]] = defaultdict(list)
            product_order: list[str] = []
            for hit in hits:
                product_id = str(hit["product_id"])
                if product_id not in hits_by_product:
                    product_order.append(product_id)
                hits_by_product[product_id].append(hit)

            for product_id in product_order[: self.products_per_scenario]:
                product_hits = hits_by_product[product_id][: self.citations_per_product]
                if not product_hits:
                    continue
                options.append(
                    PortfolioProductOption(
                        product_id=product_id,
                        product_name=str(product_hits[0]["product_name"]),
                        scenario_code=scenario_code,
                        why_consider=WHY_CONSIDER[scenario_code],
                        citations=[
                            PortfolioCitation(
                                source_file=str(hit["source_file"]),
                                page=int(hit["page"]),
                                source_sha256=str(hit["source_sha256"]),
                                excerpt=str(hit["excerpt"]),
                                chunk_id=str(hit["chunk_id"]),
                            )
                            for hit in product_hits
                        ],
                    )
                )
        return options

    def _get_store(self) -> ProductSearchStore:
        if self._store is None:
            from app.services.product_vector_store import ChromaProductVectorStore

            self._store = ChromaProductVectorStore(persist_directory=PRODUCT_VECTOR_DIR)
        return self._store

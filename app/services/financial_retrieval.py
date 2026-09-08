from __future__ import annotations

from collections.abc import Sequence
from typing import Any, Protocol

from app.core.paths import PRODUCT_CATALOG_PATH, PRODUCT_PDF_DIR, PRODUCT_VECTOR_DIR
from app.schemas.portfolio import PortfolioConflictResult, PortfolioRunRequest
from app.services.product_catalog import ProductCatalog
from app.services.service_policy import POLICIES, TOPIC_QUERIES


class ProductSearchStore(Protocol):
    def search(
        self,
        query: str,
        scenario_codes: Sequence[str],
        *,
        allowed_product_ids: Sequence[str] | None = None,
        top_k: int = 5,
        topics: Sequence[str] | None = None,
    ) -> list[dict[str, Any]]: ...


class FinancialRetrieval:
    def __init__(
        self, *, store: ProductSearchStore | None = None, catalog: ProductCatalog | None = None
    ) -> None:
        self._store, self._catalog = store, catalog
        self._owns_store = store is None

    def search(
        self, request: PortfolioRunRequest, conflicts: list[PortfolioConflictResult]
    ) -> tuple[list[dict[str, Any]], list[str]]:
        catalog = self._catalog or ProductCatalog.load()
        events = {event.event_id: event for event in request.financial_events}
        evidence: list[dict[str, Any]] = []
        warnings: list[str] = []
        for conflict in conflicts:
            if conflict.status != "CONFLICT":
                continue
            policy = POLICIES[conflict.scenario_code]
            allowed = {
                p.product_id: p
                for p in catalog.eligible(request, events[conflict.event_id])
                if any(s.topic in policy.topics for s in p.sections)
            }
            if not allowed:
                warnings.append(
                    f"{conflict.event_id}: 거래 조건에 맞는 검토 완료 PDF가 없습니다. 자료 또는 거래 구분 확인이 필요합니다."
                )
                continue
            # Each query stays inside a reviewed topic, not merely a matching PDF.
            hits: list[dict[str, Any]] = []
            for product_id, candidate in allowed.items():
                topics = [t for t in policy.topics if any(s.topic == t for s in candidate.sections)]
                for topic in topics:
                    hits.extend(
                        hit
                        for hit in self._get_store().search(
                            TOPIC_QUERIES[topic],
                            [conflict.scenario_code],
                            allowed_product_ids=[product_id],
                            topics=[topic],
                            top_k=1,
                        )[:1]
                        if hit.get("product_id") == product_id and hit.get("topic") == topic
                    )
            accepted = 0
            seen: set[tuple[str, str]] = set()
            for hit in hits:
                product = allowed.get(str(hit.get("product_id")))
                if product is None or hit.get("scenario_code") != conflict.scenario_code:
                    continue
                if not any(
                    s.section_id == hit.get("section_id")
                    and s.topic == hit.get("topic")
                    and s.page == hit.get("page")
                    and s.topic in policy.topics
                    for s in product.sections
                ):
                    continue
                if (
                    hit.get("source_sha256") != product.reviewed_sha256
                    or hit.get("source_file") != product.source_file
                ):
                    continue
                excerpt = str(hit.get("excerpt", "")).strip()
                if (
                    not excerpt
                    or not hit.get("chunk_id")
                    or not isinstance(hit.get("page"), int)
                    or hit["page"] < 1
                ):
                    continue
                identity = (product.product_id, str(hit["chunk_id"]))
                if identity in seen:
                    continue
                seen.add(identity)
                # Full retrieved chunks, not the previous 360-character teaser.
                evidence.append(
                    {
                        **hit,
                        "product_name": product.canonical_name,
                        "financial_institution": product.bank_name,
                        "event_id": conflict.event_id,
                        "citation_id": f"ref-{len(evidence) + 1}",
                        "source_url": product.source_url,
                        "selection_reason": product.selection_reason,
                        "service_code": policy.code,
                        "service_title": policy.title,
                        "customer_need": policy.customer_need,
                    }
                )
                accepted += 1
            if not accepted:
                warnings.append(f"{conflict.event_id}: 검증 가능한 PDF 검색 근거가 없습니다.")
        return evidence, warnings

    def _get_store(self) -> ProductSearchStore:
        if self._store is None:
            from app.services.product_vector_store import ChromaProductVectorStore

            self._store = ChromaProductVectorStore(persist_directory=PRODUCT_VECTOR_DIR)
        if self._owns_store:
            self._store.validate_manifest(  # type: ignore[attr-defined]
                pdf_directory=PRODUCT_PDF_DIR, catalog_path=PRODUCT_CATALOG_PATH
            )
        return self._store

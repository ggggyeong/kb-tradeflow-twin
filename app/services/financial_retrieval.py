from __future__ import annotations

from collections.abc import Sequence
from typing import Any, Protocol

from app.core.paths import PRODUCT_CATALOG_PATH, PRODUCT_PDF_DIR, PRODUCT_VECTOR_DIR
from app.schemas.portfolio import PortfolioConflictResult, PortfolioRunRequest
from app.services.product_catalog import ProductCatalog, ProductRecord
from app.services.service_policy import POLICIES


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
        self.last_search_trace: list[dict[str, Any]] = []

    @staticmethod
    def _valid_hit(hit: dict[str, Any], product: ProductRecord, scenario: str) -> bool:
        """Check provenance, not whether a preselected answer topic was returned."""
        if (
            hit.get("scenario_code") != scenario
            or hit.get("source_sha256") != product.reviewed_sha256
            or hit.get("source_file") != product.source_file
            or not str(hit.get("excerpt", "")).strip()
            or not hit.get("chunk_id")
            or not isinstance(hit.get("page"), int)
            or hit["page"] < 1
        ):
            return False
        if product.index_mode == "pages":
            return hit["page"] in product.include_pages
        return any(
            s.section_id == hit.get("section_id")
            and s.topic == hit.get("topic")
            and s.page == hit["page"]
            for s in product.sections
        )

    def search(
        self, request: PortfolioRunRequest, conflicts: list[PortfolioConflictResult]
    ) -> tuple[list[dict[str, Any]], list[str]]:
        catalog = self._catalog or ProductCatalog.load()
        events = {event.event_id: event for event in request.financial_events}
        evidence: list[dict[str, Any]] = []
        warnings: list[str] = []
        self.last_search_trace = []
        store: ProductSearchStore | None = None
        for conflict in conflicts:
            if conflict.status != "CONFLICT":
                continue
            policy = POLICIES[conflict.scenario_code]
            allowed = {
                p.product_id: p
                for p in catalog.eligible(request, events[conflict.event_id])
                if (p.index_mode == "pages" and p.include_pages) or p.sections
            }
            if not allowed:
                warnings.append(
                    f"{conflict.event_id}: 거래 조건에 맞는 검토 완료 PDF가 없습니다. 자료 또는 거래 구분 확인이 필요합니다."
                )
                continue
            accepted = 0
            seen: dict[tuple[str, str], dict[str, Any]] = {}
            for question in policy.search_questions:
                # Coarse transaction-family gate only; no exact topic/page/answer filter.
                candidates = {
                    pid: product
                    for pid, product in allowed.items()
                    if product.requires_export_receivable == question.receivables_only
                }
                trace: dict[str, Any] = {
                    "event_id": conflict.event_id,
                    "scenario_code": conflict.scenario_code,
                    "query_id": question.query_id,
                    "query": question.text,
                    "allowed_source_ids": list(candidates),
                    "top_k": 3,
                    "page_filter": None,
                    "topic_filter": None,
                    "results": [],
                }
                self.last_search_trace.append(trace)
                if not candidates:
                    trace.update(
                        status="SKIPPED", reason="거래 조건에 맞는 자료 없음 또는 수출채권 미확인"
                    )
                    continue
                if store is None:
                    store = self._get_store()
                hits = store.search(
                    question.text,
                    [conflict.scenario_code],
                    allowed_product_ids=list(candidates),
                    top_k=3,
                )
                store_trace = getattr(store, "last_search_trace", {})
                if isinstance(store_trace, dict):
                    trace.update(
                        {
                            k: v
                            for k, v in store_trace.items()
                            if k in {"candidate_count", "unique_candidate_count", "where", "filter"}
                        }
                    )
                trace["status"] = "SEARCHED"
                for hit in hits[:3]:
                    product = candidates.get(str(hit.get("product_id")))
                    if product is None or not self._valid_hit(hit, product, conflict.scenario_code):
                        trace["rejected_hits"] = trace.get("rejected_hits", 0) + 1
                        continue
                    trace["results"].append(
                        {
                            "source_file": product.source_file,
                            "page": hit["page"],
                            "chunk_id": hit["chunk_id"],
                            "score": hit.get("score"),
                            "excerpt": hit["excerpt"],
                        }
                    )
                    identity = (product.product_id, str(hit["chunk_id"]))
                    if identity in seen:
                        previous = seen[identity]
                        if question.query_id not in previous["query_ids"]:
                            previous["query_ids"].append(question.query_id)
                            previous["retrieval_queries"].append(question.text)
                        continue
                    item = {
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
                        "query_ids": [question.query_id],
                        "retrieval_queries": [question.text],
                        "query_id": question.query_id,
                        "search_query": question.text,
                    }
                    seen[identity] = item
                    evidence.append(item)
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

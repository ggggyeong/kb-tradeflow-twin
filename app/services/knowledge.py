from __future__ import annotations

import hashlib
import json
import re
import unicodedata
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any

from langsmith import traceable
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import PROJECT_ROOT
from app.db.models import Shipment, TradeCase
from app.schemas.workflows import KnowledgeAnswer, ProductCitation

PRODUCT_PDF_DIR = PROJECT_ROOT / "kb_doc" / "KB 금융상품 pdf"
PRODUCT_INDEX_PATH = PROJECT_ROOT / "data" / "knowledge" / "product_ocr_index.json"
PRODUCT_INDEX_SCHEMA = "product-ocr-index-v1"

_TOKEN_PATTERN = re.compile(r"[0-9A-Za-z가-힣]+")
_STOP_WORDS = {
    "kb",
    "국민은행",
    "상품",
    "추천",
    "알려줘",
    "상담",
    "가능",
    "관련",
    "어떤",
    "대한",
}
_PRODUCT_NAMES = {
    "PAYMENT USANCE": "KB Payment Usance",
    "모아드림론": "KB 모아드림론",
    "특별출연": "KB 특별출연 수출입 금융지원",
    "사장님": "KB사장님+ 마이너스통장",
    "셀러론": "KB셀러론",
    "수출팩토링": "KB수출팩토링",
    "ONE KB": "ONE KB 기업 우대대출",
    "외화대출": "외화대출",
    "일반운전자금": "일반운전자금대출",
}


@dataclass(frozen=True)
class ProductPage:
    """Validated local OCR page used by deterministic lexical retrieval."""

    source_file: str
    source_sha256: str
    page: int
    text: str

    @property
    def evidence_id(self) -> str:
        return f"kb-product:{self.source_sha256}:p{self.page}"


def _normalized(value: str) -> str:
    return unicodedata.normalize("NFC", value)


def _hash_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


@lru_cache(maxsize=1)
def _product_pages() -> tuple[ProductPage, ...]:
    """Load the checked-in OCR cache and reject stale or untraceable pages."""
    payload = json.loads(PRODUCT_INDEX_PATH.read_text(encoding="utf-8"))
    if payload.get("schemaVersion") != PRODUCT_INDEX_SCHEMA:
        raise ValueError(f"Unsupported product index schema: {payload.get('schemaVersion')}")
    paths_by_name = {_normalized(path.name): path for path in PRODUCT_PDF_DIR.glob("*.pdf")}
    hash_cache: dict[Path, str] = {}
    pages: list[ProductPage] = []
    seen: set[tuple[str, int]] = set()
    for item in payload.get("pages", []):
        source_file = _normalized(str(item["sourceFile"]))
        source_path = paths_by_name.get(source_file)
        if source_path is None:
            raise FileNotFoundError(f"Indexed product PDF is missing: {source_file}")
        expected_hash = str(item["sourceSha256"])
        actual_hash = hash_cache.setdefault(source_path, _hash_file(source_path))
        if actual_hash != expected_hash:
            raise ValueError(
                f"Product index is stale for {source_file}: "
                f"expected {expected_hash}, got {actual_hash}"
            )
        page = int(item["page"])
        key = (source_file, page)
        text = str(item.get("text", "")).strip()
        if page < 1 or key in seen or not text:
            raise ValueError(f"Invalid product OCR page: {source_file} page {page}")
        seen.add(key)
        pages.append(
            ProductPage(
                source_file=source_file,
                source_sha256=actual_hash,
                page=page,
                text=text,
            )
        )
    if not pages:
        raise ValueError("Product OCR index contains no pages")
    return tuple(sorted(pages, key=lambda item: (item.source_file, item.page)))


def clear_product_index_cache() -> None:
    """Clear the validated index cache for tests or an operator rebuild."""
    _product_pages.cache_clear()


def _tokens(value: str) -> set[str]:
    return {
        token.casefold()
        for token in _TOKEN_PATTERN.findall(_normalized(value))
        if len(token) > 1 and token.casefold() not in _STOP_WORDS
    }


def _ngrams(value: str, size: int = 2) -> set[str]:
    compact = "".join(_TOKEN_PATTERN.findall(_normalized(value).casefold()))
    return {compact[index : index + size] for index in range(max(0, len(compact) - size + 1))}


def _score(query: str, page: ProductPage) -> float:
    query_tokens = _tokens(query)
    haystack = f"{page.source_file}\n{page.text}".casefold()
    token_score = sum(
        4.0 + min(haystack.count(token), 5) for token in query_tokens if token in haystack
    )
    query_grams = _ngrams(query)
    if not query_grams:
        return token_score
    overlap = len(query_grams & _ngrams(haystack))
    gram_score = 8.0 * overlap / len(query_grams)
    filename_score = 3.0 * sum(token in page.source_file.casefold() for token in query_tokens)
    return token_score + gram_score + filename_score


def _product_name(source_file: str) -> str:
    normalized = source_file.upper()
    for marker, name in _PRODUCT_NAMES.items():
        if marker.upper() in normalized:
            return name
    return Path(source_file).stem


def _excerpt(text: str, query: str, limit: int = 360) -> str:
    flattened = " ".join(text.split())
    tokens = sorted(_tokens(query), key=len, reverse=True)
    starts = [flattened.casefold().find(token) for token in tokens]
    starts = [start for start in starts if start >= 0]
    start = max(0, (min(starts) if starts else 0) - 80)
    excerpt = flattened[start : start + limit]
    if start:
        excerpt = f"…{excerpt}"
    if start + limit < len(flattened):
        excerpt = f"{excerpt}…"
    return excerpt


def search_product_pages(
    query: str,
    top_k: int = 5,
    *,
    source_files: set[str] | None = None,
    pages_by_source: dict[str, set[int]] | None = None,
) -> list[dict[str, Any]]:
    """Return one best citation per product using local lexical retrieval."""
    if not query.strip():
        raise ValueError("query must not be empty")
    if not 1 <= top_k <= 10:
        raise ValueError("top_k must be between 1 and 10")
    normalized_sources = {_normalized(source) for source in source_files} if source_files else None
    normalized_page_filter = {
        _normalized(source): pages for source, pages in (pages_by_source or {}).items()
    }
    eligible_pages = [
        page
        for page in _product_pages()
        if normalized_sources is None or page.source_file in normalized_sources
        if (
            not normalized_page_filter
            or page.source_file not in normalized_page_filter
            or page.page in normalized_page_filter[page.source_file]
        )
    ]
    ranked = sorted(
        ((_score(query, page), page) for page in eligible_pages),
        key=lambda item: (-item[0], item[1].source_file, item[1].page),
    )
    best_by_source: dict[str, tuple[float, ProductPage]] = {}
    for score, page in ranked:
        if score <= 0:
            continue
        best_by_source.setdefault(page.source_file, (score, page))
    matches = sorted(
        best_by_source.values(),
        key=lambda item: (-item[0], item[1].source_file),
    )[:top_k]
    return [
        {
            "product_name": _product_name(page.source_file),
            "score": round(score, 4),
            "source_file": page.source_file,
            "page": page.page,
            "source_sha256": page.source_sha256,
            "excerpt": _excerpt(page.text, query),
            "evidence_id": page.evidence_id,
        }
        for score, page in matches
    ]


class KnowledgeService:
    """Answer bounded knowledge/case questions and persist immutable scenarios."""

    def __init__(self, session: Session) -> None:
        self.session = session

    @traceable(name="kb_only_qa", run_type="retriever")
    def answer_general(self, query: str, source_scope: str = "KB_ONLY") -> KnowledgeAnswer:
        if source_scope != "KB_ONLY":
            raise ValueError("General KB QA is restricted to KB_ONLY")
        matches = search_product_pages(query)
        candidates = [
            {
                "product_name": item["product_name"],
                "evidence_id": item["evidence_id"],
            }
            for item in matches
        ]
        names = ", ".join(item["product_name"] for item in matches[:3])
        answer = (
            f"로컬 KB 상품 PDF에서 관련 근거를 찾았습니다: {names}. "
            "이는 상담 후보이며 실제 이용 가능 여부, 한도, 금리와 승인은 "
            "KB 직원의 최신 심사·약관 확인이 필요합니다."
            if matches
            else (
                "로컬 KB 상품 PDF에서 직접 관련된 근거를 찾지 못했습니다. "
                "상품 적격성은 KB 직원에게 확인해 주세요."
            )
        )
        return KnowledgeAnswer(
            answer=answer,
            source_scope="KB_ONLY",
            source_status="LOCAL_OCR_INDEX",
            evidence_ids=[str(item["evidence_id"]) for item in matches],
            used_case_db=False,
            used_web=False,
            citations=[
                ProductCitation(
                    source_file=str(item["source_file"]),
                    page=int(item["page"]),
                    source_sha256=str(item["source_sha256"]),
                    excerpt=str(item["excerpt"]),
                )
                for item in matches
            ],
            candidates=candidates,
        )

    @traceable(name="case_qa", run_type="chain")
    def answer_case(self, case_id: str) -> KnowledgeAnswer:
        trade_case = self.session.get(TradeCase, case_id)
        if trade_case is None:
            raise KeyError(case_id)
        shipment = self.session.scalar(select(Shipment).where(Shipment.case_id == case_id))
        if shipment is None:
            shipment_text = "선적 레코드 상태는 UNKNOWN입니다."
        else:
            departure_status = (
                "CONFIRMED_DEPARTED" if shipment.on_board_date is not None else "UNKNOWN"
            )
            shipment_text = (
                f"선적 lifecycle 상태는 {shipment.status}이며, "
                f"실제 출항 확인 상태는 {departure_status}입니다."
            )
        return KnowledgeAnswer(
            answer=(
                f"{case_id}는 {trade_case.status} 상태입니다. {shipment_text} "
                "B/L 부재는 미출항의 증거가 아닙니다."
            ),
            source_scope="CASE_DB",
            source_status="VERIFIED_DOMAIN_FACTS",
            evidence_ids=[f"trade_case:{case_id}", f"shipment:{case_id}"],
            used_case_db=True,
        )

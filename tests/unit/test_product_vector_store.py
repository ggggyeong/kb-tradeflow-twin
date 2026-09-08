from __future__ import annotations

import hashlib
import json
import shutil
import sys
import unicodedata
from collections.abc import Sequence
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from pypdf import PdfReader

from app.services.product_vector_store import (
    DEFAULT_PORTFOLIO_PRODUCT_IDS,
    SCENARIO_CODES,
    ChromaProductVectorStore,
    MultilingualE5Embedding,
    ProductVectorError,
    build_index_from_current_assets,
    load_product_vector_corpus,
    split_product_page,
)

PROJECT_ROOT = Path(__file__).resolve().parents[2]
PDF_DIRECTORY = PROJECT_ROOT / "kb_doc" / "KB 금융상품 pdf"
CATALOG_PATH = PROJECT_ROOT / "data" / "knowledge" / "product_catalog.json"


@pytest.fixture(autouse=True)
def reviewed_test_catalog(tmp_path: Path, monkeypatch: Any) -> None:
    # Test-only approval of fixture files, never changes the production review state.
    data = json.loads(CATALOG_PATH.read_text())
    paths = {unicodedata.normalize("NFC", p.name): p for p in PDF_DIRECTORY.glob("*.pdf")}
    for item in data["products"]:
        item["enabled"] = False
        if item["product_id"] in DEFAULT_PORTFOLIO_PRODUCT_IDS:
            item.update(
                enabled=True,
                review_status="REVIEWED",
                reviewed_on="2026-09-06",
                source_url="https://example.com/test-only",
                reviewed_sha256=hashlib.sha256(paths[item["source_file"]].read_bytes()).hexdigest(),
            )
    target = tmp_path / "approved-test-catalog.json"
    target.write_text(json.dumps(data, ensure_ascii=False))
    monkeypatch.setattr(sys.modules[__name__], "CATALOG_PATH", target)


def fake_pdf_ocr(path: Path) -> list[str]:
    name = unicodedata.normalize("NFC", path.name)
    return [
        f"{name} {page}페이지 금융상품 대상 한도 만기 상환 조건 안내"
        for page in range(1, len(PdfReader(path).pages) + 1)
    ]


class FakeEmbedding:
    model_name = "fake-e5"
    dimension = 3

    def embed_documents(self, texts: Sequence[str]) -> list[list[float]]:
        return [[float(len(text) % 7), 1.0, 0.0] for text in texts]

    def embed_query(self, text: str) -> list[float]:
        return [1.0, 1.0, 0.0]


class FakeCollection:
    def __init__(self) -> None:
        self.records: dict[str, dict[str, Any]] = {}
        self.last_query: dict[str, Any] | None = None

    def get(self, **kwargs: Any) -> dict[str, Any]:
        selected = [
            (chunk_id, record)
            for chunk_id, record in self.records.items()
            if "where" not in kwargs or _matches(record["metadata"], kwargs["where"])
        ]
        return {
            "ids": [chunk_id for chunk_id, _ in selected],
            "metadatas": [record["metadata"] for _, record in selected],
        }

    def delete(self, **kwargs: Any) -> None:
        for chunk_id in kwargs["ids"]:
            self.records.pop(chunk_id, None)

    def upsert(self, **kwargs: Any) -> None:
        for chunk_id, document, metadata in zip(
            kwargs["ids"], kwargs["documents"], kwargs["metadatas"], strict=True
        ):
            self.records[chunk_id] = {"document": document, "metadata": metadata}

    def query(self, **kwargs: Any) -> dict[str, Any]:
        self.last_query = kwargs
        eligible = [
            (chunk_id, record)
            for chunk_id, record in self.records.items()
            if _matches(record["metadata"], kwargs["where"])
        ][: kwargs["n_results"]]
        return {
            "ids": [[chunk_id for chunk_id, _ in eligible]],
            "documents": [[record["document"] for _, record in eligible]],
            "metadatas": [[record["metadata"] for _, record in eligible]],
            "distances": [[0.1 + index * 0.01 for index, _ in enumerate(eligible)]],
        }

    def count(self) -> int:
        return len(self.records)


def _matches(metadata: dict[str, Any], where: dict[str, Any]) -> bool:
    if "$and" in where:
        return all(_matches(metadata, clause) for clause in where["$and"])
    return all(
        metadata.get(key) in expected["$in"]
        if isinstance(expected, dict)
        else metadata.get(key) == expected
        for key, expected in where.items()
    )


class FakeSentenceModel:
    def __init__(self) -> None:
        self.calls: list[list[str]] = []

    def get_sentence_embedding_dimension(self) -> int:
        return 3

    def encode(self, texts: list[str], **kwargs: Any) -> list[list[float]]:
        self.calls.append(texts)
        return [[1.0, 0.0, 0.0] for _ in texts]


def test_pdf_corpus_uses_reviewed_products_and_stable_citations() -> None:
    first = load_product_vector_corpus(
        pdf_directory=PDF_DIRECTORY,
        catalog_path=CATALOG_PATH,
        ocr_backend=fake_pdf_ocr,
    )
    second = load_product_vector_corpus(
        pdf_directory=PDF_DIRECTORY,
        catalog_path=CATALOG_PATH,
        ocr_backend=fake_pdf_ocr,
    )
    subset = load_product_vector_corpus(
        pdf_directory=PDF_DIRECTORY,
        catalog_path=CATALOG_PATH,
        ocr_backend=fake_pdf_ocr,
        include_product_ids=["KB-PAYMENT-USANCE"],
    )

    assert set(first.product_ids) == set(DEFAULT_PORTFOLIO_PRODUCT_IDS)
    assert len(first.source_hashes) == 3
    assert len(first.chunks) == 24
    assert first.corpus_fingerprint == second.corpus_fingerprint
    assert [chunk.chunk_id for chunk in first.chunks] == [chunk.chunk_id for chunk in second.chunks]
    assert all(chunk.metadata["scenario_code"] in SCENARIO_CODES for chunk in first.chunks)
    assert all(int(chunk.metadata["page"]) >= 1 for chunk in first.chunks)
    assert all(len(str(chunk.metadata["source_sha256"])) == 64 for chunk in first.chunks)
    assert len(subset.product_ids) == 1
    assert len(subset.chunks) == 2


def test_multilingual_e5_uses_query_and_passage_prefixes() -> None:
    model = FakeSentenceModel()
    embedding = MultilingualE5Embedding("fake-e5", dimension=3, model=model)

    embedding.embed_documents(["상품 안내", "외화 대출"])
    embedding.embed_query("선물환 만기")

    assert model.calls == [
        ["passage: 상품 안내", "passage: 외화 대출"],
        ["query: 선물환 만기"],
    ]


def test_build_replaces_stale_data_and_writes_manifest(tmp_path: Path) -> None:
    collection = FakeCollection()
    collection.records["stale"] = {"document": "old", "metadata": {}}
    manifest = build_index_from_current_assets(
        project_root=PROJECT_ROOT,
        pdf_directory=PDF_DIRECTORY,
        catalog_path=CATALOG_PATH,
        persist_directory=tmp_path,
        ocr_backend=fake_pdf_ocr,
        embedding_provider=FakeEmbedding(),
        collection=collection,
    )

    assert "stale" not in collection.records
    assert manifest["product_count"] == 3
    assert manifest["record_count"] == 24
    assert collection.count() == 24
    assert (tmp_path / "product_vector_manifest.json").is_file()


def test_search_filters_products_and_returns_page_evidence(tmp_path: Path) -> None:
    collection = FakeCollection()
    build_index_from_current_assets(
        project_root=PROJECT_ROOT,
        pdf_directory=PDF_DIRECTORY,
        catalog_path=CATALOG_PATH,
        persist_directory=tmp_path,
        ocr_backend=fake_pdf_ocr,
        embedding_provider=FakeEmbedding(),
        collection=collection,
    )
    store = ChromaProductVectorStore(
        persist_directory=tmp_path,
        embedding_provider=FakeEmbedding(),
        collection=collection,
    )
    results = store.search(
        "수입대금 지급",
        ["SUPPLIER_PAYMENT"],
        allowed_product_ids=["KB-PAYMENT-USANCE"],
        top_k=2,
    )

    assert results
    assert collection.last_query is not None
    assert collection.last_query["where"] == {
        "$and": [
            {"scenario_code": "SUPPLIER_PAYMENT"},
            {"product_id": "KB-PAYMENT-USANCE"},
        ]
    }
    assert all(result["product_id"] == "KB-PAYMENT-USANCE" for result in results)
    assert all(result["page"] >= 1 and len(result["source_sha256"]) == 64 for result in results)
    assert set(results[0]) == {
        "product_id",
        "product_name",
        "scenario_code",
        "source_file",
        "page",
        "source_sha256",
        "excerpt",
        "chunk_id",
        "score",
        "topic",
        "section_id",
    }


def test_search_rejects_empty_product_filter_and_invalid_top_k(tmp_path: Path) -> None:
    store = ChromaProductVectorStore(
        persist_directory=tmp_path,
        embedding_provider=FakeEmbedding(),
        collection=FakeCollection(),
        validate_manifest=False,
    )

    with pytest.raises(ValueError, match="allowed_product_ids"):
        store.search("운전자금", ["SUPPLIER_PAYMENT"], allowed_product_ids=[])
    with pytest.raises(ValueError, match="top_k"):
        store.search("운전자금", ["SUPPLIER_PAYMENT"], top_k=0)


def test_manifest_detects_changed_source_pdf(tmp_path: Path) -> None:
    pdf_copy = tmp_path / "pdfs"
    pdf_copy.mkdir()
    for source in PDF_DIRECTORY.glob("*.pdf"):
        shutil.copy2(source, pdf_copy / source.name)
    catalog_copy = tmp_path / "product_catalog.json"
    shutil.copy2(CATALOG_PATH, catalog_copy)
    index_dir, collection = tmp_path / "index", FakeCollection()
    manifest = build_index_from_current_assets(
        pdf_directory=pdf_copy,
        catalog_path=catalog_copy,
        persist_directory=index_dir,
        ocr_backend=fake_pdf_ocr,
        embedding_provider=FakeEmbedding(),
        collection=collection,
    )

    changed_source = next(iter(manifest["source_hashes"]))
    changed = next(
        path
        for path in pdf_copy.glob("*.pdf")
        if unicodedata.normalize("NFC", path.name) == changed_source
    )
    changed.write_bytes(changed.read_bytes() + b"\n")
    store = ChromaProductVectorStore(
        persist_directory=index_dir,
        embedding_provider=FakeEmbedding(),
        collection=collection,
        validate_manifest=False,
    )
    try:
        store.validate_manifest(pdf_directory=pdf_copy, catalog_path=catalog_copy)
    except ProductVectorError as exc:
        assert "source_hash:" in str(exc)
    else:
        raise AssertionError("Changed source PDF must invalidate the vector manifest")


def test_archived_product_cannot_be_forced_into_index() -> None:
    with pytest.raises(ProductVectorError, match="reviewed and enabled"):
        load_product_vector_corpus(
            pdf_directory=PDF_DIRECTORY,
            catalog_path=CATALOG_PATH,
            ocr_backend=fake_pdf_ocr,
            include_product_ids=["KB-SELLER-LOAN"],
        )


def test_embedding_failure_preserves_existing_records(tmp_path: Path) -> None:
    from app.services.product_vector_store import ProductChunk

    class BrokenEmbedding(FakeEmbedding):
        def embed_documents(self, texts: Sequence[str]) -> list[list[float]]:
            raise RuntimeError("embedding failed")

    collection = FakeCollection()
    collection.records["old"] = {"document": "old", "metadata": {}}
    store = ChromaProductVectorStore(
        persist_directory=tmp_path,
        collection=collection,
        embedding_provider=BrokenEmbedding(),
        validate_manifest=False,
    )
    with pytest.raises(RuntimeError):
        store.replace([ProductChunk(chunk_id="new", document="new", metadata={})])
    assert set(collection.records) == {"old"}


def test_reading_absent_index_does_not_create_empty_database(tmp_path: Path) -> None:
    target = tmp_path / "absent-index"
    with pytest.raises(ProductVectorError, match="not built"):
        ChromaProductVectorStore(persist_directory=target)
    assert not target.exists()


def test_page_chunks_preserve_sentences_and_token_budget() -> None:
    sentences = [
        "대출 만기 연장은 심사가 필요합니다.",
        "심사 결과에 따라 연장이 거절될 수 있습니다.",
        "이자를 납부하지 않으면 연체이자가 발생합니다.",
        "기존 계약 조건은 거래 은행에 확인해야 합니다.",
    ]

    # Simulates a tokenizer that needs more than one token per character.
    def counter(text: str) -> int:
        return len(text) * 2 + 4

    chunks = split_product_page(
        "\n\n".join(sentences),
        chunk_size=200,
        chunk_overlap=0,
        token_counter=counter,
        max_tokens=110,
    )
    assert len(chunks) > 1
    assert all(counter(chunk) <= 110 for chunk in chunks)
    assert all(any(sentence in chunk for chunk in chunks) for sentence in sentences)
    assert all(chunk.endswith(".") for chunk in chunks)


def test_oversized_single_clause_is_not_silently_dropped() -> None:
    distinct = " ".join(f"조건{number:03d}" for number in range(100))
    chunks = split_product_page(distinct, chunk_size=100, chunk_overlap=0)
    assert " ".join(chunks) == distinct


def test_structural_headings_keep_body_but_do_not_merge_unrelated_sections() -> None:
    text = (
        "상품 설명서\n"
        "■ 신용에 미치는 영향\n• 대출계약 체결 시 신용평점에 영향이 있습니다.\n"
        "■ 유지에 필요한 서류\n• 재무제표와 매출 관련 자료를 요구할 수 있습니다.\n"
        "▣ 계약기간 및 연장\n• 심사 결과에 따라 연장이 거절될 수 있습니다."
    )
    chunks = split_product_page(text, chunk_size=800)
    assert len(chunks) == 4
    documents_chunk = next(chunk for chunk in chunks if "재무제표" in chunk)
    assert "유지에 필요한 서류" in documents_chunk
    assert "신용평점" not in documents_chunk
    assert "연장" not in documents_chunk


def test_legal_clause_headings_remain_separate() -> None:
    text = "1조 정의\n이 약정에서 정하는 용어입니다.\n2조 매입 조건\n은행 수락이 필요합니다."
    chunks = split_product_page(text, chunk_size=800)
    assert len(chunks) == 2
    assert chunks[0].startswith("1조 정의")
    assert chunks[1].startswith("2조 매입 조건")


def test_pdf_blank_lines_do_not_detach_conditional_list_from_its_clause() -> None:
    text = (
        "3조 환매 의무\n\n"
        "3.01 다음 경우에는 고객이 은행의 요청에 따라 채권을 환매해야 합니다.\n\n"
        "(1) 채무자가 유예기간 최종일까지 채권 결제를 하지 않은 경우\n\n"
        "(2) 계약 이행이 불법이 된 경우\n\n"
        "3.02 환매가는 별도 약정으로 계산합니다."
    )
    chunks = split_product_page(text)
    conditions = next(chunk for chunk in chunks if "유예기간" in chunk)
    assert "3.01" in conditions and "환매해야" in conditions
    assert "(2)" in conditions
    assert "3.02" not in conditions


def test_pdf_document_list_keeps_submission_context() -> None:
    text = (
        "고객은 다음 서류를 은행에 제출합니다.\n\n"
        "(a) 정관과 법인등기부등본\n\n"
        "(b) 상업송장과 선하증권 원본\n\n"
        "(c) 이미 제출한 경우 제외되는 자료"
    )
    chunks = split_product_page(text)
    assert len(chunks) == 1
    assert "은행에 제출" in chunks[0] and "선하증권" in chunks[0]


def test_page_mode_indexes_candidate_passages_not_reviewed_answer_spans(
    tmp_path: Path,
    monkeypatch: Any,
) -> None:
    source = tmp_path / "whole-page.pdf"
    source.write_bytes(b"test-pdf-placeholder")
    sentences = [
        "Repayment is due at maturity. " * 8,
        "Overdue principal attracts default interest. " * 8,
        "Extension requires a review and can be declined. " * 8,
    ]
    page_text = "\n\n".join(sentences)
    monkeypatch.setattr(
        "pypdf.PdfReader",
        lambda path: SimpleNamespace(pages=[SimpleNamespace(extract_text=lambda: page_text)]),
    )
    catalog = tmp_path / "page-catalog.json"
    catalog.write_text(
        json.dumps(
            {
                "schema_version": "kb-product-catalog-v3",
                "products": [
                    {
                        "product_id": "LOAN-GUIDE",
                        "canonical_name": "Loan guide",
                        "bank_name": "Test bank",
                        "source_file": source.name,
                        "source_url": "https://example.com/loan.pdf",
                        "reviewed_on": "2026-09-08",
                        "reviewed_sha256": hashlib.sha256(source.read_bytes()).hexdigest(),
                        "review_status": "REVIEWED",
                        "enabled": True,
                        "selection_reason": "Whole reviewed page",
                        "scenario_codes": ["WORKING_CAPITAL_LOAN_MATURITY"],
                        "trade_directions": ["EXPORT"],
                        "index_mode": "pages",
                        "include_pages": [1],
                        "sections": [],
                    }
                ],
            }
        )
    )
    collection = FakeCollection()
    manifest = build_index_from_current_assets(
        pdf_directory=tmp_path,
        catalog_path=catalog,
        persist_directory=tmp_path / "index",
        embedding_provider=FakeEmbedding(),
        collection=collection,
        chunk_size=250,
        chunk_overlap=50,
    )
    assert manifest["unique_chunk_count"] > 1
    store = ChromaProductVectorStore(
        persist_directory=tmp_path / "index",
        collection=collection,
        embedding_provider=FakeEmbedding(),
    )
    result = store.search(
        "extension conditions",
        ["WORKING_CAPITAL_LOAN_MATURITY"],
        allowed_product_ids=["LOAN-GUIDE"],
        top_k=2,
    )
    assert len(result) == 2
    assert store.last_search_trace["unique_candidate_count"] > len(result)
    assert "topic" not in json.dumps(store.last_search_trace["where"])
    assert all(item["topic"] == "GENERAL" for item in result)
    assert any("Overdue" in record["document"] for record in collection.records.values())
    assert any("Extension" in record["document"] for record in collection.records.values())


def test_real_tokenizer_budget_is_checked_before_encode() -> None:
    model = FakeSentenceModel()
    model.tokenizer = SimpleNamespace(encode=lambda text, **kwargs: list(text))
    model.max_seq_length = 32
    embedding = MultilingualE5Embedding("fake-e5", dimension=3, model=model)
    assert embedding.count_tokens("본문") == len("passage: 본문")
    with pytest.raises(ProductVectorError, match="token limit"):
        embedding.embed_documents(["본문" * 30])
    assert not model.calls


def test_page_mode_rejects_answer_anchor_configuration() -> None:
    from pydantic import ValidationError

    from app.services.product_catalog import ProductRecord

    with pytest.raises(ValidationError, match="answer-span"):
        ProductRecord.model_validate(
            {
                "product_id": "guide",
                "canonical_name": "Guide",
                "bank_name": "Bank",
                "source_file": "guide.pdf",
                "selection_reason": "Test",
                "scenario_codes": [],
                "trade_directions": [],
                "index_mode": "pages",
                "include_pages": [1],
                "sections": [
                    {
                        "section_id": "fixed",
                        "topic": "fixed",
                        "page": 1,
                        "start_anchor": "start",
                        "end_anchor": "end",
                    }
                ],
            }
        )


def test_fresh_rebuild_preserves_active_collection_and_manifest_until_publication(
    tmp_path: Path,
) -> None:
    from app.services.product_vector_store import ProductChunk

    class FakeClient:
        def __init__(self) -> None:
            self.collections = {"active-old": FakeCollection()}
            self.collections["active-old"].records["old"] = {
                "document": "previous evidence",
                "metadata": {"product_id": "guide"},
            }

        def get_or_create_collection(self, *, name: str, **kwargs: Any) -> FakeCollection:
            return self.collections.setdefault(name, FakeCollection())

        def create_collection(self, *, name: str, **kwargs: Any) -> FakeCollection:
            assert name not in self.collections
            self.collections[name] = FakeCollection()
            return self.collections[name]

    client = FakeClient()
    original_manifest = json.dumps({"collection_name": "active-old"})
    manifest_path = tmp_path / "product_vector_manifest.json"
    manifest_path.write_text(original_manifest)
    store = ChromaProductVectorStore(
        persist_directory=tmp_path,
        client=client,
        collection_name="active-old",
        embedding_provider=FakeEmbedding(),
        validate_manifest=False,
    )
    store.replace(
        [ProductChunk("new", "new evidence", {"product_id": "guide"})],
        fresh_collection=True,
    )
    assert store.collection_name != "active-old"
    assert store.previous_collection_name == "active-old"
    assert "old" in client.collections["active-old"].records
    assert "new" in store.collection.records
    assert manifest_path.read_text() == original_manifest


def test_fresh_ann_lookup_failure_keeps_previous_manifest(tmp_path: Path) -> None:
    from app.services.product_vector_store import ProductChunk

    class UnsearchableCollection(FakeCollection):
        def query(self, **kwargs: Any) -> dict[str, Any]:
            return {"documents": [[]]}

    class Client:
        def get_or_create_collection(self, **kwargs: Any) -> FakeCollection:
            return FakeCollection()

        def create_collection(self, **kwargs: Any) -> FakeCollection:
            return UnsearchableCollection()

    manifest_path = tmp_path / "product_vector_manifest.json"
    original_manifest = json.dumps({"collection_name": "previous"})
    manifest_path.write_text(original_manifest)
    store = ChromaProductVectorStore(
        persist_directory=tmp_path,
        client=Client(),
        collection_name="previous",
        embedding_provider=FakeEmbedding(),
        validate_manifest=False,
    )
    with pytest.raises(ProductVectorError, match="self-retrieval"):
        store.replace(
            [ProductChunk("new", "valid row but absent from ANN", {"product_id": "guide"})],
            fresh_collection=True,
        )
    assert manifest_path.read_text() == original_manifest

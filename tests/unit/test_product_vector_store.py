from __future__ import annotations

import shutil
import unicodedata
from collections.abc import Sequence
from pathlib import Path
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
)

PROJECT_ROOT = Path(__file__).resolve().parents[2]
PDF_DIRECTORY = PROJECT_ROOT / "kb_doc" / "KB 금융상품 pdf"
CATALOG_PATH = PROJECT_ROOT / "data" / "knowledge" / "product_catalog.json"


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
        return {"ids": list(self.records)}

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


def test_pdf_corpus_defaults_to_seven_products_and_has_stable_citations() -> None:
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
    expanded = load_product_vector_corpus(
        pdf_directory=PDF_DIRECTORY,
        catalog_path=CATALOG_PATH,
        ocr_backend=fake_pdf_ocr,
        include_product_ids=["KB-SELLER-LOAN", "KB-OWNER-OVERDRAFT"],
    )

    assert first.product_ids == DEFAULT_PORTFOLIO_PRODUCT_IDS
    assert len(first.source_hashes) == 7
    assert len(first.chunks) == 55
    assert first.corpus_fingerprint == second.corpus_fingerprint
    assert [chunk.chunk_id for chunk in first.chunks] == [
        chunk.chunk_id for chunk in second.chunks
    ]
    assert all(chunk.metadata["scenario_code"] in SCENARIO_CODES for chunk in first.chunks)
    assert all(int(chunk.metadata["page"]) >= 1 for chunk in first.chunks)
    assert all(len(str(chunk.metadata["source_sha256"])) == 64 for chunk in first.chunks)
    assert len(expanded.product_ids) == 9
    assert len(expanded.source_hashes) == 9
    assert len(expanded.chunks) == 70


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
        persist_directory=tmp_path,
        ocr_backend=fake_pdf_ocr,
        embedding_provider=FakeEmbedding(),
        collection=collection,
    )

    assert "stale" not in collection.records
    assert manifest["product_count"] == 7
    assert manifest["record_count"] == 55
    assert collection.count() == 55
    assert (tmp_path / "product_vector_manifest.json").is_file()


def test_search_filters_products_and_returns_page_evidence(tmp_path: Path) -> None:
    collection = FakeCollection()
    build_index_from_current_assets(
        project_root=PROJECT_ROOT,
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

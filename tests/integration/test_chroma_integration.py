from collections.abc import Sequence
from pathlib import Path

from app.services.product_vector_store import (
    ChromaProductVectorStore,
    ProductChunk,
)


class TinyEmbedding:
    model_name = "tiny-test"
    dimension = 3

    def embed_documents(self, texts: Sequence[str]) -> list[list[float]]:
        return [[1.0, float(len(text) % 7), 0.5] for text in texts]

    def embed_query(self, text: str) -> list[float]:
        return [1.0, float(len(text) % 7), 0.5]


def _chunk(chunk_id: str, scenario_code: str, product_id: str) -> ProductChunk:
    return ProductChunk(
        chunk_id=chunk_id,
        document="수입대금과 운전자금 관련 금융상품 원문",
        metadata={
            "product_id": product_id,
            "product_name": product_id,
            "scenario_code": scenario_code,
            "source_file": f"{product_id}.pdf",
            "source_sha256": "a" * 64,
            "page": 1,
            "chunk_index": 0,
        },
    )


def test_real_chroma_persists_and_filters_page_evidence(tmp_path: Path) -> None:
    first = ChromaProductVectorStore(
        persist_directory=tmp_path,
        embedding_provider=TinyEmbedding(),
        validate_manifest=False,
    )
    first.replace(
        [
            _chunk("supplier-1", "SUPPLIER_PAYMENT", "PAYMENT-USANCE"),
            _chunk("fx-1", "FX_FORWARD_MATURITY", "FX-LOAN"),
        ]
    )

    reopened = ChromaProductVectorStore(
        persist_directory=tmp_path,
        embedding_provider=TinyEmbedding(),
        validate_manifest=False,
    )
    hits = reopened.search("공급자 지급", ["SUPPLIER_PAYMENT"], top_k=1)

    assert reopened.collection.count() == 2
    assert len(hits) == 1
    assert hits[0]["product_id"] == "PAYMENT-USANCE"
    assert hits[0]["page"] == 1

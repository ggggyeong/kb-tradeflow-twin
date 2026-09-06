from __future__ import annotations

import hashlib
import json
import unicodedata
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from importlib import import_module
from pathlib import Path
from typing import Any

VECTOR_MANIFEST_SCHEMA = "product-vector-manifest-v1"
DEFAULT_COLLECTION_NAME = "kb_product_evidence"
DEFAULT_EMBEDDING_MODEL = "intfloat/multilingual-e5-base"
DEFAULT_EMBEDDING_DIMENSION = 768
DEFAULT_CHUNK_SIZE = 800
DEFAULT_CHUNK_OVERLAP = 100
DEFAULT_MANIFEST_NAME = "product_vector_manifest.json"
SCENARIO_CODES = ("WORKING_CAPITAL_LOAN_MATURITY", "FX_FORWARD_MATURITY", "SUPPLIER_PAYMENT")
DEFAULT_PORTFOLIO_PRODUCT_IDS = (
    "KB-GENERAL-WORKING-CAPITAL",
    "KB-FX-LOAN",
    "KB-PAYMENT-USANCE",
    "KB-EXPORT-FACTORING",
    "ONE-KB-CORPORATE-LOAN",
    "KB-MOADREAM-LOAN",
    "KB-KSURE-TRADE-SUPPORT",
)
_SCENARIOS = frozenset(SCENARIO_CODES)
_CORE_PRODUCTS = frozenset(DEFAULT_PORTFOLIO_PRODUCT_IDS)
PdfOcrBackend = Callable[[Path], Sequence[str]]
Metadata = dict[str, str | int | float | bool]


class ProductVectorError(RuntimeError):
    pass


@dataclass(frozen=True)
class ProductChunk:
    chunk_id: str
    document: str
    metadata: Metadata


@dataclass(frozen=True)
class ProductCorpus:
    chunks: tuple[ProductChunk, ...]
    product_ids: tuple[str, ...]
    source_hashes: dict[str, str]
    catalog_sha256: str
    corpus_fingerprint: str


def _hash_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _hash_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _read_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError) as exc:
        raise ProductVectorError(f"Invalid JSON file: {path}") from exc
    if not isinstance(value, dict):
        raise ProductVectorError(f"JSON root must be an object: {path}")
    return value


def _select_products(
    catalog_path: Path,
    include_product_ids: Sequence[str] | None,
) -> tuple[tuple[str, ...], dict[str, dict[str, Any]]]:
    catalog = _read_json(catalog_path)
    if catalog.get("schema_version") != "kb-product-catalog-v2":
        raise ProductVectorError("Unsupported product catalog schema")
    products = {str(item["product_id"]): item for item in catalog.get("products", [])}
    selected = set(_CORE_PRODUCTS)
    selected.update(str(value).strip() for value in (include_product_ids or ()) if str(value).strip())
    unknown = selected - products.keys()
    if unknown:
        raise ProductVectorError(f"Unknown product ids: {sorted(unknown)}")
    ordered = (*DEFAULT_PORTFOLIO_PRODUCT_IDS, *sorted(selected - _CORE_PRODUCTS))
    by_source: dict[str, dict[str, Any]] = {}
    covered: set[str] = set()
    for product_id in ordered:
        item = products[product_id]
        source = unicodedata.normalize("NFC", str(item.get("source_file", "")).strip())
        scenarios = tuple(dict.fromkeys(str(code) for code in item.get("scenario_codes", [])))
        if not source or not scenarios or set(scenarios) - _SCENARIOS or source in by_source:
            raise ProductVectorError(f"Invalid catalog product: {product_id}")
        by_source[source] = {
            "product_id": product_id,
            "product_name": str(item["canonical_name"]).strip(),
            "scenario_codes": scenarios,
        }
        covered.update(scenarios)
    if covered != _SCENARIOS:
        raise ProductVectorError("Selected products must cover all three scenarios")
    return ordered, by_source


def _pdf_paths(pdf_directory: Path) -> dict[str, Path]:
    return {
        unicodedata.normalize("NFC", path.name): path
        for path in Path(pdf_directory).glob("*.pdf")
    }


def split_product_page(
    text: str,
    *,
    chunk_size: int = DEFAULT_CHUNK_SIZE,
    chunk_overlap: int = DEFAULT_CHUNK_OVERLAP,
) -> tuple[str, ...]:
    if chunk_size < 64 or not 0 <= chunk_overlap < chunk_size:
        raise ValueError("Use chunk_size >= 64 and 0 <= overlap < chunk_size")
    lines = [" ".join(line.split()) for line in text.replace("\r\n", "\n").split("\n")]
    text = "\n".join(line for line in lines if line).strip()
    if not text:
        return ()
    step = chunk_size - chunk_overlap
    return tuple(
        text[start : start + chunk_size].strip()
        for start in range(0, len(text), step)
        if text[start : start + chunk_size].strip()
    )


def load_product_vector_corpus(
    *,
    pdf_directory: Path,
    catalog_path: Path,
    ocr_backend: PdfOcrBackend | None = None,
    chunk_size: int = DEFAULT_CHUNK_SIZE,
    chunk_overlap: int = DEFAULT_CHUNK_OVERLAP,
    include_product_ids: Sequence[str] | None = None,
) -> ProductCorpus:
    try:
        from pypdf import PdfReader
    except ImportError as exc:
        raise ProductVectorError("Install pypdf to read product PDFs") from exc
    product_ids, products = _select_products(Path(catalog_path), include_product_ids)
    paths = _pdf_paths(pdf_directory)
    missing = set(products) - paths.keys()
    if missing:
        raise ProductVectorError(f"Missing product PDFs: {sorted(missing)}")

    chunks: list[ProductChunk] = []
    source_hashes: dict[str, str] = {}
    for source, product in products.items():
        path, reader = paths[source], PdfReader(paths[source])
        source_hashes[source] = _hash_file(path)
        native_pages = [(page.extract_text() or "").strip() for page in reader.pages]
        if any(not text for text in native_pages):
            if ocr_backend is None:
                raise ProductVectorError(
                    f"OCR backend is required for image-only product PDF: {source}"
                )
            ocr_pages = list(ocr_backend(path))
            if len(ocr_pages) != len(native_pages):
                raise ProductVectorError(f"OCR page count mismatch: {source}")
        else:
            ocr_pages = [""] * len(native_pages)
        for page, (native_text, ocr_text) in enumerate(zip(native_pages, ocr_pages, strict=True), 1):
            page_chunks = split_product_page(
                native_text or ocr_text,
                chunk_size=chunk_size,
                chunk_overlap=chunk_overlap,
            )
            if not page_chunks:
                raise ProductVectorError(f"No text extracted: {source} page {page}")
            for chunk_index, document in enumerate(page_chunks):
                text_hash = _hash_text(document)
                for scenario in product["scenario_codes"]:
                    identity = f"{product['product_id']}|{scenario}|{source_hashes[source]}|{page}|{chunk_index}|{text_hash}"
                    chunks.append(
                        ProductChunk(
                            chunk_id=f"product-chunk-v1-{_hash_text(identity)}",
                            document=document,
                            metadata={
                                "product_id": str(product["product_id"]),
                                "product_name": str(product["product_name"]),
                                "scenario_code": scenario,
                                "source_file": source,
                                "source_sha256": source_hashes[source],
                                "page": page,
                                "chunk_index": chunk_index,
                            },
                        )
                    )
    chunks.sort(key=lambda chunk: chunk.chunk_id)
    ids = [chunk.chunk_id for chunk in chunks]
    if len(ids) != len(set(ids)):
        raise ProductVectorError("Stable chunk ids are not unique")
    return ProductCorpus(
        chunks=tuple(chunks),
        product_ids=product_ids,
        source_hashes=source_hashes,
        catalog_sha256=_hash_file(Path(catalog_path)),
        corpus_fingerprint=_hash_text("\n".join(ids)),
    )


class MultilingualE5Embedding:
    def __init__(
        self,
        model_name: str = DEFAULT_EMBEDDING_MODEL,
        *,
        dimension: int = DEFAULT_EMBEDDING_DIMENSION,
        model: Any | None = None,
    ) -> None:
        self.model_name, self.dimension, self._model = model_name, dimension, model

    def _encode(self, texts: Sequence[str]) -> list[list[float]]:
        if self._model is None:
            try:
                sentence_transformers = import_module("sentence_transformers")
            except ImportError as exc:
                raise ProductVectorError("Install sentence-transformers for E5") from exc
            self._model = sentence_transformers.SentenceTransformer(self.model_name)
        actual = getattr(self._model, "get_sentence_embedding_dimension", lambda: None)()
        if actual is not None and int(actual) != self.dimension:
            raise ProductVectorError(f"Expected embedding dimension {self.dimension}, got {actual}")
        encoded = self._model.encode(
            list(texts),
            normalize_embeddings=True,
            convert_to_numpy=True,
            show_progress_bar=False,
        )
        values = encoded.tolist() if hasattr(encoded, "tolist") else list(encoded)
        rows = [[float(value) for value in row] for row in values]
        if len(rows) != len(texts) or any(len(row) != self.dimension for row in rows):
            raise ProductVectorError("Embedding output shape is invalid")
        return rows

    def embed_documents(self, texts: Sequence[str]) -> list[list[float]]:
        return self._encode([f"passage: {text.strip()}" for text in texts])

    def embed_query(self, text: str) -> list[float]:
        if not text.strip():
            raise ValueError("query must not be empty")
        return self._encode([f"query: {text.strip()}"])[0]


class ChromaProductVectorStore:
    def __init__(
        self,
        *,
        persist_directory: Path,
        embedding_provider: Any | None = None,
        collection_name: str = DEFAULT_COLLECTION_NAME,
        collection: Any | None = None,
        client: Any | None = None,
        validate_manifest: bool = True,
    ) -> None:
        self.persist_directory = Path(persist_directory)
        self.persist_directory.mkdir(parents=True, exist_ok=True)
        self.manifest_path = self.persist_directory / DEFAULT_MANIFEST_NAME
        self.collection_name = collection_name
        self.embedding_provider = embedding_provider or MultilingualE5Embedding()
        if collection is None:
            if client is None:
                try:
                    chromadb = import_module("chromadb")
                except ImportError as exc:
                    raise ProductVectorError("Install chromadb for product RAG") from exc
                client = chromadb.PersistentClient(path=str(self.persist_directory))
            collection = client.get_or_create_collection(
                name=collection_name,
                metadata={"hnsw:space": "cosine"},
            )
        self.collection = collection
        if validate_manifest:
            self.validate_manifest()

    def replace(self, chunks: Sequence[ProductChunk], batch_size: int = 128) -> None:
        old_ids = self.collection.get(include=[]).get("ids", [])
        if old_ids and isinstance(old_ids[0], list):
            old_ids = [value for group in old_ids for value in group]
        if old_ids:
            self.collection.delete(ids=old_ids)
        for start in range(0, len(chunks), batch_size):
            batch = chunks[start : start + batch_size]
            documents = [chunk.document for chunk in batch]
            self.collection.upsert(
                ids=[chunk.chunk_id for chunk in batch],
                documents=documents,
                metadatas=[chunk.metadata for chunk in batch],
                embeddings=self.embedding_provider.embed_documents(documents),
            )

    def search(
        self,
        query: str,
        scenario_codes: Sequence[str],
        allowed_product_ids: Sequence[str] | None = None,
        top_k: int = 5,
    ) -> list[dict[str, Any]]:
        scenarios = tuple(dict.fromkeys(scenario_codes))
        if not scenarios or set(scenarios) - _SCENARIOS:
            raise ValueError("Unsupported scenario_codes")
        if not 1 <= top_k <= 20:
            raise ValueError("top_k must be between 1 and 20")
        scenario_filter: dict[str, Any] = (
            {"scenario_code": scenarios[0]}
            if len(scenarios) == 1
            else {"scenario_code": {"$in": list(scenarios)}}
        )
        where = scenario_filter
        if allowed_product_ids is not None:
            product_ids = sorted(set(allowed_product_ids))
            if not product_ids:
                raise ValueError("allowed_product_ids must not be empty")
            product_filter: dict[str, Any] = (
                {"product_id": product_ids[0]}
                if len(product_ids) == 1
                else {"product_id": {"$in": product_ids}}
            )
            where = {"$and": [scenario_filter, product_filter]}
        raw = self.collection.query(
            query_embeddings=[self.embedding_provider.embed_query(query)],
            n_results=top_k * max(2, len(scenarios)),
            where=where,
            include=["documents", "metadatas", "distances"],
        )
        ids, documents, metadatas, distances = (
            raw.get(key, [[]])[0] for key in ("ids", "documents", "metadatas", "distances")
        )
        results, seen = [], set()
        for index, chunk_id in enumerate(ids):
            metadata = metadatas[index]
            evidence = (
                metadata["product_id"],
                metadata["source_sha256"],
                metadata["page"],
                metadata["chunk_index"],
            )
            if evidence in seen:
                continue
            seen.add(evidence)
            text = " ".join(documents[index].split())
            distance = float(distances[index]) if distances else None
            results.append(
                {
                    "product_id": metadata["product_id"],
                    "product_name": metadata["product_name"],
                    "scenario_code": metadata["scenario_code"],
                    "source_file": metadata["source_file"],
                    "page": int(metadata["page"]),
                    "source_sha256": metadata["source_sha256"],
                    "excerpt": text if len(text) <= 360 else f"{text[:360].rstrip()}…",
                    "chunk_id": chunk_id,
                    "score": None if distance is None else 1.0 - distance,
                }
            )
            if len(results) == top_k:
                break
        return results

    def validate_manifest(
        self,
        *,
        pdf_directory: Path | None = None,
        catalog_path: Path | None = None,
    ) -> dict[str, Any]:
        manifest = _read_json(self.manifest_path)
        chunk_ids = self.collection.get(include=[]).get("ids", [])
        checks = {
            "schema_version": VECTOR_MANIFEST_SCHEMA,
            "collection_name": self.collection_name,
            "embedding_model": self.embedding_provider.model_name,
            "embedding_dimension": self.embedding_provider.dimension,
            "record_count": self.collection.count(),
            "corpus_fingerprint": _hash_text("\n".join(sorted(chunk_ids))),
        }
        errors = [key for key, expected in checks.items() if manifest.get(key) != expected]
        if catalog_path and manifest.get("catalog_sha256") != _hash_file(Path(catalog_path)):
            errors.append("catalog_sha256")
        if pdf_directory:
            paths = _pdf_paths(pdf_directory)
            for source, expected in manifest.get("source_hashes", {}).items():
                if source not in paths or _hash_file(paths[source]) != expected:
                    errors.append(f"source_hash:{source}")
        if errors:
            raise ProductVectorError(f"Vector manifest mismatch: {', '.join(errors)}")
        return manifest


def build_index_from_current_assets(
    *,
    ocr_backend: PdfOcrBackend | None = None,
    project_root: Path | None = None,
    pdf_directory: Path | None = None,
    catalog_path: Path | None = None,
    persist_directory: Path | None = None,
    embedding_provider: Any | None = None,
    include_product_ids: Sequence[str] | None = None,
    collection_name: str = DEFAULT_COLLECTION_NAME,
    chunk_size: int = DEFAULT_CHUNK_SIZE,
    chunk_overlap: int = DEFAULT_CHUNK_OVERLAP,
    collection: Any | None = None,
    client: Any | None = None,
) -> dict[str, Any]:
    root = Path(project_root or Path(__file__).resolve().parents[2])
    pdf_directory = Path(pdf_directory or root / "kb_doc" / "KB 금융상품 pdf")
    catalog_path = Path(catalog_path or root / "data" / "knowledge" / "product_catalog.json")
    persist_directory = Path(
        persist_directory or root / "data" / "knowledge" / "chroma_products"
    )
    corpus = load_product_vector_corpus(
        pdf_directory=pdf_directory,
        catalog_path=catalog_path,
        ocr_backend=ocr_backend,
        chunk_size=chunk_size,
        chunk_overlap=chunk_overlap,
        include_product_ids=include_product_ids,
    )
    provider = embedding_provider or MultilingualE5Embedding()
    store = ChromaProductVectorStore(
        persist_directory=persist_directory,
        embedding_provider=provider,
        collection_name=collection_name,
        collection=collection,
        client=client,
        validate_manifest=False,
    )
    store.replace(corpus.chunks)
    manifest = {
        "schema_version": VECTOR_MANIFEST_SCHEMA,
        "collection_name": collection_name,
        "embedding_model": provider.model_name,
        "embedding_dimension": provider.dimension,
        "product_ids": list(corpus.product_ids),
        "product_count": len(corpus.product_ids),
        "record_count": len(corpus.chunks),
        "corpus_fingerprint": corpus.corpus_fingerprint,
        "source_hashes": corpus.source_hashes,
        "catalog_sha256": corpus.catalog_sha256,
    }
    store.manifest_path.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    store.validate_manifest(pdf_directory=pdf_directory, catalog_path=catalog_path)
    return manifest

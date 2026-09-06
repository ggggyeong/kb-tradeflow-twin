from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.services.product_vector_store import (  # noqa: E402
    DEFAULT_CHUNK_OVERLAP,
    DEFAULT_CHUNK_SIZE,
    DEFAULT_COLLECTION_NAME,
    DEFAULT_EMBEDDING_DIMENSION,
    DEFAULT_EMBEDDING_MODEL,
    MultilingualE5Embedding,
    _pdf_paths,
    _select_products,
    build_index_from_current_assets,
)

PDF_DIRECTORY = PROJECT_ROOT / "kb_doc" / "KB 금융상품 pdf"
CATALOG_PATH = PROJECT_ROOT / "data" / "knowledge" / "product_catalog.json"
PERSIST_DIRECTORY = PROJECT_ROOT / "data" / "knowledge" / "chroma_products"


def _paddle_pages() -> Any:
    from app.services.ingestion.ocr_backend import default_ocr_backend

    backend = default_ocr_backend()

    def extract(pdf_path: Path) -> list[str]:
        layout = backend.extract(pdf_path)
        return ["\n".join(line.text for line in page.lines) for page in layout.pages]

    return extract


def _dry_run(include_product_ids: list[str]) -> dict[str, Any]:
    from pypdf import PdfReader

    product_ids, products = _select_products(CATALOG_PATH, include_product_ids)
    paths = _pdf_paths(PDF_DIRECTORY)
    missing = set(products) - paths.keys()
    if missing:
        raise FileNotFoundError(f"Missing product PDFs: {sorted(missing)}")
    page_count = native_text_pages = 0
    for source in products:
        pages = PdfReader(paths[source]).pages
        page_count += len(pages)
        native_text_pages += sum(bool((page.extract_text() or "").strip()) for page in pages)
    return {
        "mode": "dry-run",
        "product_ids": list(product_ids),
        "product_count": len(product_ids),
        "page_count": page_count,
        "native_text_pages": native_text_pages,
        "ocr_required_pages": page_count - native_text_pages,
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Build the seven-product Chroma portfolio index.")
    parser.add_argument("--persist-directory", type=Path, default=PERSIST_DIRECTORY)
    parser.add_argument("--collection-name", default=DEFAULT_COLLECTION_NAME)
    parser.add_argument("--embedding-model", default=DEFAULT_EMBEDDING_MODEL)
    parser.add_argument("--embedding-dimension", type=int, default=DEFAULT_EMBEDDING_DIMENSION)
    parser.add_argument("--chunk-size", type=int, default=DEFAULT_CHUNK_SIZE)
    parser.add_argument("--chunk-overlap", type=int, default=DEFAULT_CHUNK_OVERLAP)
    parser.add_argument("--ocr-backend", choices=("paddle", "none"), default="paddle")
    parser.add_argument("--include-product-id", action="append", default=[])
    parser.add_argument("--dry-run", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.dry_run:
        result = _dry_run(args.include_product_id)
    else:
        result = build_index_from_current_assets(
            project_root=PROJECT_ROOT,
            persist_directory=args.persist_directory,
            collection_name=args.collection_name,
            ocr_backend=_paddle_pages() if args.ocr_backend == "paddle" else None,
            embedding_provider=MultilingualE5Embedding(
                args.embedding_model,
                dimension=args.embedding_dimension,
            ),
            include_product_ids=args.include_product_id,
            chunk_size=args.chunk_size,
            chunk_overlap=args.chunk_overlap,
        )
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

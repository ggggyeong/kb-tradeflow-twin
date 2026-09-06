from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
KNOWLEDGE_DIR = PROJECT_ROOT / "data" / "knowledge"
PRODUCT_PDF_DIR = PROJECT_ROOT / "kb_doc" / "KB 금융상품 pdf"
PRODUCT_CATALOG_PATH = KNOWLEDGE_DIR / "product_catalog.json"
PRODUCT_VECTOR_DIR = KNOWLEDGE_DIR / "chroma_products"
REPORT_OUTPUT_DIR = PROJECT_ROOT / "output" / "pdf"

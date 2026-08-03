from __future__ import annotations

import hashlib
import json
from pathlib import Path

from pypdf import PdfReader

from app.core.config import PROJECT_ROOT
from app.domain_inputs.providers import field_registry
from app.schemas.field_contract import DocumentType
from app.services.ingestion.fixed_extractors import extract_fixed_document

FIXTURE_DIR = PROJECT_ROOT / "data" / "demo_judges_v2"


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def test_demo_judges_v2_manifest_matches_eight_new_pdfs() -> None:
    manifest = json.loads((FIXTURE_DIR / "manifest.json").read_text(encoding="utf-8"))

    assert manifest["manifest_version"] == "demo-judges-v2"
    assert manifest["dataset_id"] == "demo_judges_v2"
    assert manifest["batch_id"] == "DEMO-DOCS-20260710"
    assert manifest["document_count"] == 8
    assert [
        (item["transaction_reference"], item["finance_transaction_id"], item["company_id"])
        for item in manifest["transactions"]
    ] == [
        ("TRD-DEMO-001", "TXN-DEMO-001", "DEMO-A"),
        ("TRD-DEMO-002", "TXN-DEMO-002", "DEMO-A"),
        ("TRD-DEMO-003", "TXN-DEMO-003", "DEMO-A"),
    ]
    assert [len(item["expected_documents"]) for item in manifest["transactions"]] == [3, 3, 2]
    assert manifest["transactions"][1]["manual_override_value"] == "Jun 28, 2026"
    assert manifest["transactions"][1]["manual_override_normalized"] == "2026-06-28"

    pdf_paths = sorted(FIXTURE_DIR.glob("*.pdf"))
    assert len(pdf_paths) == 8
    assert {path.name for path in pdf_paths} == {
        item["file_name"] for item in manifest["documents"]
    }
    for item in manifest["documents"]:
        path = FIXTURE_DIR / item["file_name"]
        reader = PdfReader(path)
        text = "\n".join(page.extract_text() or "" for page in reader.pages)
        assert path.stat().st_size == item["bytes"]
        assert _sha256(path) == item["sha256"]
        assert len(reader.pages) == item["pages"] == 1
        assert item["transaction_reference"] in text
        assert item["finance_transaction_id"] in text
        assert item["company_id"] in text


def test_demo_judges_v2_pdfs_match_fixed_extractor_and_core22_contract() -> None:
    manifest = json.loads((FIXTURE_DIR / "manifest.json").read_text(encoding="utf-8"))
    documents_by_file = {item["file_name"]: item for item in manifest["documents"]}
    registry = field_registry()
    counts = {doc_type: 0 for doc_type in DocumentType}
    missing_by_file: dict[str, list[str]] = {}

    for path in sorted(FIXTURE_DIR.glob("*.pdf")):
        expected = documents_by_file[path.name]
        extraction = extract_fixed_document(path)
        assert extraction.classification.status == "AUTO_CONFIRMED"
        assert extraction.classification.doc_type is not None
        doc_type = extraction.classification.doc_type
        counts[doc_type] += 1
        assert doc_type is DocumentType(expected["doc_type"])
        assert extraction.model_calls == 0
        assert extraction.source_references == {
            "transaction_reference": expected["transaction_reference"],
            "finance_transaction_id": expected["finance_transaction_id"],
            "company_id": "DEMO-A",
        }
        assert extraction.missing_core_fields == expected["expected_missing_core_fields"]
        extracted_core = registry.core_keys(doc_type) & set(extraction.fields)
        assert extracted_core == registry.core_keys(doc_type) - set(
            expected["expected_missing_core_fields"]
        )
        assert sorted(extracted_core) == expected["extracted_core_fields"]
        missing_by_file[path.name] = extraction.missing_core_fields

    assert counts == {
        DocumentType.BILL_OF_LADING: 2,
        DocumentType.BOOKING_CONFIRMATION: 3,
        DocumentType.COMMERCIAL_INVOICE: 3,
    }
    assert {name: fields for name, fields in missing_by_file.items() if fields} == {
        "06_TRD-DEMO-002_bill_of_lading_missing_on_board_date.pdf": ["on_board_date"]
    }

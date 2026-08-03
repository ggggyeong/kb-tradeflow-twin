from __future__ import annotations

import hashlib
import json
from pathlib import Path

from pypdf import PdfReader

from app.core.config import PROJECT_ROOT
from app.schemas.field_contract import DocumentType
from app.services.ingestion.fixed_extractors import extract_fixed_document

FIXTURE_DIR = PROJECT_ROOT / "data" / "fixtures" / "live_trade_documents"


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def test_live_fixture_manifest_matches_eight_readable_pdfs() -> None:
    manifest = json.loads((FIXTURE_DIR / "manifest.json").read_text(encoding="utf-8"))

    assert manifest["manifest_version"] == "live-8pdf-v1"
    assert manifest["document_count"] == 8
    assert [
        (item["transaction_reference"], item["finance_transaction_id"])
        for item in manifest["transactions"]
    ] == [
        ("TRD-001", "TXN001"),
        ("TRD-002", "TXN002"),
        ("TRD-003", "TXN003"),
    ]
    assert [len(item["expected_documents"]) for item in manifest["transactions"]] == [
        3,
        3,
        2,
    ]

    for item in manifest["documents"]:
        path = FIXTURE_DIR / item["file_name"]
        reader = PdfReader(path)
        text = "\n".join(page.extract_text() or "" for page in reader.pages)
        assert path.stat().st_size == item["bytes"]
        assert _sha256(path) == item["sha256"]
        assert len(reader.pages) == item["pages"] == 1
        assert item["transaction_reference"] in text
        assert item["finance_transaction_id"] in text


def test_live_fixtures_classify_and_expose_the_single_blank_core_field() -> None:
    missing_by_file: dict[str, list[str]] = {}
    type_by_file: dict[str, DocumentType] = {}
    for path in sorted(FIXTURE_DIR.glob("*.pdf")):
        extraction = extract_fixed_document(path)
        assert extraction.classification.status == "AUTO_CONFIRMED"
        assert extraction.classification.doc_type is not None
        assert extraction.source_references["transaction_reference"].startswith("TRD-")
        assert extraction.source_references["finance_transaction_id"].startswith("TXN")
        missing_by_file[path.name] = extraction.missing_core_fields
        type_by_file[path.name] = extraction.classification.doc_type

    assert (
        sum(doc_type is DocumentType.BOOKING_CONFIRMATION for doc_type in type_by_file.values())
        == 3
    )
    assert (
        sum(doc_type is DocumentType.COMMERCIAL_INVOICE for doc_type in type_by_file.values()) == 3
    )
    assert sum(doc_type is DocumentType.BILL_OF_LADING for doc_type in type_by_file.values()) == 2
    assert {file_name: missing for file_name, missing in missing_by_file.items() if missing} == {
        "06_TRD-002_bill_of_lading_missing_on_board_date.pdf": ["on_board_date"]
    }

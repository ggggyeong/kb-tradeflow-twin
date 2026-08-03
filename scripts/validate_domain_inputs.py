from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from pypdf import PdfReader

from app.core.config import PROJECT_ROOT
from app.domain_inputs.providers import validate_active_inputs

EXPECTED: dict[str, dict[str, Any]] = {
    "data/domain_inputs/active/document_field_dictionary.xlsx": {
        "sha256": "fccd5d751556e92a33cbe1c3931bb2e8475434b7edc8dae3448076d30e285bcd",
        "kind": "xlsx",
    },
    "data/domain_inputs/active/payment_terms_cases.xlsx": {
        "sha256": "8af207ba36a674fd0596250d202420d589f14f73e01c9a58184f2fbf3cf702d8",
        "kind": "xlsx",
    },
    "data/template_references/booking_one_v1.pdf": {
        "sha256": "5cf89cc60a0a910f3edc02b5838a7ad510997946b2dca4a4651f41d43051d59f",
        "kind": "pdf",
        "pages": 1,
    },
    "data/template_references/bill_of_lading_v1.pdf": {
        "sha256": "b66a22486a8d5b853f2e34e3b95333b0c2b47d4bdc1c78a81b00f20f9380163e",
        "kind": "pdf",
        "pages": 1,
    },
    "data/template_references/commercial_invoice_v1.pdf": {
        "sha256": "a6d50e7b1da914598178f7fbc3401dff6559830e40bbfdf4e61cfc54768cccc5",
        "kind": "pdf",
        "pages": 1,
    },
    "data/contracts/build_spec_v16.pdf": {
        "sha256": "cfbc3fbd83e59f200df4abbb69c865672eaf8c82c3a1722fbdb797bf6bd3d4ad",
        "kind": "pdf",
        "pages": 27,
    },
    "data/contracts/native_agent_contract_v1.pdf": {
        "sha256": "2c72dd7ad24098bee6d0b1d720bc4b4c1ec0fb795716b01dc14914283bfa5f9d",
        "kind": "pdf",
        "pages": 31,
    },
}


def sha256(path: Path) -> str:
    """Return the binary SHA-256 of one immutable source."""
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def build_manifest() -> dict[str, Any]:
    """Validate source hash, PDF integrity/page count, and XLSX contracts."""
    files: list[dict[str, Any]] = []
    for relative_path, expected in EXPECTED.items():
        path = PROJECT_ROOT / relative_path
        if not path.exists():
            raise FileNotFoundError(f"Required source missing: {relative_path}")
        actual_hash = sha256(path)
        if actual_hash != expected["sha256"]:
            raise ValueError(f"Source hash mismatch: {relative_path}")
        record: dict[str, Any] = {
            "path": relative_path,
            "kind": expected["kind"],
            "sha256": actual_hash,
            "bytes": path.stat().st_size,
        }
        if expected["kind"] == "pdf":
            reader = PdfReader(path)
            page_count = len(reader.pages)
            if page_count != expected["pages"]:
                raise ValueError(f"PDF page mismatch: {relative_path}")
            record["pages"] = page_count
        files.append(record)
    return {
        "manifest_version": "v16.0",
        "immutable": True,
        "files": files,
        "domain_contract": validate_active_inputs(),
    }


def main() -> None:
    """Write the validated immutable source manifest."""
    manifest = build_manifest()
    target = PROJECT_ROOT / "data" / "source_manifest.json"
    target.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(manifest["domain_contract"], ensure_ascii=False, sort_keys=True))
    print(target)


if __name__ == "__main__":
    main()

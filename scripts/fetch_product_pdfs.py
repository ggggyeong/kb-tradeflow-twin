"""Download a small official corpus. No customer files or credentials are sent."""

from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.services.product_catalog import ProductCatalog  # noqa: E402


def main() -> None:
    target = ROOT / "data" / "knowledge" / "products"
    target.mkdir(parents=True, exist_ok=True)
    for source in ProductCatalog.load().products:
        if not source.enabled:
            continue
        name, url = source.source_file, source.source_url
        assert url is not None
        path = target / name
        if path.exists():
            data = path.read_bytes()
        else:
            with httpx.stream("GET", url, timeout=45, follow_redirects=True) as response:
                response.raise_for_status()
                parts = []
                size = 0
                for part in response.iter_bytes():
                    size += len(part)
                    if size > 15_000_000:
                        raise ValueError(f"Oversized PDF: {name}")
                    parts.append(part)
                data = b"".join(parts)
            if not data.startswith(b"%PDF") or len(data) > 15_000_000:
                raise ValueError(f"Invalid or oversized PDF: {name}")
            if hashlib.sha256(data).hexdigest() != source.reviewed_sha256:
                raise ValueError(f"Source changed; review before saving or indexing: {name}")
            path.write_bytes(data)
        if hashlib.sha256(data).hexdigest() != source.reviewed_sha256:
            raise ValueError(f"Local PDF differs from reviewed source: {name}")
        print(
            json.dumps(
                {"file": name, "bytes": len(data), "sha256": hashlib.sha256(data).hexdigest()}
            )
        )


if __name__ == "__main__":
    main()

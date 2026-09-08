import hashlib
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from scripts import fetch_product_pdfs


def test_fetch_uses_active_catalog_only_without_rewriting_cached_source(
    tmp_path: Path, monkeypatch: Any, capsys: Any
) -> None:
    data = b"%PDF-1.4 synthetic test source"
    source = SimpleNamespace(
        enabled=True,
        source_file="active.pdf",
        source_url="https://example.com/active.pdf",
        reviewed_sha256=hashlib.sha256(data).hexdigest(),
    )
    catalog = SimpleNamespace(products=[source, SimpleNamespace(enabled=False)])
    monkeypatch.setattr(fetch_product_pdfs, "ROOT", tmp_path)
    monkeypatch.setattr(fetch_product_pdfs.ProductCatalog, "load", lambda: catalog)

    def forbidden_network(*args: Any, **kwargs: Any) -> None:
        raise AssertionError("Cached or archived sources must not trigger a download")

    monkeypatch.setattr(fetch_product_pdfs.httpx, "stream", forbidden_network)
    target = tmp_path / "data/knowledge/products/active.pdf"
    target.parent.mkdir(parents=True)
    target.write_bytes(data)
    modified_at = target.stat().st_mtime_ns
    fetch_product_pdfs.main()
    assert "active.pdf" in capsys.readouterr().out
    assert target.stat().st_mtime_ns == modified_at

    target.write_bytes(b"%PDF different from reviewed source")
    with pytest.raises(ValueError, match="differs from reviewed source"):
        fetch_product_pdfs.main()
    assert target.read_bytes() == b"%PDF different from reviewed source"

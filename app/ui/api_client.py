from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import httpx

API_BASE = os.getenv("TRADEFLOW_API_URL", "http://127.0.0.1:8000")
REQUEST_TIMEOUT = float(os.getenv("TRADEFLOW_UI_REQUEST_TIMEOUT_SECONDS", "600"))


def get(path: str) -> Any:
    response = httpx.get(f"{API_BASE}{path}", timeout=REQUEST_TIMEOUT)
    response.raise_for_status()
    return response.json()


def post(path: str, payload: dict[str, Any] | None = None) -> Any:
    response = httpx.post(
        f"{API_BASE}{path}",
        json=payload or {},
        timeout=REQUEST_TIMEOUT,
    )
    response.raise_for_status()
    return response.json()


def upload(batch_id: str, files: list[Any]) -> Any:
    content_types = {
        ".pdf": "application/pdf",
        ".xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    }
    multipart = [
        (
            "files",
            (
                item.name,
                item.getvalue(),
                content_types.get(
                    Path(item.name).suffix.lower(),
                    "application/octet-stream",
                ),
            ),
        )
        for item in files
    ]
    response = httpx.post(
        f"{API_BASE}/api/files/upload",
        params={"batch_id": batch_id},
        files=multipart,
        timeout=REQUEST_TIMEOUT,
    )
    response.raise_for_status()
    return response.json()

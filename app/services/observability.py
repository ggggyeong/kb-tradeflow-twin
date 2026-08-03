from __future__ import annotations

import hashlib
import time
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

from sqlalchemy.orm import Session

from app.db.models import TraceEvent

SENSITIVE_KEYS = {"content", "raw_text", "openai_api_key", "langsmith_api_key"}


def _current_trace_id() -> str | None:
    try:
        from langsmith.run_helpers import get_current_run_tree

        run = get_current_run_tree()
        return str(run.trace_id) if run is not None else None
    except Exception:
        return None


def _safe_summary(value: dict[str, Any] | None) -> dict[str, Any]:
    """Retain operational metadata while excluding document bodies and secrets."""
    if not value:
        return {}
    safe: dict[str, Any] = {}
    for key, item in value.items():
        if key.lower() in SENSITIVE_KEYS:
            rendered = str(item)
            safe[f"{key}_sha256"] = hashlib.sha256(rendered.encode()).hexdigest()
            safe[f"{key}_length"] = len(rendered)
        else:
            safe[key] = item
    return safe


def record_local_event(
    session: Session,
    *,
    request_id: str,
    span_type: str,
    name: str,
    input_summary: dict[str, Any] | None = None,
    output_summary: dict[str, Any] | None = None,
    metadata: dict[str, Any] | None = None,
    timing_ms: float = 0.0,
) -> None:
    """Append a single graph/agent/tool event to the local debug tree."""
    session.add(
        TraceEvent(
            request_id=request_id,
            trace_id=_current_trace_id(),
            span_type=span_type,
            name=name,
            input_summary_json=_safe_summary(input_summary),
            output_summary_json=_safe_summary(output_summary),
            timing_ms=timing_ms,
            metadata_json={
                "privacy": "NO_DOCUMENT_BODY",
                **(metadata or {}),
            },
        )
    )


@contextmanager
def local_span(
    session: Session,
    request_id: str,
    span_type: str,
    name: str,
    input_summary: dict[str, Any] | None = None,
    metadata: dict[str, Any] | None = None,
) -> Iterator[dict[str, Any]]:
    """Persist a privacy-safe local span whether the operation succeeds or fails."""
    started = time.perf_counter()
    output: dict[str, Any] = {}
    status = "SUCCESS"
    try:
        yield output
    except Exception:
        status = "FAILED"
        raise
    finally:
        session.add(
            TraceEvent(
                request_id=request_id,
                trace_id=_current_trace_id(),
                span_type=span_type,
                name=name,
                input_summary_json=_safe_summary(input_summary),
                output_summary_json=_safe_summary(output),
                timing_ms=round((time.perf_counter() - started) * 1000, 3),
                metadata_json={
                    "status": status,
                    "privacy": "NO_DOCUMENT_BODY",
                    **(metadata or {}),
                },
            )
        )

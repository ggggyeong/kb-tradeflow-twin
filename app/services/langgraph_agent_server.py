from __future__ import annotations

from collections.abc import Mapping
from typing import Any

import httpx

from app.core.config import get_settings


class LangGraphAgentServerError(RuntimeError):
    """Raised when the local LangGraph Agent Server cannot fulfill a request."""


def _post(path: str, payload: Mapping[str, Any]) -> dict[str, Any]:
    settings = get_settings()
    try:
        response = httpx.post(
            f"{settings.langgraph_agent_url.rstrip('/')}{path}",
            json=dict(payload),
            timeout=settings.langgraph_request_timeout_seconds,
        )
        response.raise_for_status()
        result = response.json()
    except (httpx.HTTPError, ValueError) as exc:
        raise LangGraphAgentServerError(
            "LangGraph Agent Server(:2024)에 연결하지 못했습니다. "
            "먼저 LangGraph Dev를 실행해 주세요."
        ) from exc
    if not isinstance(result, dict):
        raise LangGraphAgentServerError("LangGraph Agent Server가 잘못된 응답을 반환했습니다.")
    return result


def create_chat_thread(*, metadata: Mapping[str, Any] | None = None) -> dict[str, Any]:
    """Create a real Agent Server thread associated with the public chat graph."""
    settings = get_settings()
    return _post(
        "/threads",
        {
            "graph_id": settings.langgraph_graph_id,
            "metadata": {"source": "streamlit", **dict(metadata or {})},
        },
    )


def run_chat_thread(thread_id: str, graph_input: Mapping[str, Any]) -> dict[str, Any]:
    """Run chat on Agent Server so the same execution is observable in Studio."""
    settings = get_settings()
    return _post(
        f"/threads/{thread_id}/runs/wait",
        {
            "assistant_id": settings.langgraph_graph_id,
            "input": dict(graph_input),
            "multitask_strategy": "reject",
        },
    )


def resume_chat_thread(thread_id: str, resume_payload: Mapping[str, Any]) -> dict[str, Any]:
    """Resume a Human interrupt on the exact same Agent Server thread."""
    settings = get_settings()
    return _post(
        f"/threads/{thread_id}/runs/wait",
        {
            "assistant_id": settings.langgraph_graph_id,
            "command": {"resume": dict(resume_payload)},
            "multitask_strategy": "reject",
        },
    )

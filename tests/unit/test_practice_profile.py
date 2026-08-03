from __future__ import annotations

from pathlib import Path


def _read_profile() -> dict[str, str]:
    project_root = Path(__file__).resolve().parents[2]
    lines = (project_root / "config" / "practice.env").read_text(encoding="utf-8")
    values: dict[str, str] = {}
    for raw_line in lines.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        key, value = line.split("=", 1)
        values[key] = value
    return values


def test_practice_profile_is_secret_free_and_role_aware() -> None:
    profile = _read_profile()

    assert "OPENAI_API_KEY" not in profile
    assert "LANGSMITH_API_KEY" not in profile
    assert profile["TRADEFLOW_MODEL_PLANNING"] == "gpt-5.6-terra"
    assert profile["TRADEFLOW_MODEL_SUPERVISOR"] == "gpt-5.6-terra"
    assert profile["TRADEFLOW_MODEL_FINANCIAL"] == "gpt-5.6-terra"
    assert profile["TRADEFLOW_MODEL_CRITIC"] == "gpt-5.6-terra"
    assert profile["TRADEFLOW_MODEL_DOCUMENT"] == "gpt-5.6-luna"
    assert profile["TRADEFLOW_MODEL_SHIPMENT"] == "gpt-5.6-luna"
    assert profile["TRADEFLOW_MODEL_PRODUCT"] == "gpt-5.6-luna"
    assert profile["TRADEFLOW_MODEL_REPORT_WRITER"] == "gpt-5.6-luna"
    assert profile["TRADEFLOW_REASONING_SUPERVISOR"] == "low"
    assert profile["TRADEFLOW_REASONING_SPECIALIST"] == "low"
    assert profile["TRADEFLOW_REASONING_CRITIC"] == "low"

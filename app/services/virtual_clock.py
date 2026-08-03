from __future__ import annotations

import json
from datetime import date, timedelta

from app.core.config import PROJECT_ROOT

CLOCK_PATH = PROJECT_ROOT / "data" / "virtual_clock.json"


def current_date() -> date:
    if not CLOCK_PATH.exists():
        set_date(date(2026, 7, 10))
    payload = json.loads(CLOCK_PATH.read_text(encoding="utf-8"))
    return date.fromisoformat(payload["current_date"])


def set_date(value: date) -> date:
    CLOCK_PATH.parent.mkdir(parents=True, exist_ok=True)
    temporary = CLOCK_PATH.with_suffix(".tmp")
    temporary.write_text(
        json.dumps({"current_date": value.isoformat()}, indent=2),
        encoding="utf-8",
    )
    temporary.replace(CLOCK_PATH)
    return value


def advance(days: int) -> date:
    if days < 0:
        raise ValueError("Virtual clock can only move forward")
    return set_date(current_date() + timedelta(days=days))

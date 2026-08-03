from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

PROJECT_ROOT = Path(__file__).resolve().parents[2]


class Settings(BaseSettings):
    """Validated runtime configuration loaded from the project-root .env file."""

    model_config = SettingsConfigDict(
        env_file=PROJECT_ROOT / ".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=True,
    )

    tradeflow_mode: Literal["offline", "live"] = Field("offline", alias="TRADEFLOW_MODE")
    database_url: str = Field("sqlite:///./data/tradeflow.db", alias="DATABASE_URL")
    checkpoint_db_path: str = Field("./data/checkpoints.sqlite", alias="CHECKPOINT_DB_PATH")
    deterministic_final_answer: bool = Field(
        True,
        alias="TRADEFLOW_DETERMINISTIC_FINAL_ANSWER",
    )
    live_replanning_model: bool = Field(
        False,
        alias="TRADEFLOW_LIVE_REPLANNING_MODEL",
    )
    live_supervisor_routing_model: bool = Field(
        True,
        alias="TRADEFLOW_LIVE_SUPERVISOR_ROUTING_MODEL",
    )

    openai_api_key: SecretStr | None = Field(None, alias="OPENAI_API_KEY")
    langsmith_api_key: SecretStr | None = Field(None, alias="LANGSMITH_API_KEY")
    langsmith_tracing: bool = Field(True, alias="LANGSMITH_TRACING")
    langsmith_project: str = Field("kb-tradeflow-twin-live", alias="LANGSMITH_PROJECT")
    langgraph_agent_url: str = Field(
        "http://127.0.0.1:2024",
        alias="TRADEFLOW_LANGGRAPH_URL",
    )
    langgraph_graph_id: str = Field("chat", alias="TRADEFLOW_LANGGRAPH_GRAPH_ID")
    langgraph_request_timeout_seconds: float = Field(
        600.0,
        alias="TRADEFLOW_LANGGRAPH_TIMEOUT_SECONDS",
    )

    model_planning: str = Field("gpt-5.6-sol", alias="TRADEFLOW_MODEL_PLANNING")
    model_supervisor: str = Field("gpt-5.6-sol", alias="TRADEFLOW_MODEL_SUPERVISOR")
    model_document: str = Field("gpt-5.6-terra", alias="TRADEFLOW_MODEL_DOCUMENT")
    model_shipment: str = Field("gpt-5.6-terra", alias="TRADEFLOW_MODEL_SHIPMENT")
    model_financial: str = Field("gpt-5.6-terra", alias="TRADEFLOW_MODEL_FINANCIAL")
    model_product: str = Field("gpt-5.6-terra", alias="TRADEFLOW_MODEL_PRODUCT")
    model_report_writer: str = Field("gpt-5.6-terra", alias="TRADEFLOW_MODEL_REPORT_WRITER")
    model_critic: str = Field("gpt-5.6-sol", alias="TRADEFLOW_MODEL_CRITIC")

    reasoning_supervisor: str = Field("medium", alias="TRADEFLOW_REASONING_SUPERVISOR")
    reasoning_specialist: str = Field("medium", alias="TRADEFLOW_REASONING_SPECIALIST")
    reasoning_critic: str = Field("medium", alias="TRADEFLOW_REASONING_CRITIC")

    def require_openai_key(self) -> SecretStr:
        """Return the OpenAI key or fail before a live model is constructed."""
        if self.openai_api_key is None or not self.openai_api_key.get_secret_value():
            raise RuntimeError("OPENAI_API_KEY is required when TRADEFLOW_MODE=live")
        return self.openai_api_key


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Return the process-wide immutable settings snapshot."""
    settings = Settings()  # type: ignore[call-arg]
    if settings.langsmith_api_key is not None:
        os.environ.setdefault(
            "LANGSMITH_API_KEY",
            settings.langsmith_api_key.get_secret_value(),
        )
    os.environ.setdefault("LANGSMITH_PROJECT", settings.langsmith_project)
    os.environ.setdefault("LANGSMITH_TRACING", str(settings.langsmith_tracing).lower())
    return settings


def clear_settings_cache() -> None:
    """Clear cached settings for tests that alter environment variables."""
    get_settings.cache_clear()

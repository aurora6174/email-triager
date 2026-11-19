from functools import lru_cache
from typing import Literal

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # Which LLM backs the classifier. "fake" is a deterministic keyword classifier used by
    # tests and for running the whole pipeline without an API key.
    llm_provider: Literal["anthropic", "openai", "fake"] = "anthropic"
    anthropic_model: str = "claude-opus-5-5"
    anthropic_effort: Literal["low", "medium", "high"] = "low"
    openai_model: str = "gpt-5-mini"
    anthropic_api_key: SecretStr | None = None
    openai_api_key: SecretStr | None = None
    llm_timeout_seconds: float = 60.0
    llm_max_retries: int = 2  # SDK-level retries for 429/5xx/connection errors

    # Shared secret n8n sends in X-API-Key. Empty disables auth (local dev only).
    classifier_api_key: SecretStr = SecretStr("")

    # Below this self-reported confidence the message goes to human review.
    confidence_threshold: float = Field(default=0.75, ge=0.0, le=1.0)
    # Bodies longer than this are cut before reaching the LLM (cost + abuse guard).
    # The response reports body_truncated=true; nothing is cut silently.
    max_body_chars: int = 12_000

    db_path: str = "data/classifier.db"
    # A claim older than this is treated as abandoned (process crashed mid-call).
    claim_ttl_seconds: int = 180
    # After a result is handed out, duplicates are told "delivering" for this long. Must
    # exceed the worst-case delivery time in n8n (destination + ack retries, about 45 s).
    delivery_lease_seconds: int = 120


@lru_cache
def get_settings() -> Settings:
    return Settings()

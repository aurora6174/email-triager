from app.config import Settings

from .base import (
    LLMProvider,
    ProviderConfigError,
    ProviderOutputError,
    ProviderUnavailable,
)


def build_provider(settings: Settings) -> LLMProvider:
    if settings.llm_provider == "anthropic":
        from .anthropic_provider import AnthropicProvider

        key = settings.anthropic_api_key
        return AnthropicProvider(
            model=settings.anthropic_model,
            effort=settings.anthropic_effort,
            api_key=key.get_secret_value() if key else None,
            timeout=settings.llm_timeout_seconds,
            max_retries=settings.llm_max_retries,
        )
    if settings.llm_provider == "openai":
        from .openai_provider import OpenAIProvider

        key = settings.openai_api_key
        return OpenAIProvider(
            model=settings.openai_model,
            api_key=key.get_secret_value() if key else None,
            timeout=settings.llm_timeout_seconds,
            max_retries=settings.llm_max_retries,
        )
    from .fake import FakeProvider

    return FakeProvider()


__all__ = [
    "LLMProvider",
    "ProviderConfigError",
    "ProviderOutputError",
    "ProviderUnavailable",
    "build_provider",
]

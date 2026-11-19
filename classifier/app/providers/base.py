from typing import Protocol


class ProviderUnavailable(Exception):
    """Transient failure (rate limit, 5xx, timeout) that survived the SDK's own retries.
    The caller should retry later; the API answers 503 so n8n's retry/error path runs."""


class ProviderOutputError(Exception):
    """The provider answered, but not with something we can use (refusal, truncated or
    off-schema output). Retrying the same input rarely helps; route to a human instead."""


class ProviderConfigError(Exception):
    """Bad key, unknown model, malformed request. Needs an operator, not a retry."""


class LLMProvider(Protocol):
    name: str
    model: str

    def classify(self, system: str, user: str, schema: dict) -> dict:
        """Return the provider's JSON answer as a dict (not yet validated)."""
        ...

import json

import anthropic

from .base import ProviderConfigError, ProviderOutputError, ProviderUnavailable

# Models that accept the server-side refusal fallback. On these, a safety-classifier
# decline is retried on a fallback model inside the same API call instead of failing.
_FALLBACK_MODELS = ("claude-opus-5-5", "claude-opus-5", "claude-sonnet-5-5", "claude-fable-5")


class AnthropicProvider:
    name = "anthropic"

    def __init__(
        self,
        model: str,
        effort: str,
        api_key: str | None,
        timeout: float,
        max_retries: int,
        client: anthropic.Anthropic | None = None,
    ):
        self.model = model
        self.effort = effort
        # With api_key=None the SDK falls back to its normal credential chain.
        self.client = client or anthropic.Anthropic(
            api_key=api_key, timeout=timeout, max_retries=max_retries
        )

    def classify(self, system: str, user: str, schema: dict) -> dict:
        kwargs: dict = {}
        if self.model.startswith(_FALLBACK_MODELS):
            kwargs = {"betas": ["server-side-fallback-2026-07-01"], "fallbacks": "default"}
        try:
            response = self.client.beta.messages.create(
                model=self.model,
                # Room for adaptive thinking plus a ~150-token JSON answer.
                max_tokens=4096,
                system=system,
                messages=[{"role": "user", "content": user}],
                output_config={
                    "effort": self.effort,
                    "format": {"type": "json_schema", "schema": schema},
                },
                **kwargs,
            )
        except (
            anthropic.RateLimitError,
            anthropic.InternalServerError,
            anthropic.APITimeoutError,
            anthropic.APIConnectionError,
        ) as e:
            raise ProviderUnavailable(f"anthropic: {type(e).__name__}") from e
        except anthropic.APIStatusError as e:
            if e.status_code >= 500:
                raise ProviderUnavailable(f"anthropic: HTTP {e.status_code}") from e
            raise ProviderConfigError(f"anthropic: HTTP {e.status_code}: {e.message}") from e

        if response.stop_reason == "refusal":
            raise ProviderOutputError("anthropic: model refused")
        if response.stop_reason == "max_tokens":
            raise ProviderOutputError("anthropic: output truncated at max_tokens")

        text = next((b.text for b in response.content if b.type == "text"), None)
        if not text:
            raise ProviderOutputError("anthropic: no text block in response")
        try:
            return json.loads(text)
        except json.JSONDecodeError as e:
            raise ProviderOutputError("anthropic: response was not valid JSON") from e

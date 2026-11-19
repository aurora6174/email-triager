import json

import openai

from .base import ProviderConfigError, ProviderOutputError, ProviderUnavailable


class OpenAIProvider:
    name = "openai"

    def __init__(
        self,
        model: str,
        api_key: str | None,
        timeout: float,
        max_retries: int,
        client: openai.OpenAI | None = None,
    ):
        self.model = model
        self.client = client or openai.OpenAI(
            api_key=api_key, timeout=timeout, max_retries=max_retries
        )

    def classify(self, system: str, user: str, schema: dict) -> dict:
        try:
            response = self.client.responses.create(
                model=self.model,
                instructions=system,
                input=user,
                max_output_tokens=4096,
                text={
                    "format": {
                        "type": "json_schema",
                        "name": "classification",
                        "schema": schema,
                        "strict": True,
                    }
                },
            )
        except (
            openai.RateLimitError,
            openai.InternalServerError,
            openai.APITimeoutError,
            openai.APIConnectionError,
        ) as e:
            raise ProviderUnavailable(f"openai: {type(e).__name__}") from e
        except openai.APIStatusError as e:
            if e.status_code >= 500:
                raise ProviderUnavailable(f"openai: HTTP {e.status_code}") from e
            raise ProviderConfigError(f"openai: HTTP {e.status_code}: {e.message}") from e

        if response.status == "incomplete":
            raise ProviderOutputError("openai: response incomplete (token limit or filter)")
        for item in response.output:
            for part in getattr(item, "content", None) or []:
                if part.type == "refusal":
                    raise ProviderOutputError("openai: model refused")

        text = response.output_text
        if not text:
            raise ProviderOutputError("openai: empty response")
        try:
            return json.loads(text)
        except json.JSONDecodeError as e:
            raise ProviderOutputError("openai: response was not valid JSON") from e

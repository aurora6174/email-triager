import json
from types import SimpleNamespace

import anthropic
import httpx
import pytest

from app.providers import ProviderOutputError, ProviderUnavailable
from app.providers.anthropic_provider import AnthropicProvider
from app.schema import LLM_OUTPUT_SCHEMA, Category, Classification
from app.service import ROUTE_BY_CATEGORY
from tests.conftest import good


def test_llm_schema_matches_pydantic_model():
    assert set(LLM_OUTPUT_SCHEMA["properties"]) == set(Classification.model_fields)
    assert set(LLM_OUTPUT_SCHEMA["required"]) == set(Classification.model_fields)
    assert LLM_OUTPUT_SCHEMA["additionalProperties"] is False


def test_every_category_has_a_route():
    assert set(ROUTE_BY_CATEGORY) == set(Category)


class FakeMessages:
    def __init__(self, result):
        self.result = result
        self.kwargs = None

    def create(self, **kwargs):
        self.kwargs = kwargs
        if isinstance(self.result, Exception):
            raise self.result
        return self.result


def provider_with(result, model="claude-opus-5-5"):
    messages = FakeMessages(result)
    client = SimpleNamespace(beta=SimpleNamespace(messages=messages))
    p = AnthropicProvider(model, "low", "k", 10, 0, client=client)
    return p, messages


GOOD_JSON = json.dumps(good())


def message(stop_reason="end_turn", text=GOOD_JSON):
    return SimpleNamespace(
        stop_reason=stop_reason,
        content=[SimpleNamespace(type="thinking"), SimpleNamespace(type="text", text=text)],
    )


def test_anthropic_parses_json_and_sends_schema_and_fallback():
    p, messages = provider_with(message())
    assert p.classify("sys", "user", LLM_OUTPUT_SCHEMA)["category"] == "billing"
    assert messages.kwargs["output_config"]["format"]["schema"] is LLM_OUTPUT_SCHEMA
    assert messages.kwargs["fallbacks"] == "default"


def test_anthropic_skips_fallback_for_models_without_it():
    p, messages = provider_with(message(), model="claude-haiku-5-5")
    p.classify("sys", "user", LLM_OUTPUT_SCHEMA)
    assert "fallbacks" not in messages.kwargs


@pytest.mark.parametrize("stop_reason", ["refusal", "max_tokens"])
def test_anthropic_unusable_stop_reasons(stop_reason):
    p, _ = provider_with(message(stop_reason=stop_reason))
    with pytest.raises(ProviderOutputError):
        p.classify("sys", "user", LLM_OUTPUT_SCHEMA)


def test_anthropic_rate_limit_is_unavailable():
    req = httpx.Request("POST", "https://api.anthropic.com/v1/messages")
    err = anthropic.RateLimitError(
        "slow down", response=httpx.Response(429, request=req), body=None
    )
    p, _ = provider_with(err)
    with pytest.raises(ProviderUnavailable):
        p.classify("sys", "user", LLM_OUTPUT_SCHEMA)

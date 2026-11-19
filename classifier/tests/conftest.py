import pytest
from fastapi.testclient import TestClient

from app.config import Settings, get_settings
from app.main import app, get_provider, get_store
from app.providers.fake import FakeProvider
from app.store import Store

API_KEY = "test-key"


class ScriptedProvider:
    """Returns (or raises) queued responses in order, and counts calls."""

    name = "scripted"
    model = "scripted-1"

    def __init__(self, *responses):
        self.responses = list(responses)
        self.calls = 0

    def classify(self, system, user, schema):
        self.calls += 1
        item = self.responses.pop(0) if len(self.responses) > 1 else self.responses[0]
        if isinstance(item, Exception):
            raise item
        return item


def good(category="billing", priority="normal", confidence=0.95):
    return {
        "category": category,
        "priority": priority,
        "confidence": confidence,
        "summary": "Customer asks about a charge.",
        "rationale": "Mentions an invoice.",
    }


@pytest.fixture
def settings(tmp_path):
    return Settings(
        llm_provider="fake",
        classifier_api_key=API_KEY,
        db_path=str(tmp_path / "test.db"),
        confidence_threshold=0.75,
        max_body_chars=200,
        _env_file=None,
    )


@pytest.fixture
def store(settings):
    # Lease 0 by default so redelivery tests see "classified"; lease tests build their own.
    return Store(settings.db_path, settings.claim_ttl_seconds, lease_seconds=0)


@pytest.fixture
def make_client(settings, store):
    def _make(provider=None):
        app.dependency_overrides[get_settings] = lambda: settings
        app.dependency_overrides[get_store] = lambda: store
        app.dependency_overrides[get_provider] = lambda: provider or FakeProvider()
        client = TestClient(app)
        client.headers["X-API-Key"] = API_KEY
        return client

    yield _make
    app.dependency_overrides.clear()


def email(message_id="msg-1", subject="Question about my invoice", body="I was charged twice."):
    return {
        "message_id": message_id,
        "sender": "jane@customer.com",
        "subject": subject,
        "body": body,
    }

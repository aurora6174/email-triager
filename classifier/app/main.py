import hmac
import json
import logging
from functools import lru_cache
from typing import Annotated

from fastapi import Depends, FastAPI, Header, HTTPException, Path
from fastapi.responses import JSONResponse

from app.config import Settings, get_settings
from app.providers import (
    LLMProvider,
    ProviderConfigError,
    ProviderUnavailable,
    build_provider,
)
from app.schema import AckResponse, ClassifyResponse, EmailIn
from app.service import ClassificationInProgress, IdempotencyConflict, classify_email
from app.store import Store


class JsonFormatter(logging.Formatter):
    """One JSON object per line. Email bodies are never logged (PII)."""

    _skip = set(logging.makeLogRecord({}).__dict__) | {"message", "asctime"}

    def format(self, record: logging.LogRecord) -> str:
        data = {"level": record.levelname, "logger": record.name, "msg": record.getMessage()}
        data.update({k: v for k, v in record.__dict__.items() if k not in self._skip})
        return json.dumps(data, default=str)


_handler = logging.StreamHandler()
_handler.setFormatter(JsonFormatter())
logging.getLogger("classifier").addHandler(_handler)
logging.getLogger("classifier").setLevel(logging.INFO)
logging.getLogger("classifier").propagate = False

app = FastAPI(
    title="Email triage classifier",
    version="1.0.0",
    description="Classifies an email with an LLM and decides its route. Called by n8n.",
)


@lru_cache
def get_store() -> Store:
    s = get_settings()
    return Store(s.db_path, s.claim_ttl_seconds, s.delivery_lease_seconds)


@lru_cache
def get_provider() -> LLMProvider:
    return build_provider(get_settings())


def require_api_key(
    settings: Annotated[Settings, Depends(get_settings)],
    x_api_key: Annotated[str | None, Header()] = None,
) -> None:
    expected = settings.classifier_api_key.get_secret_value()
    if not expected:
        return
    if not x_api_key or not hmac.compare_digest(x_api_key, expected):
        raise HTTPException(status_code=401, detail="invalid or missing X-API-Key")


Auth = Depends(require_api_key)


@app.get("/healthz")
def healthz(settings: Annotated[Settings, Depends(get_settings)]) -> dict:
    return {"ok": True, "provider": settings.llm_provider}


@app.post(
    "/v1/classify",
    response_model=ClassifyResponse,
    dependencies=[Auth],
    responses={
        409: {"description": "Same message is being classified right now; retry shortly."},
        422: {"description": "message_id reused with different content, or invalid body."},
        500: {"description": "LLM provider misconfigured (key, model)."},
        503: {"description": "LLM provider unavailable after retries; retry later."},
    },
)
def classify(
    email: EmailIn,
    provider: Annotated[LLMProvider, Depends(get_provider)],
    store: Annotated[Store, Depends(get_store)],
    settings: Annotated[Settings, Depends(get_settings)],
):
    try:
        return classify_email(email, provider, store, settings)
    except ClassificationInProgress:
        return JSONResponse(
            status_code=409,
            content={"detail": "classification in progress"},
            headers={"Retry-After": "5"},
        )
    except IdempotencyConflict:
        return JSONResponse(
            status_code=422,
            content={"detail": "message_id was already used for different content"},
        )
    except ProviderUnavailable as e:
        logging.getLogger("classifier").warning("provider unavailable: %s", e)
        return JSONResponse(
            status_code=503,
            content={"detail": f"LLM provider unavailable: {e}"},
            headers={"Retry-After": "30"},
        )
    except ProviderConfigError as e:
        logging.getLogger("classifier").error("provider config error: %s", e)
        return JSONResponse(status_code=500, content={"detail": f"LLM provider error: {e}"})


@app.post("/v1/messages/{message_id}/ack", response_model=AckResponse, dependencies=[Auth])
def ack(
    message_id: Annotated[str, Path(max_length=512)],
    store: Annotated[Store, Depends(get_store)],
):
    """n8n calls this after every destination succeeded. Idempotent."""
    previous = store.mark_routed(message_id)
    if previous is None:
        raise HTTPException(status_code=404, detail="unknown message_id")
    if previous == "processing":
        raise HTTPException(status_code=409, detail="message is still being classified")
    return AckResponse(message_id=message_id, status="routed")


@app.get("/v1/messages/{message_id}", response_model=ClassifyResponse, dependencies=[Auth])
def get_message(
    message_id: Annotated[str, Path(max_length=512)],
    store: Annotated[Store, Depends(get_store)],
):
    stored = store.get(message_id)
    if stored is None or stored.result_json is None:
        raise HTTPException(status_code=404, detail="unknown message_id")
    result = ClassifyResponse.model_validate_json(stored.result_json)
    return result.model_copy(update={"status": stored.status})

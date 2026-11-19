"""Classification pipeline: idempotency claim -> LLM -> validation -> review rules -> routing."""

import hashlib
import json
import logging
import re
import time

from pydantic import ValidationError

from app.config import Settings
from app.prompt import PROMPT_VERSION, SYSTEM_PROMPT, build_user_message
from app.providers import LLMProvider, ProviderOutputError
from app.schema import (
    LLM_OUTPUT_SCHEMA,
    Category,
    Classification,
    ClassifyResponse,
    EmailIn,
    Priority,
    Route,
)
from app.store import ClaimOutcome, Store

log = logging.getLogger("classifier")

# The LLM labels; this table decides where things go. Changing a destination is a code
# review, not a prompt tweak, and the model can never invent a route.
ROUTE_BY_CATEGORY: dict[Category, Route] = {
    Category.BILLING: Route.BILLING,
    Category.TECHNICAL_SUPPORT: Route.SUPPORT_TICKET,
    Category.ACCOUNT_ACCESS: Route.SUPPORT_TICKET,
    Category.SALES_LEAD: Route.CRM,
    Category.FEEDBACK: Route.FEEDBACK_LOG,
    Category.SPAM: Route.ARCHIVE,
    Category.OTHER: Route.HUMAN_REVIEW,
}

# Cheap second opinion on prompt injection. It does not block anything; it only forces
# a human to look, because a successful injection could have bent the label.
_INJECTION_PATTERNS = re.compile(
    r"ignore (all |any )?(previous|prior|above) (instructions|prompts)"
    r"|you are now|system prompt|disregard (the|your) (rules|instructions)"
    r"|classify this (email )?as",
    re.IGNORECASE,
)

# Output errors are retried once before giving up and sending the email to a human.
OUTPUT_ATTEMPTS = 2


class IdempotencyConflict(Exception):
    pass


class ClassificationInProgress(Exception):
    pass


def content_hash(email: EmailIn) -> str:
    payload = json.dumps([email.sender, email.subject, email.body], ensure_ascii=False)
    return hashlib.sha256(payload.encode()).hexdigest()


def _fallback(reason: str) -> Classification:
    return Classification(
        category=Category.OTHER,
        priority=Priority.NORMAL,
        confidence=0.0,
        summary="Automatic classification failed; needs a human.",
        rationale=reason[:500],
    )


def _call_llm(provider: LLMProvider, user_msg: str) -> tuple[Classification, list[str]]:
    """Returns the classification plus any review reasons caused by the LLM step."""
    last_error = ""
    for attempt in range(1, OUTPUT_ATTEMPTS + 1):
        try:
            raw = provider.classify(SYSTEM_PROMPT, user_msg, LLM_OUTPUT_SCHEMA)
            return Classification.model_validate(raw), []
        except (ProviderOutputError, ValidationError) as e:
            last_error = f"{type(e).__name__}: {str(e).splitlines()[0]}"
            log.warning("llm output unusable (attempt %d): %s", attempt, last_error)
    return _fallback(last_error), ["llm_output_invalid"]
    # ProviderUnavailable / ProviderConfigError propagate to the API layer.


def classify_email(
    email: EmailIn, provider: LLMProvider, store: Store, settings: Settings
) -> ClassifyResponse:
    started = time.monotonic()
    digest = content_hash(email)
    outcome, stored = store.claim(email.message_id, digest)

    if outcome is ClaimOutcome.CONFLICT:
        raise IdempotencyConflict(email.message_id)
    if outcome is ClaimOutcome.IN_PROGRESS:
        raise ClassificationInProgress(email.message_id)
    if outcome in (ClaimOutcome.EXISTING, ClaimOutcome.DELIVERING):
        assert stored is not None and stored.result_json is not None
        previous = ClassifyResponse.model_validate_json(stored.result_json)
        status = "delivering" if outcome is ClaimOutcome.DELIVERING else stored.status
        return previous.model_copy(update={"duplicate": True, "status": status})

    # We own the claim. Any exception must release it so a retry can proceed.
    try:
        response = _classify_new(email, provider, settings, started)
    except BaseException:
        store.release(email.message_id)
        raise
    store.complete(email.message_id, response.model_dump_json())
    log.info(
        "classified",
        extra={
            "message_id": email.message_id,
            "category": response.classification.category.value,
            "priority": response.classification.priority.value,
            "confidence": response.classification.confidence,
            "route": response.route.value,
            "review_reasons": response.review_reasons,
            "latency_ms": response.latency_ms,
        },
    )
    return response


def _classify_new(
    email: EmailIn, provider: LLMProvider, settings: Settings, started: float
) -> ClassifyResponse:
    review_reasons: list[str] = []
    body = email.body
    truncated = len(body) > settings.max_body_chars
    if truncated:
        body = body[: settings.max_body_chars]

    if not email.subject and not email.body:
        classification = _fallback("Empty subject and body; nothing to classify.")
        review_reasons.append("empty_message")
    else:
        user_msg = build_user_message(email.sender, email.subject, body)
        classification, llm_reasons = _call_llm(provider, user_msg)
        review_reasons += llm_reasons

    if _INJECTION_PATTERNS.search(f"{email.subject}\n{email.body}"):
        review_reasons.append("possible_prompt_injection")
    if not review_reasons:
        if classification.confidence < settings.confidence_threshold:
            review_reasons.append("low_confidence")
        if classification.category is Category.OTHER:
            review_reasons.append("category_other")

    needs_review = bool(review_reasons)
    route = Route.HUMAN_REVIEW if needs_review else ROUTE_BY_CATEGORY[classification.category]
    notify = (
        classification.priority in (Priority.URGENT, Priority.HIGH) and route is not Route.ARCHIVE
    )

    return ClassifyResponse(
        message_id=email.message_id,
        status="classified",
        duplicate=False,
        classification=classification,
        route=route,
        notify=notify,
        needs_review=needs_review,
        review_reasons=review_reasons,
        body_truncated=truncated,
        provider=provider.name,
        model=provider.model,
        prompt_version=PROMPT_VERSION,
        latency_ms=int((time.monotonic() - started) * 1000),
    )

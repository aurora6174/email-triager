"""Data contracts.

There are two layers on purpose:

* ``LLM_OUTPUT_SCHEMA`` is the JSON Schema sent to the provider (structured outputs).
  It stays inside the subset both OpenAI strict mode and Anthropic structured outputs
  accept: no numeric bounds, every property required, ``additionalProperties: false``.
* ``Classification`` is the Pydantic model we validate the provider's answer against.
  It adds the constraints the provider schema cannot express (e.g. 0 <= confidence <= 1).

``tests/test_schema.py`` keeps the two in sync.
"""

from datetime import datetime
from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class Category(StrEnum):
    BILLING = "billing"
    TECHNICAL_SUPPORT = "technical_support"
    ACCOUNT_ACCESS = "account_access"
    SALES_LEAD = "sales_lead"
    FEEDBACK = "feedback"
    SPAM = "spam"
    OTHER = "other"


class Priority(StrEnum):
    LOW = "low"
    NORMAL = "normal"
    HIGH = "high"
    URGENT = "urgent"


class Route(StrEnum):
    """Where the n8n workflow sends the message. Decided by code, never by the LLM."""

    SUPPORT_TICKET = "support_ticket"
    BILLING = "billing"
    CRM = "crm"
    FEEDBACK_LOG = "feedback_log"
    ARCHIVE = "archive"
    HUMAN_REVIEW = "human_review"


class Classification(BaseModel):
    model_config = ConfigDict(extra="forbid")

    category: Category
    priority: Priority
    confidence: float = Field(ge=0.0, le=1.0)
    summary: str = Field(min_length=1, max_length=500)
    rationale: str = Field(min_length=1, max_length=500)


LLM_OUTPUT_SCHEMA: dict = {
    "type": "object",
    "properties": {
        "category": {"type": "string", "enum": [c.value for c in Category]},
        "priority": {"type": "string", "enum": [p.value for p in Priority]},
        "confidence": {
            "type": "number",
            "description": "Your confidence in the category, from 0.0 to 1.0.",
        },
        "summary": {
            "type": "string",
            "description": "One sentence describing what the sender wants.",
        },
        "rationale": {
            "type": "string",
            "description": "One short sentence on why this category and priority.",
        },
    },
    "required": ["category", "priority", "confidence", "summary", "rationale"],
    "additionalProperties": False,
}


class EmailIn(BaseModel):
    """What n8n sends. ``message_id`` doubles as the idempotency key."""

    model_config = ConfigDict(extra="ignore", str_strip_whitespace=True)

    message_id: str = Field(min_length=1, max_length=512)
    sender: str = Field(min_length=1, max_length=512)
    subject: str = Field(default="", max_length=2000)
    body: str = Field(default="", max_length=500_000)
    received_at: datetime | None = None


class ClassifyResponse(BaseModel):
    message_id: str
    # classified: deliver it. delivering: another run is delivering it right now, stop.
    # routed: already delivered, stop.
    status: Literal["classified", "delivering", "routed"]
    duplicate: bool
    classification: Classification
    route: Route
    notify: bool = Field(description="True when a human should be pinged now (urgent/high).")
    needs_review: bool
    review_reasons: list[str]
    body_truncated: bool
    provider: str
    model: str
    prompt_version: str
    latency_ms: int


class AckResponse(BaseModel):
    message_id: str
    status: Literal["routed"]

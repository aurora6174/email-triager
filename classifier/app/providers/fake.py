"""Deterministic keyword classifier. Not a real model: it lets the full n8n pipeline and the
test-suite run offline, with no API key and no cost. Set LLM_PROVIDER=fake."""

import re

_RULES: list[tuple[str, str]] = [
    (r"unsubscribe|seo|backlink|guest post|crypto|wire transfer|ignore (all|previous)", "spam"),
    (r"log ?in|password|2fa|locked out|sso|reset link", "account_access"),
    (r"invoice|refund|charged|billing|receipt|payment|vat", "billing"),
    (r"pricing|demo|quote|enterprise plan|seats|partnership|purchase", "sales_lead"),
    (r"error|bug|crash|broken|not working|500|timeout|down", "technical_support"),
    (r"feature request|would be nice|love the|suggestion|wish", "feedback"),
]


class FakeProvider:
    name = "fake"
    model = "keyword-rules-v1"

    def classify(self, system: str, user: str, schema: dict) -> dict:
        text = user.lower()
        category, confidence = "other", 0.4
        for pattern, label in _RULES:
            if re.search(pattern, text):
                category, confidence = label, 0.9
                break
        if re.search(r"production (is )?down|outage|data loss|security", text):
            priority = "urgent"
        elif category in ("technical_support", "account_access"):
            priority = "high"
        elif category in ("spam", "feedback"):
            priority = "low"
        else:
            priority = "normal"
        return {
            "category": category,
            "priority": priority,
            "confidence": confidence,
            "summary": "Classified by keyword rules (fake provider).",
            "rationale": f"Matched rules for {category}.",
        }

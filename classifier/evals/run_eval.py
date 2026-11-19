"""Measure the classifier on a labeled set, with the provider configured in .env.

    uv run python -m evals.run_eval                    # uses LLM_PROVIDER from .env
    LLM_PROVIDER=openai uv run python -m evals.run_eval

This calls the real LLM once per example, so it costs money (30 short emails is cents).

Metrics that matter for triage:
* auto-route precision: of the emails that skipped human review, how many were right.
  This is the number that protects customers; the threshold trades it against
* review rate: the share of emails a human has to look at.
"""

import json
import sys
import tempfile
from collections import Counter
from pathlib import Path

from app.config import get_settings
from app.providers import build_provider
from app.schema import EmailIn
from app.service import classify_email
from app.store import Store

DATASET = Path(__file__).with_name("dataset.jsonl")


def main() -> int:
    settings = get_settings()
    provider = build_provider(settings)
    rows = [json.loads(line) for line in DATASET.read_text().splitlines() if line.strip()]

    with tempfile.TemporaryDirectory() as tmp:
        store = Store(f"{tmp}/eval.db", settings.claim_ttl_seconds, settings.delivery_lease_seconds)
        results = []
        for row in rows:
            email = EmailIn(
                message_id=row["id"], **{k: row[k] for k in ("sender", "subject", "body")}
            )
            r = classify_email(email, provider, store, settings)
            got = r.classification.category.value
            results.append((row, r, got == row["expected"]))
            mark = "ok " if got == row["expected"] else "XX "
            review = f" -> review {r.review_reasons}" if r.needs_review else ""
            print(
                f"{mark}{row['id']} expected={row['expected']:<18} got={got:<18}"
                f" conf={r.classification.confidence:.2f} {r.latency_ms}ms{review}"
            )

    n = len(results)
    correct = sum(ok for _, _, ok in results)
    auto = [(row, r, ok) for row, r, ok in results if not r.needs_review]
    auto_correct = sum(ok for _, _, ok in auto)
    confusions = Counter(
        (row["expected"], r.classification.category.value) for row, r, ok in results if not ok
    )

    print(f"\nprovider={provider.name} model={provider.model}")
    print(f"confidence threshold: {settings.confidence_threshold}")
    print(f"category accuracy:    {correct}/{n} = {correct / n:.0%}")
    print(f"review rate:          {n - len(auto)}/{n} = {(n - len(auto)) / n:.0%}")
    if auto:
        print(f"auto-route precision: {auto_correct}/{len(auto)} = {auto_correct / len(auto):.0%}")
    for (exp, got), count in confusions.most_common():
        print(f"  confused {exp} -> {got}: {count}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

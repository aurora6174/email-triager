#!/usr/bin/env python3
"""Post sample emails to the n8n webhook, for demos and smoke tests (stdlib only).

    python3 scripts/send_samples.py              # all 30 samples from the eval set
    python3 scripts/send_samples.py e05 e13 e23  # just these ids
    python3 scripts/send_samples.py --repeat e05 # send twice to show deduplication

Reads WEBHOOK_SECRET from the environment or from .env.
"""

import json
import os
import sys
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
URL = os.environ.get("TRIAGE_WEBHOOK_URL", "http://localhost:5678/webhook/email-triage")


def secret() -> str:
    if os.environ.get("WEBHOOK_SECRET"):
        return os.environ["WEBHOOK_SECRET"]
    for line in (ROOT / ".env").read_text().splitlines():
        if line.startswith("WEBHOOK_SECRET="):
            return line.split("=", 1)[1].strip()
    sys.exit("WEBHOOK_SECRET not set")


def main() -> None:
    args = sys.argv[1:]
    repeat = "--repeat" in args
    ids = {a for a in args if not a.startswith("--")}
    rows = [
        json.loads(line)
        for line in (ROOT / "classifier/evals/dataset.jsonl").read_text().splitlines()
        if line.strip()
    ]
    key = secret()
    for row in rows:
        if ids and row["id"] not in ids:
            continue
        payload = {
            "message_id": f"sample-{row['id']}",
            "sender": row["sender"],
            "subject": row["subject"],
            "body": row["body"],
        }
        for _ in range(2 if repeat else 1):
            req = urllib.request.Request(
                URL,
                data=json.dumps(payload).encode(),
                headers={"Content-Type": "application/json", "X-Webhook-Secret": key},
                method="POST",
            )
            try:
                with urllib.request.urlopen(req, timeout=10) as resp:
                    status = resp.status
            except urllib.error.HTTPError as e:
                status = e.code
            print(f"{row['id']} {status} {row['subject']}")


if __name__ == "__main__":
    main()

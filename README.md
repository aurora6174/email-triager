# Email Triager

An n8n workflow that reads incoming support email, classifies it with an LLM (Claude or
OpenAI), and routes it to the right place: tickets, billing, CRM leads, feedback, spam
archive, or a **human-review queue when the model isn't sure**. It's built to be
reliable: deliveries are idempotent, failures are retried, and anything that still fails
lands in a dead-letter queue with an alert.

> Demo video: _coming soon_ · Design write-up: [docs/DESIGN.md](docs/DESIGN.md) · Illustrated PDF (18 pages, 9 diagrams): [docs/DESIGN.pdf](docs/DESIGN.pdf)

## The problem it solves

A shared `support@` inbox mixes outages, refund requests, locked-out users, sales leads,
feature ideas and spam. Sorting it by hand is slow, and urgent issues wait behind
newsletters. This pipeline labels every email within seconds, files it in the right place,
pings Slack for urgent ones, and sends anything ambiguous to a person instead of guessing.

## How it works

```
Webhook / Gmail ─► Normalize ─► Classify ──────────► Delivered / delivering? ─► stop
                                  │  (FastAPI service:
                                  │   idempotency, LLM w/ structured output,
                                  │   validation, review rules, routing table)
                                  ▼
                         Route ─┬─ support_ticket ─► Sheet: Tickets ──┐
                                ├─ billing ────────► Sheet: Billing ──┤
                                ├─ crm ────────────► Sheet: Leads ────┤
                                ├─ feedback_log ───► Sheet: Feedback ─┼─► Ack ─► urgent? ─► Slack
                                ├─ human_review ───► Sheet: Review + Slack
                                └─ archive (spam) ────────────────────┘
Any failure ─► Error workflow ─► Sheet: DeadLetter + Slack alert (replay safely from n8n)
```

1. **Trigger:** a webhook (authenticated with a header secret, answers `202` right away)
   or the Gmail Trigger.
2. **Classify:** n8n calls the classifier service. The service returns a category,
   priority, confidence, a one-line summary and a rationale, all **schema-enforced** by
   the provider's structured outputs and re-validated with Pydantic.
3. **Decide:** code, not the LLM, picks the route. Low confidence, the `other` category,
   suspected prompt injection, or unusable LLM output all go to **human review**.
4. **Deliver:** the email is appended to the matching Google Sheet tab, which stands in
   for Zendesk or HubSpot. Then n8n **acks** it and pings Slack if it's urgent.
5. **Recover:** retries happen at three levels (LLM SDK, n8n HTTP nodes, Sheets nodes).
   Anything still failing goes to the error workflow, which writes a dead-letter row and
   sends a Slack alert. Retrying a failed run is safe, because the classifier never bills
   or delivers the same email twice.

Each step is explained in detail, with the reasoning behind it, in
[docs/DESIGN.md](docs/DESIGN.md).

## Design decisions (summary)

| Decision | Why |
|---|---|
| **n8n** (self-hosted) over Make/Zapier | Email stays on your own infrastructure, real code nodes, error workflows, workflows versioned as JSON in git, no per-task pricing. Make would suit a SaaS-only client. |
| **A separate Python classifier** instead of n8n's AI nodes | The prompt, schema and rules are unit-tested and evaluated. The provider is swappable through env. Atomic idempotency. Costs one extra container. |
| **Structured outputs** instead of asking for JSON in the prompt | Constrained decoding means the output always parses and always uses a valid label. Pydantic adds the bounds the schema can't express. |
| **The LLM labels, code routes** | Prompt changes or prompt injection can't create new actions. Routing is reviewable and tested. |
| **Confidence threshold plus review queue** | Self-reported confidence is only roughly calibrated, so the threshold is tuned on an eval set (auto-route precision vs review rate). |
| **Idempotency by `message_id`**, with `classified` → `routed` states and a delivery lease | Redeliveries are free and never double-routed, even when two copies arrive at once. A run that failed mid-way still finishes delivery on retry. |
| **Dead-letter sheet** instead of a queue | Enough for inbox volume. A queue is the upgrade path. |
| **SQLite store** | One replica, zero extra infrastructure. Postgres is the upgrade for more than one replica. |

## Run it

### Prerequisites
Docker with Compose, plus [uv](https://docs.astral.sh/uv/) to run the tests locally.

### 1. Quick start, offline (no API keys)
```bash
cp .env.example .env
# Set these four in .env (generate secrets with: openssl rand -hex 32):
#   LLM_PROVIDER=fake   CLASSIFIER_API_KEY=...   N8N_ENCRYPTION_KEY=...   WEBHOOK_SECRET=...
docker compose up --build
python3 scripts/send_samples.py e20 e23    # a spam email and an injection attempt
```
`fake` is a keyword-rules classifier: the whole pipeline runs with no cost. Open n8n at
<http://localhost:5678> (create the owner account on first visit) and look at
**Executions**.

### 2. With a real LLM
In `.env`, set `LLM_PROVIDER=anthropic` and `ANTHROPIC_API_KEY`, or `LLM_PROVIDER=openai`
and `OPENAI_API_KEY`. Then run `docker compose up -d --build classifier`.

### 3. Destinations
- **Slack:** create an [incoming webhook](https://api.slack.com/messaging/webhooks) and set
  `SLACK_WEBHOOK_URL`.
- **Google Sheets:**
  1. Create a spreadsheet with the tabs `Tickets`, `Billing`, `Leads`, `Feedback`,
     `Review` and `DeadLetter`, and put its ID in `GOOGLE_SHEET_ID`.
  2. Create an OAuth client in Google Cloud with the redirect URI
     `http://localhost:5678/rest/oauth2-credential/callback`, and set
     `GOOGLE_CLIENT_ID` and `GOOGLE_CLIENT_SECRET` **before the first start**.
  3. In n8n → Credentials → **Google Sheets (triage)**, click *Sign in with Google*. Every
     Sheets node already points at this credential.
  4. Run `docker compose run --rm n8n-bootstrap` to publish the workflows.
- **Gmail (optional):** enable the *Gmail Trigger* node in the workflow and connect a
  Gmail credential.

### Webhook contract
```bash
curl -X POST http://localhost:5678/webhook/email-triage \
  -H "X-Webhook-Secret: $WEBHOOK_SECRET" -H "Content-Type: application/json" \
  -d '{"message_id":"<unique id>","sender":"jane@acme.com","subject":"Refund","body":"..."}'
# → 202 {"message":"Workflow was started"}
```

### Tests and evals
```bash
cd classifier
uv run pytest                    # 28 tests, offline, under 2 s
uv run python -m evals.run_eval  # 30 labeled emails against the configured LLM (costs cents)
```
The eval reports category accuracy, **review rate**, and **auto-route precision**: of the
emails that skipped review, the share that were right. Use it to tune
`CONFIDENCE_THRESHOLD`. Baseline: the keyword-rules provider gets 67% accuracy.

## Project layout
```
classifier/            FastAPI service (Python 3.13, uv)
  app/schema.py        data contracts and the JSON schema sent to the LLM
  app/prompt.py        versioned system prompt
  app/service.py       pipeline: claim → LLM → validate → review rules → route
  app/store.py         SQLite idempotency state machine
  app/providers/       anthropic, openai, fake
  tests/               pytest suite
  evals/               labeled dataset + eval runner
n8n/workflows/         email-triage.json, error-handler.json (imported at startup)
n8n/scripts/           bootstrap: credentials + import + publish
scripts/send_samples.py  demo and smoke-test sender
docs/DESIGN.md         detailed process breakdown and decisions
docs/DESIGN.pdf        illustrated version (rebuild: docs/pdf/build.sh)
```

## Known limitations
- A single classifier replica (SQLite store). Use Postgres to run more than one.
- Confidence is self-reported by the model. Calibrate the threshold on real traffic.
- The prompt and eval set are English-only. Attachments and email threads are not
  considered.
- Corrections made in the review queue aren't fed back into the eval set yet.
- The Gmail trigger path is wired up but hasn't been tested end to end. The demo uses the
  webhook.
- Workflow edits made in the n8n UI are overwritten on the next start, because git is the
  source of truth. Export them back with
  `docker compose exec n8n n8n export:workflow --id=emailTriageMain1 --output=/tmp/wf.json`.

See [docs/DESIGN.md §10](docs/DESIGN.md#10-known-limitations-and-next-steps) for the next steps.

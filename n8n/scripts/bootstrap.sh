#!/bin/sh
# One-shot setup, run by the n8n-bootstrap service each time the stack starts.
#  1. Imports the webhook-auth credential, generated from WEBHOOK_SECRET (never in git).
#  2. On first run only, creates an empty Google Sheets OAuth credential so every Sheets
#     node is already wired to it; you finish it with one "Sign in with Google" click.
#  3. Imports both workflows from ./n8n/workflows (git is the source of truth).
#  4. Publishes them. This fails until step 2 is completed; that is reported, not fatal.
set -eu

if [ -z "${WEBHOOK_SECRET:-}" ]; then
  echo "WEBHOOK_SECRET is not set; refusing to expose an unauthenticated webhook." >&2
  exit 1
fi

cat > /tmp/webhook-credential.json <<JSON
[{
  "id": "triageWebhookAu1",
  "name": "Triage webhook secret",
  "type": "httpHeaderAuth",
  "data": { "name": "X-Webhook-Secret", "value": "${WEBHOOK_SECRET}" }
}]
JSON
n8n import:credentials --input=/tmp/webhook-credential.json
rm -f /tmp/webhook-credential.json

MARKER=/home/node/.n8n/.triage-google-credential-created
if [ ! -f "$MARKER" ]; then
  cat > /tmp/google-credential.json <<JSON
[{
  "id": "triageGoogleShee",
  "name": "Google Sheets (triage)",
  "type": "googleSheetsOAuth2Api",
  "data": { "clientId": "${GOOGLE_CLIENT_ID:-}", "clientSecret": "${GOOGLE_CLIENT_SECRET:-}" }
}]
JSON
  n8n import:credentials --input=/tmp/google-credential.json
  rm -f /tmp/google-credential.json
  touch "$MARKER"
fi

n8n import:workflow --separate --input=/workflows

for id in emailTriageErr01 emailTriageMain1; do
  if ! n8n publish:workflow --id="$id"; then
    echo "NOTE: workflow $id is imported but not published yet." >&2
    echo "      Open http://localhost:5678, finish the 'Google Sheets (triage)' credential," >&2
    echo "      then publish the workflow (or run: docker compose run --rm n8n-bootstrap)." >&2
  fi
done
echo "bootstrap done"

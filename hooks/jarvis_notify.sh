#!/usr/bin/env bash
# Claude Code "Notification" hook: reads the hook JSON payload from stdin and
# POSTs it to the local Jarvis daemon. spec_lines 56.
set -euo pipefail

payload="$(cat)"
session_id="$(jq -r '.session_id' <<<"$payload")"
cwd="$(jq -r '.cwd' <<<"$payload")"
message="$(jq -r '.message' <<<"$payload")"

body="$(jq -n \
  --arg session_id "$session_id" \
  --arg cwd "$cwd" \
  --arg message "$message" \
  '{session_id: $session_id, cwd: $cwd, event: "notification", message: $message}')"

curl -s -o /dev/null -X POST \
  -H "Content-Type: application/json" \
  -d "$body" \
  "http://127.0.0.1:${JARVIS_PORT:-8000}/events"

#!/usr/bin/env bash
# Claude Code "Stop" hook: reads the hook JSON payload from stdin, pulls the
# last assistant message out of the transcript JSONL, and POSTs it to the
# local Jarvis daemon. spec_lines 55.
set -euo pipefail

payload="$(cat)"
session_id="$(jq -r '.session_id' <<<"$payload")"
cwd="$(jq -r '.cwd' <<<"$payload")"
transcript_path="$(jq -r '.transcript_path' <<<"$payload")"

message="$(jq -r -s '
  map(select(.type == "assistant"))
  | last
  | .message.content
  | map(select(.type == "text") | .text)
  | last
' "$transcript_path")"

body="$(jq -n \
  --arg session_id "$session_id" \
  --arg cwd "$cwd" \
  --arg message "$message" \
  '{session_id: $session_id, cwd: $cwd, event: "stop", message: $message}')"

curl -s -o /dev/null -X POST \
  -H "Content-Type: application/json" \
  -d "$body" \
  "http://127.0.0.1:${JARVIS_PORT}/events"

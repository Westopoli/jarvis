# Groq cloud LLM backend

Jarvis's brain defaults to local Ollama (`qwen3:8b`). Setting
`JARVIS_LLM_PROVIDER=groq` in `.env` switches it to Groq's cloud API
(`openai/gpt-oss-120b` by default), with automatic failover back to Ollama
if Groq errors mid-call. See `docs/research/` for why: shared-GPU
contention with other local workloads was causing 20-30s stalls; Groq's
specialized inference hardware sidesteps that entirely, at negligible cost
next to the Twilio call bill.

This is config-only: pick a provider, restart the server. No voice command
switches it mid-call.

## Setup

1. `console.groq.com` → API Keys → create one.
2. In `.env`:
   ```
   JARVIS_LLM_PROVIDER=groq
   GROQ_API_KEY=gsk_...
   ```
   (`GROQ_MODEL` defaults to `openai/gpt-oss-120b`; override if you want a
   different Groq-hosted model.)
3. Restart: `uv run jarvis` (server) or `uv run python -m jarvis.run_desktop`.

Leaving `JARVIS_LLM_PROVIDER` unset, or `ollama`, is unchanged from before
this feature existed — no new dependency, no behavior change.

## What happens on a Groq error

Any error from Groq (timeout, connection failure, 5xx, rate limit) fails
the call over to local Ollama immediately, without dropping the call — see
`jarvis/llm_providers.py`'s `_FailoverGroqLLMService` for why this needed a
small wrapper rather than "just working" with Pipecat's stock
`ServiceSwitcherStrategyFailover`. Once failed over, a call stays on Ollama
for its remainder; the next call starts fresh on Groq again.

## Desktop A/B verification

The offline test suite (`make test`) covers config plumbing, dispatch, the
prompt's honesty about which backend is active, and the failover mechanism
itself against a real running pipeline (synthetic errors, no network). What
it can't cover is "does Groq actually behave the same as Ollama in a real
conversation" — that needs a live `GROQ_API_KEY` and is not part of CI.

### 1. Run the same scripted conversation against both backends

```bash
JARVIS_LLM_PROVIDER=ollama uv run python -m jarvis.run_desktop \
  --no-server \
  --say "jarvis, what tabs are open" \
  --say "jarvis, switch to tab one" \
  --say "jarvis, find the tab where we talked about the phone setup" \
  --say "jarvis, update me" \
  --say "jarvis, tell it to run the tests" \
  --say "send" \
  --say "jarvis, how's it going today" \
  2>&1 | tee /tmp/jarvis_ollama_run.log

JARVIS_LLM_PROVIDER=groq uv run python -m jarvis.run_desktop \
  --no-server \
  --say "jarvis, what tabs are open" \
  --say "jarvis, switch to tab one" \
  --say "jarvis, find the tab where we talked about the phone setup" \
  --say "jarvis, update me" \
  --say "jarvis, tell it to run the tests" \
  --say "send" \
  --say "jarvis, how's it going today" \
  2>&1 | tee /tmp/jarvis_groq_run.log
```

`JARVIS_LLM_PROVIDER=...` on the command line overrides `.env` for that one
run. Adjust "tab one" / "the phone setup" to a real tab number and topic
that exist in your own tmux session — both runs must reference the *same*
real tab so the comparison is apples to apples; check the `list_tabs`
response from the first run before writing the rest of the script.

### 2. Compare the tool-routing sequence

```bash
uv run python scripts/compare_llm_runs.py /tmp/jarvis_ollama_run.log /tmp/jarvis_groq_run.log
```

Every turn should print `OK`. A mismatch means the two backends routed the
same spoken command to different tools — worth reading both transcripts
around that turn to see why.

### 3. Manual checks the script can't do

- Listen to (or read) the Groq run's answer to "how's it going today" —
  confirm it describes itself as running on Groq/cloud, not the old
  Ollama-only claim (`jarvis/prompt.py`'s `build_system_prompt`).
- Resilience (optional, live): mid-script, temporarily break Groq
  connectivity (bad `GROQ_API_KEY`, or block `api.groq.com`) for one turn,
  confirm the conversation continues and the next turn is answered — this
  exercises real `openai`-SDK exception types, which the offline suite's
  synthetic errors can't stand in for.

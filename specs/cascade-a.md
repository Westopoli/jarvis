# cascade-a

## Summary

Core logic for Jarvis, the voice interface to Claude Code tmux sessions, with no live services (no GPU, no network, no real tmux server in tests). Six leaves: tmux control tools, a conversation state machine, a narration reader/cursor, synthetic audio fixture generation plus a whisper-hallucination filter, an audio interrupt gate (VAD params + word-gating + wake word), and Claude Code hook ingest (event store + `/events` route).

## Acceptance criteria

### tmux tools (`jarvis/tools/tmux.py`)
1. `tmux_list()` returns one entry per tmux window in the target session, each with index, window name, pane path, and pane current command, parsed from `tmux list-windows -F '#I|#W|#{pane_current_path}|#{pane_current_command}'` output.
2. `resolve_tab(query)` resolves an integer-like string ("3") to that window index directly. For a non-numeric query it fuzzy-matches against window names and pane path basenames, returning the best match's index, or `None` if nothing scores above a minimum similarity threshold.
3. `tmux_read(tab, lines)` returns cleaned text captured via `tmux capture-pane -p -t <session>:<tab> -S -<lines>`: ANSI escape codes stripped, box-drawing border characters stripped, spinner/status-bar chrome lines stripped (lines matching known Claude Code TUI spinner glyphs or a leading `─`/`│`/`╭`/`╰` box character).
4. `tmux_send(tab, text, confirmed)` raises `PermissionError` (does not call `tmux send-keys`) when `confirmed` is not `True`.
5. `tmux_send(tab, text, confirmed=True)` raises `PermissionError` when the target pane's `pane_current_command` is not `claude` or `node`, unless called with `force=True`.
6. `tmux_send(tab, text, confirmed=True)` on an allowed pane calls `tmux send-keys -t <session>:<tab> -l <text>` followed by a separate `tmux send-keys -t <session>:<tab> Enter`.
7. All tmux tools take a `session` parameter (default from `JARVIS_TMUX_SESSION` env var, default `"main"`) so tests can point at a throwaway `tmux -L jarvis-test` server instead of the user's real session.

### Conversation state machine (`jarvis/conversation.py`)
8. `Conversation` starts in state `IDLE`.
9. States are `IDLE`, `LISTENING`, `NARRATING`, `DRAFTING`, `CONFIRMING`, `SMALLTALK`, matching the table below.

   | State | Mic policy | Exit |
   |---|---|---|
   | IDLE | wake word required, or open window after Jarvis asked a question | speech event → LISTENING |
   | LISTENING | normal VAD end-of-turn | transcript event → dispatched by `llm_intent` to NARRATING, DRAFTING, or SMALLTALK |
   | NARRATING | word-gated + wake/addressed gate | gated interrupt → LISTENING; narration_end event → IDLE |
   | DRAFTING | accumulates transcript fragments; "scratch that" clears accumulator; "done"/"send" ends | → CONFIRMING |
   | CONFIRMING | expects yes/no/send/edit | "send"/"yes" → emits `SendPrompt(tab, text)`, then → IDLE (or NARRATING if a narration was interrupted to enter DRAFTING); anything else → re-asks once, then reverts to prior state on a second miss |
   | SMALLTALK | no tools callable | command-intent event or "back to work" → IDLE |
10. A transcript event in NARRATING whose `words` count is below the gate's minimum, or that carries no wake word / not-addressed classification, does not cause a state transition (narration continues uninterrupted).
11. Three consecutive transcript fragments accumulated in DRAFTING are concatenated (in arrival order, whitespace-joined) into a single pending draft string.
12. "scratch that" as a DRAFTING transcript clears the accumulated draft back to empty, staying in DRAFTING.
13. A "send" transcript in CONFIRMING emits a `SendPrompt(tab=<active tab>, text=<draft>)` event object.

### Narration reader (`jarvis/reader.py`)
14. `Reader(text)` splits `text` into sentences (splitting on `.`, `!`, `?` followed by whitespace, preserving the terminator) and starts its cursor at sentence 0.
15. `Reader.next()` returns the sentence at the cursor and advances the cursor; returns `None` once past the last sentence.
16. `Reader.resume()` returns the sentence at the current cursor position (the one last returned by `next()`, i.e. does not re-advance past where narration was interrupted).
17. `Reader.skip()` advances the cursor by one sentence without returning it.
18. `Reader.done` is `True` once the cursor has passed the last sentence.

### Audio fixtures + hallucination filter (`tests/fixtures/gen_audio.py`, `jarvis/audio/hallucination.py`)
19. `gen_audio.py` provides functions to synthesize, as numpy arrays / WAV files under `tests/fixtures/audio/`: white noise, pink noise, and a mix of a clean speech fixture with noise at a specified SNR in dB.
20. `is_hallucination(segment)` returns `True` for a whisper transcription segment whose `no_speech_prob > 0.6` and `avg_logprob < -1.0`, or whose text matches a known hallucination string ("thank you for watching", case-insensitive substring match, among others in a documented list). Returns `False` otherwise.

### Interrupt gate (`jarvis/audio/gate.py`)
21. `InterruptGate` is constructed with VAD params (`confidence`, `start_secs`, `stop_secs`, `min_volume`), a minimum word count for interruption, and a wake-word detector callable.
22. Given a simulated honk-only audio segment (fixture from A4), the gate reports no interrupt.
23. Given a segment transcribing to fewer words than the configured minimum, the gate reports no interrupt regardless of VAD activity.
24. Given a segment transcribing to at least the minimum words, spoken while the conversation is in NARRATING, and containing neither the wake word nor an "addressed to me" flag, the gate reports no interrupt.
25. Given a segment transcribing to at least the minimum words and containing the wake word, spoken while NARRATING, the gate reports an interrupt.
26. Given a segment flagged as a whisper hallucination (per AC 20), the gate reports no interrupt even if word count and VAD would otherwise pass.

### Claude Code hook ingest (`hooks/jarvis_stop.sh`, `hooks/jarvis_notify.sh`, `jarvis/events.py`, `jarvis/server.py`)
27. `hooks/jarvis_stop.sh` reads a Claude Code `Stop` hook JSON payload from stdin, extracts the last `type == "assistant"` entry's message text from the JSONL file at the payload's `transcript_path`, and POSTs a JSON body `{"session_id", "cwd", "event": "stop", "message"}` to `http://127.0.0.1:$JARVIS_PORT/events`.
28. `hooks/jarvis_notify.sh` reads a Claude Code `Notification` hook JSON payload from stdin and POSTs `{"session_id", "cwd", "event": "notification", "message"}` to the same `/events` endpoint.
29. `EventStore.record(payload)` stores the event keyed by `session_id`, with fields `{cwd, last_message, pending_permission, ts}`; a `notification` event whose message text matches a permission-prompt pattern sets `pending_permission` to that message text, and any `stop` event clears `pending_permission`.
30. `EventStore.tab_for_session(session_id, windows)` maps a stored session to a tmux window index by matching the event's `cwd` against each window's pane path (exact match preferred, then longest-prefix match); returns `None` when no window's path matches.
31. `POST /events` (FastAPI route, bound to `127.0.0.1` only) accepts the hook JSON body, calls `EventStore.record`, and returns HTTP 200 with an empty body.
32. `claude_summary(tab)` returns the `EventStore`'s `last_message` for the session mapped to that tab if present; otherwise falls back to `tmux_read(tab, 200)` cleaned per AC 3.

## Inputs / Outputs / Constraints / Out of scope

- Inputs: tmux window/pane state (via `tmux` CLI, real or `-L jarvis-test` fixture server), synthetic/recorded audio fixtures, Claude Code hook JSON payloads (stdin) and sample transcript JSONL fixtures.
- Outputs: parsed tab lists, cleaned pane text, `SendPrompt` events, interrupt booleans, HTTP responses from `/events`.
- Constraints: no GPU, no network calls beyond localhost HTTP in tests, no dependency on a live Ollama or TTS service in this cascade. Real audio ML models (Silero VAD, whisper) may be used in the interrupt-gate tests but run on CPU against short fixtures (seconds, not minutes).
- Out of scope for this cascade: LLM orchestration and intent classification (cascade B), the assembled Pipecat pipeline (cascade B), Telnyx/phone transport and Orpheus TTS (cascade C).

## Scale & Boundary Profile

- **N typical / N peak:** a tmux session has 2-10 windows typical, ~30 peak (`tmux_list`); a narrated message is 1-20 sentences typical, ~200 peak (`Reader`); the event store holds one entry per concurrently-active Claude Code session, single digits typical, ~20 peak.
- **Growth claim per hot path:** `tmux_list` parsing is `linear_ish` in window count. `Reader` sentence splitting is `linear_ish` in text length. `EventStore.tab_for_session` matching is `linear_ish` in window count (no repeated re-scans per lookup).
- **Memory posture:** bounded-buffer — `EventStore` keeps only the latest event per session (no unbounded history), `tmux_read` truncates to the requested line count.
- **External call budget:** one `tmux` subprocess invocation per tool call (no per-window subprocess calls inside `tmux_list`'s loop).

## Bible Compliance

- **Bible path:** `/home/westopoli/.claude/plans/i-ve-got-this-idea-zany-harp.md`
- **Sections referenced:** Architecture; Noise robustness (layers 3-5, Conversation state machine table); Voice interaction model; Process (Cascade A leaf table); Slices 1-4.
- **Deliberate divergences:** DeepFilterNet (optional denoise processor, plan section "Noise robustness" item 2) is deferred out of Cascade A — it is explicitly optional/flag-gated in the plan and adds a heavy CPU-audio dependency; `InterruptGate` (AC 21-26) is built without it. Wake-word model loading (openwakeword) is exercised via a fake/stub detector callable in this cascade's tests rather than the real ONNX model, to keep tests fast and offline; the real detector wiring happens when the desktop pipeline is assembled (cascade B, slice 6).

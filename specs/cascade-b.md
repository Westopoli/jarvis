# cascade-b

## Summary

LLM orchestration and the desktop audio pipeline for Jarvis: the system prompt and tool schemas the LLM uses to call Jarvis's tools, an intent classifier + draft cleaner running against a real local Ollama model, pipeline assembly wiring cascade A's `Conversation`/`InterruptGate`/`Reader` into a Pipecat pipeline behind a swappable transport (a fake transport for tests, a real local-audio transport for the desktop smoke check), and a text-only REPL driver for exercising the whole loop without audio.

## Acceptance criteria

### System prompt + tool registry (`jarvis/prompt.py`, `jarvis/tools/registry.py`)
1. `jarvis.prompt.SYSTEM_PROMPT` is a non-empty string containing, at minimum, the substrings `"Jarvis"` and `"tmux"`, and states the confirmation rule in some form (contains `"confirm"` case-insensitively) — the persona/self-model/tone content itself is prose, not machine-checked line by line.
2. `jarvis.tools.registry.TOOL_SCHEMAS` is a list of dicts, each shaped `{"type": "function", "function": {"name": str, "description": str, "parameters": {"type": "object", "properties": {...}, "required": [...]}}}` (OpenAI-style function-calling schema, which Ollama's `/api/chat` endpoint also accepts), covering at least these five tool names: `"tmux_list"`, `"switch_tab"`, `"claude_summary"`, `"send_prompt"`, `"resume_reading"`.
3. `jarvis.tools.registry.dispatch(name: str, arguments: dict, session_state) -> Any` routes each schema name to a real cascade-A/cascade-B callable: `"tmux_list"` -> `jarvis.tools.tmux.tmux_list`; `"switch_tab"` -> `jarvis.tools.tmux.resolve_tab` on `arguments["query"]`, updating `session_state.active_tab` on a non-`None` result; `"claude_summary"` -> `jarvis.events.claude_summary` for `session_state.active_tab`; `"send_prompt"` -> raises `PermissionError` unless `arguments.get("confirmed") is True`, otherwise calls `jarvis.tools.tmux.tmux_send(session_state.active_tab, arguments["text"], confirmed=True)`; `"resume_reading"` -> calls `.resume()` on `session_state.reader` if one is set, else returns `None`.
4. `dispatch` raises `KeyError` for a tool name not in `TOOL_SCHEMAS`.

### Intent classifier + draft cleaner + filler (`jarvis/intent.py`)
5. `classify_intent(transcript: str, model: str = "qwen3:8b") -> str` calls the local Ollama chat API at temperature 0 and returns one of the exact strings `Conversation.llm_intent` already accepts: `"narrate"`, `"draft"`, `"smalltalk"`, `"command"`. On the fixed transcript set `{"what tabs are open": "command", "tab three": "command", "update me on this chat": "command", "tell it to use pydantic instead of dataclasses": "draft", "what are you": "smalltalk", "how do you work": "smalltalk"}`, `classify_intent` returns the mapped value for every entry.
6. `clean_draft(raw: str, model: str = "qwen3:8b") -> str` calls the local Ollama chat API at temperature 0 to merge fragments and drop whisper-hallucination artifacts from an accumulated DRAFTING transcript. Given a raw draft containing the phrase "thank you for watching" as a stray fragment (e.g. `"use pydantic thank you for watching instead of dataclasses"`), the cleaned result does not contain that phrase (case-insensitive) and does contain both `"pydantic"` and `"dataclasses"`.
7. `filler_for(tool_name: str) -> str` returns a short, non-empty, deterministic (no LLM call) filler phrase for at least the five tool names in AC-2's `TOOL_SCHEMAS`, distinct per tool name (e.g. mentioning the tab number is not required, but the phrase must differ across at least 3 of the 5 tools — a single constant for every tool fails this).

### Pipeline assembly + fake transport harness (`jarvis/pipeline.py`, `tests/harness/fake_transport.py`)
8. `tests/harness/fake_transport.py` provides a fake Pipecat-compatible transport/processor pair sufficient to drive a `pipecat.pipeline.pipeline.Pipeline` end-to-end in a test: it can push input frames (e.g. pre-transcribed text frames, standing in for STT output on replayed fixture audio) into the pipeline and capture every TTS-bound text frame the pipeline emits, in emission order, into a list the test can assert on.
9. `jarvis.pipeline.build_pipeline(transport, conversation, reader, interrupt_gate, llm_model="qwen3:8b") -> pipecat.pipeline.pipeline.Pipeline` returns a real `Pipeline` instance wiring `transport`'s input/output through cascade A's `conversation`/`reader`/`interrupt_gate` objects (the exact internal processor shape is this leaf's own design — no other leaf or the umbrella test inspects `Pipeline` internals, only its externally observable frame behavior).
10. Given the fake transport from AC-8 feeding a sequence of transcript-text frames that drive `Conversation` from IDLE through NARRATING (a canned narration text) to an interrupt back to LISTENING, the pipeline built by `build_pipeline` emits at least one TTS-bound text frame from the narration before the interrupt frame arrives, and stops emitting narration text after it.

### Text REPL driver (`jarvis/repl.py`)
11. `jarvis.repl.run_turn(text: str, session_state) -> str` takes one line of typed input (standing in for a spoken transcript), classifies its intent (AC-5), dispatches through the tool registry (AC-3) when the intent is `"command"`, and returns the text Jarvis would have spoken in response (a tool result summary, a filler+result, or a draft read-back) — never raises for any of the six fixed transcripts in AC-5.
12. `session_state` (a plain object or dataclass local to this leaf) carries at least `active_tab: int | None` and `reader: Reader | None`, matching AC-3's dispatch expectations.

## Inputs / Outputs / Constraints / Out of scope

- Inputs: typed/pre-transcribed text (no real STT in this cascade's automated tests), a running local Ollama instance serving `qwen3:8b`, cascade A's `Conversation`/`Reader`/`InterruptGate`/tmux tools/event store.
- Outputs: tool-call results, spoken-text strings, an assembled Pipecat `Pipeline` object, TTS-bound text frames observed via the fake transport.
- Constraints: no real microphone/speaker I/O in this cascade (`pipecat-ai[local]`'s `LocalAudioTransport`, which needs the `audio-local` extra and the `portaudio19-dev` system package, is exercised only by this cascade's manual desktop smoke check, never by an automated test). AC-5/AC-6 tests are integration tests (`@pytest.mark.slow`) against a real Ollama endpoint at temperature 0 — no network calls beyond `localhost`.
- Out of scope for this cascade: Telnyx/phone transport, Orpheus TTS, deploy config (cascade C).

## Scale & Boundary Profile

- **N typical / N peak:** `TOOL_SCHEMAS` has exactly 5 entries (fixed, not data-dependent). A pipeline run processes 1-50 frames typical, ~500 peak per call (bounded by call duration, not by this cascade's code).
- **Growth claim per hot path:** `dispatch`'s name lookup is `sublinear` (dict/direct-branch lookup, not a linear scan proportional to schema count). No other hot path in this cascade has a data-dependent growth claim worth asserting — `unbounded-unknown — assert growth only, no absolutes` for pipeline frame handling, since frame count is caller-driven, not a function of an internal data structure this leaf controls.
- **Memory posture:** bounded-buffer — the fake transport's captured-frames list and any pipeline-internal buffering are bounded by call duration, not accumulated across calls.
- **External call budget:** `classify_intent` and `clean_draft` each make exactly one Ollama chat call per invocation (no retry loop, no per-word call).

## Bible Compliance

- **Bible path:** `/home/westopoli/.claude/plans/i-ve-got-this-idea-zany-harp.md`
- **Sections referenced:** Architecture; Latency target (filler masking); Voice interaction model; Persona and small talk; Process (Cascade B leaf table); Slices 5-6.
- **Deliberate divergences:** The plan's slice 6 wires `LocalAudioTransport`, `faster-whisper`, and `Kokoro` into the pipeline directly; this cascade's automated tests instead exercise `build_pipeline` through the fake transport harness (AC-8-10) with pre-transcribed text frames standing in for STT/TTS audio, per this project's `audio-local` extra split (`pyaudio`/`portaudio19-dev` not available in the automated-test environment). Wiring the real `LocalAudioTransport` + `faster-whisper` + Kokoro is the cascade's manual desktop smoke check, done by hand after the automated leaves admit, not encoded as a leaf AC. `MinWordsInterruptionStrategy` / `allow_interruptions=True` (Pipecat `PipelineParams`) are set by `build_pipeline` per the plan but are not independently asserted by a leaf test beyond AC-10's observable behavior (Pipecat's own interruption-strategy machinery is third-party code, not this cascade's to re-test).

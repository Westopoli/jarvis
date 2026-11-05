# Jarvis

Voice interface to Claude Code sessions running in tmux. Say "Jarvis" from the desk, or call it from the car, and ask what your agents are doing, have it read a tab back to you, dictate a prompt, confirm, and send.

Built spec-first: three written specs, each run as a [manager-mode](https://github.com/Westopoli/claude-manager-mode) cascade, with the tests landing before the implementation. 226 test functions across unit, audio, and integration suites; the automated suite runs with no GPU, no network, and no real tmux server.

## What it does

- **Lists and switches tabs.** Fuzzy-matches window names and pane paths, so "switch to the profile tab" works.
- **Reads a session aloud.** Captures the pane, strips ANSI and TUI chrome, and narrates only what is new since the last read.
- **Drafts and sends prompts.** Dictated text is cleaned by the LLM, read back, and sent only after an explicit "send". Sends are refused unless the target pane is actually running Claude Code.
- **Announces events.** A Claude Code `Notification` hook POSTs to the local daemon, so Jarvis speaks up when an agent finishes or needs input.
- **Answers the phone.** Twilio or Telnyx route over a Tailscale Funnel, with a caller allow-list that fails closed when unconfigured.

## Architecture

```
mic / phone ──► Pipecat transport ──► VAD + wake word + interrupt gate
                                            │
                                   Whisper STT (faster-whisper)
                                            │
                              Conversation state machine
                   IDLE · LISTENING · NARRATING · DRAFTING · CONFIRMING · SMALLTALK
                                            │
                          LLM intent + tool calls ──► tmux tools
                      (Ollama qwen3:8b, Groq / DeepSeek failover)
                                            │
                                 TTS (Kokoro or Orpheus) ──► speaker / phone
```

- `jarvis/conversation.py` holds the state machine. Each state sets its own mic policy, so the wake word is required when idle but not after Jarvis asks a question.
- `jarvis/audio/` holds the interrupt gate, a Whisper hallucination filter, and mute handling.
- `jarvis/tools/tmux.py` is the only thing that touches tmux. Every tool takes a session argument, so tests run against a throwaway `tmux -L jarvis-test` server.
- `jarvis/llm_providers.py` defaults to local Ollama and fails over to a cloud provider mid-call on error. See `docs/groq-backend.md` for why.
- `jarvis/server.py` hosts `/events`, `/health`, and the Twilio and Telnyx TwiML and WebSocket routes.
- `jarvis/transports/` swaps between a fake transport for tests, local desktop audio, and the phone providers.

## Running it

```bash
cp .env.example .env            # fill in what you use; everything has a local default
make sync                       # uv sync with audio, server, and dev extras
make test                       # unit + audio suites, no hardware needed

# desktop voice loop (needs portaudio and a CUDA-capable GPU for Whisper)
uv sync --extra audio-local
uv run python -m jarvis.run_desktop

# same loop, no microphone
uv run python -m jarvis.run_desktop --say "jarvis, what tabs are open"

# text-only REPL
uv run python -m jarvis.repl
```

Phone setup lives in `docs/phone.md`. systemd units for the daemon and the Tailscale Funnel are in `deploy/`. The Claude Code hook script is in `hooks/`.

## Layout

```
jarvis/      package: pipeline, conversation, intent, tools, audio, transports, tts
specs/       cascade-a, -b, -c: the written specs each build ran against
tests/       unit, audio, integration, plus the umbrella tests that gate each cascade
docs/        phone setup, Groq backend rationale, latency and diarization research
deploy/      systemd units
hooks/       Claude Code hook scripts
```

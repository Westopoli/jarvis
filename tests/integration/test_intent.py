# spec: specs/cascade-b.md::Acceptance criteria::AC-5..7
"""Tests for the intent classifier, draft cleaner and filler (leaf-02, spec_lines 15-18).

Two flavours of test live here:

* ``@pytest.mark.slow`` tests that call the REAL local Ollama instance serving
  ``qwen3:8b`` at temperature 0 (spec_lines 16-17, 33).  These are the
  goal-fidelity tests: the classifier's job is to be right about real speech,
  which no stub can demonstrate.
* Fast tests that point the module at a throwaway loopback ``http.server``
  via ``OLLAMA_HOST`` (the override the leaf brief names) to pin the parts of
  the request that are contractual rather than model-dependent: temperature 0
  and the one-call-per-invocation external call budget (spec_lines 41).  This
  is HTTP-client agnostic on purpose -- which library the leaf reaches for is
  its own business.
"""
from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from jarvis.intent import classify_intent, clean_draft, filler_for

VOCABULARY = {"narrate", "draft", "smalltalk", "command"}

# spec_lines 16 -- the fixed transcript table, in full.
FIXED_TRANSCRIPTS = [
    ("what tabs are open", "command"),
    ("tab three", "command"),
    ("update me on this chat", "command"),
    ("tell it to use pydantic instead of dataclasses", "draft"),
    ("what are you", "smalltalk"),
    ("how do you work", "smalltalk"),
]

TOOL_NAMES = [
    "tmux_list",
    "switch_tab",
    "claude_summary",
    "send_prompt",
    "resume_reading",
]


# ---------------------------------------------------------------------------
# stub Ollama endpoint (fast tests)
# ---------------------------------------------------------------------------

@pytest.fixture
def stub_ollama(monkeypatch):
    """A loopback ``/api/chat`` endpoint recording every request it receives."""
    requests: list[dict] = []
    reply = {"content": "command"}

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):  # noqa: N802 - http.server API
            length = int(self.headers.get("Content-Length") or 0)
            raw = self.rfile.read(length)
            try:
                body = json.loads(raw.decode("utf-8"))
            except ValueError:
                body = {}
            requests.append({"path": self.path, "body": body})
            # One JSON object on one line: parses both as a whole-body JSON
            # response and as a single-line NDJSON stream.
            payload = json.dumps(
                {
                    "model": body.get("model", "qwen3:8b"),
                    "created_at": "2026-01-01T00:00:00Z",
                    "message": {"role": "assistant", "content": reply["content"]},
                    "done": True,
                    "done_reason": "stop",
                }
            ).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

        def log_message(self, *args):  # silence stderr noise
            return

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    host, port = server.server_address[0], server.server_address[1]
    monkeypatch.setenv("OLLAMA_HOST", f"http://{host}:{port}")
    try:
        yield {"requests": requests, "reply": reply}
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def _temperature(body: dict):
    """Temperature wherever the Ollama chat API accepts it."""
    options = body.get("options") or {}
    return options.get("temperature", body.get("temperature"))


# ---------------------------------------------------------------------------
# AC-5 -- classify_intent (spec_lines 16)
# ---------------------------------------------------------------------------

@pytest.mark.slow
@pytest.mark.parametrize(
    ("transcript", "expected"),
    FIXED_TRANSCRIPTS,
    ids=[t.replace(" ", "-") for t, _ in FIXED_TRANSCRIPTS],
)
def test_classify_intent_maps_every_fixed_transcript(transcript, expected):
    # spec_lines 16
    result = classify_intent(transcript)
    assert result in VOCABULARY
    assert result == expected


@pytest.mark.slow
def test_classify_intent_stays_in_vocabulary_for_an_empty_transcript():
    """Existence boundary: empty input still yields one of the four tokens.

    Spec is silent on WHICH label an empty transcript gets (see BOUNDARIES.md);
    the vocabulary constraint of spec_lines 16 still binds.
    """
    assert classify_intent("") in VOCABULARY


def test_classify_intent_uses_temperature_zero_and_one_chat_call(stub_ollama):
    # spec_lines 16 (temperature 0), spec_lines 41 (one chat call per call)
    classify_intent("what tabs are open")

    calls = stub_ollama["requests"]
    assert len(calls) == 1
    assert calls[0]["path"].endswith("/api/chat")
    assert _temperature(calls[0]["body"]) == 0


def test_classify_intent_passes_the_requested_model_through(stub_ollama):
    """Reference boundary: the ``model`` argument must reach the API."""
    # spec_lines 16
    classify_intent("what tabs are open", model="qwen3:8b")
    assert stub_ollama["requests"][0]["body"].get("model") == "qwen3:8b"


@pytest.mark.parametrize(
    "raw_reply",
    ["command", "  command\n", '"command"', "command.", "<think>hmm</think>command"],
    ids=["plain", "whitespace", "quoted", "punctuated", "thinking-block"],
)
def test_classify_intent_parses_the_reply_down_to_a_bare_token(stub_ollama, raw_reply):
    """Conformance boundary: reply decoration must be stripped (spec_lines 16)."""
    stub_ollama["reply"]["content"] = raw_reply
    assert classify_intent("what tabs are open") == "command"


# ---------------------------------------------------------------------------
# AC-6 -- clean_draft (spec_lines 17)
# ---------------------------------------------------------------------------

@pytest.mark.slow
def test_clean_draft_drops_the_whisper_hallucination_fragment():
    # spec_lines 17
    cleaned = clean_draft("use pydantic thank you for watching instead of dataclasses")

    assert isinstance(cleaned, str) and cleaned.strip()
    assert "thank you for watching" not in cleaned.lower()
    assert "pydantic" in cleaned.lower()
    assert "dataclasses" in cleaned.lower()


def test_clean_draft_uses_temperature_zero_and_one_chat_call(stub_ollama):
    # spec_lines 17 (temperature 0), spec_lines 41 (one chat call per call)
    stub_ollama["reply"]["content"] = "use pydantic instead of dataclasses"
    clean_draft("use pydantic thank you for watching instead of dataclasses")

    calls = stub_ollama["requests"]
    assert len(calls) == 1
    assert _temperature(calls[0]["body"]) == 0


# ---------------------------------------------------------------------------
# AC-7 -- filler_for (spec_lines 18)
# ---------------------------------------------------------------------------

def test_filler_for_is_non_empty_deterministic_and_makes_no_network_call(stub_ollama):
    # spec_lines 18
    for name in TOOL_NAMES:
        first = filler_for(name)
        assert isinstance(first, str) and first.strip()
        assert filler_for(name) == first

    assert stub_ollama["requests"] == []


def test_filler_for_differs_across_at_least_three_of_the_five_tools():
    """Cardinality boundary: a single constant for every tool fails (spec_lines 18)."""
    fillers = {filler_for(name) for name in TOOL_NAMES}
    assert len(fillers) >= 3

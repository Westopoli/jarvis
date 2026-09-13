"""Intent classifier, draft cleaner and tool filler (spec_lines 15-18, AC-5..7).

``classify_intent`` and ``clean_draft`` call a local Ollama ``/api/chat``
endpoint at temperature 0 (one call per invocation, spec_lines 41).
``filler_for`` is a pure, network-free lookup (spec_lines 18).
"""
from __future__ import annotations

import json
import os
import re
import urllib.request

_DEFAULT_HOST = "http://127.0.0.1:11434"
_VOCABULARY = ("command", "draft", "narrate", "smalltalk")

_THINK_RE = re.compile(r"<think>.*?</think>", re.IGNORECASE | re.DOTALL)


def _ollama_host() -> str:
    return os.environ.get("OLLAMA_HOST", _DEFAULT_HOST).rstrip("/")


def _chat(model: str, system: str, user: str) -> str:
    """One /api/chat call at temperature 0; returns the raw reply content."""
    url = f"{_ollama_host()}/api/chat"
    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
        "stream": False,
        "options": {"temperature": 0},
        # qwen3 otherwise emits a <think> block before the one-word answer,
        # adding seconds of latency to every classification.
        "think": False,
    }
    data = json.dumps(payload).encode("utf-8")
    request = urllib.request.Request(
        url,
        data=data,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=120) as response:
        body = response.read().decode("utf-8")
    lines = [line for line in body.splitlines() if line.strip()]
    reply = {}
    for line in lines:
        try:
            reply = json.loads(line)
        except ValueError:
            continue
    return reply.get("message", {}).get("content", "")


def _strip_decoration(text: str) -> str:
    text = _THINK_RE.sub("", text)
    return text.strip().strip("\"'").strip().rstrip(".").strip()


_CLASSIFY_SYSTEM = """You are the intent classifier inside Jarvis, a hands-free \
voice assistant that lets a driver control tmux sessions running Claude Code. \
Classify the driver's spoken transcript into exactly one category:

- command: an operational request aimed at Jarvis or a tmux/Claude session \
itself -- listing open tabs/windows, switching to a tab, asking what is \
happening in a session, or asking for a status update/summary of a chat. \
The driver is talking ABOUT the session, not giving Claude new work to do.
- draft: the driver is dictating content that Claude should receive as a \
message/prompt/instruction -- this includes phrases like "tell it to...", \
"tell claude to...", "ask it to...", "say that...", or directly speaking \
the instruction/code/wording that should be sent. Even though these \
sentences are phrased as an instruction, the driver is composing the BODY \
of a message for Claude to act on, not asking Jarvis to operate the \
session. Example: "tell it to use pydantic instead of dataclasses" is \
draft, because the driver is dictating what Claude should be told, not \
asking Jarvis to look at or switch sessions.
- narrate: asking Jarvis to read text aloud, or to continue/resume reading.
- smalltalk: casual conversation, chit-chat, or questions about Jarvis \
itself that are not about operating a tmux/Claude session.

Reply with exactly one lowercase word and nothing else: command, draft, \
narrate, or smalltalk."""


def classify_intent(transcript: str, model: str = "qwen3:8b") -> str:
    reply = _chat(model, _CLASSIFY_SYSTEM, transcript)
    text = _strip_decoration(reply).lower()
    if text in _VOCABULARY:
        return text
    for word in _VOCABULARY:
        if word in text:
            return word
    return "command"


_CLEAN_SYSTEM = """You clean up a raw speech-to-text transcript of a driver \
dictating a message that will be sent to Claude. Merge sentence fragments \
into one fluent instruction and remove filler/hallucination artifacts that \
a whisper-style transcriber sometimes appends -- stray phrases such as \
"thank you for watching" that the driver did not actually say. Preserve \
every substantive word and technical term (library names, identifiers, \
etc.) from the input. Reply with ONLY the cleaned text: no quotes, no \
preamble, no explanation."""


def clean_draft(raw: str, model: str = "qwen3:8b") -> str:
    reply = _chat(model, _CLEAN_SYSTEM, raw)
    return _strip_decoration(reply)


_FILLERS = {
    "tmux_list": "Checking your open tabs...",
    "switch_tab": "Switching over...",
    "claude_summary": "Pulling up the summary...",
    "send_prompt": "Sending that along...",
    "resume_reading": "Picking back up...",
}
_DEFAULT_FILLER = "One moment..."


def filler_for(tool_name: str) -> str:
    return _FILLERS.get(tool_name, _DEFAULT_FILLER)

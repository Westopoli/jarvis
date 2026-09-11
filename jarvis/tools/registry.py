# spec: specs/cascade-b.md::Acceptance criteria::AC-2..4
"""OpenAI-function-calling tool schemas + dispatch for Jarvis (leaf-01,
spec_lines 11-13).

``TOOL_SCHEMAS`` describes the five tools the LLM may call, in the
OpenAI/Ollama function-calling shape. ``dispatch`` routes a tool call by
name to the real cascade-A/cascade-B callable that implements it, threading
per-conversation state (``session_state``) through where needed.
"""
from __future__ import annotations

from jarvis.events import claude_summary
from jarvis.tools.tmux import resolve_tab, tmux_list, tmux_send

TOOL_SCHEMAS: list[dict] = [
    {
        "type": "function",
        "function": {
            "name": "tmux_list",
            "description": "List the tmux windows in the current session, with their index, name, working directory, and running command.",
            "parameters": {
                "type": "object",
                "properties": {},
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "switch_tab",
            "description": "Switch the active tmux tab to the window that best fuzzy-matches a human description, such as its name or directory.",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "A human description of the tab to switch to, e.g. a window name or directory fragment.",
                    },
                },
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "claude_summary",
            "description": "Summarise the latest activity of the Claude Code session running in the active tmux tab.",
            "parameters": {
                "type": "object",
                "properties": {},
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "send_prompt",
            "description": "Type a prompt into the active tmux tab's pane. Requires explicit user confirmation.",
            "parameters": {
                "type": "object",
                "properties": {
                    "text": {
                        "type": "string",
                        "description": "The text to type into the pane.",
                    },
                    "confirmed": {
                        "type": "boolean",
                        "description": "Must be exactly true; the user has explicitly confirmed sending this prompt.",
                    },
                },
                "required": ["text", "confirmed"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "resume_reading",
            "description": "Resume narrating the current sentence of the active reader, without advancing past it.",
            "parameters": {
                "type": "object",
                "properties": {},
                "required": [],
            },
        },
    },
]


def _dispatch_tmux_list(arguments: dict, session_state) -> object:
    return tmux_list()


def _dispatch_switch_tab(arguments: dict, session_state) -> object:
    result = resolve_tab(arguments["query"])
    if result is not None:
        session_state.active_tab = result
    return result


def _dispatch_claude_summary(arguments: dict, session_state) -> object:
    return claude_summary(session_state.active_tab, session_state.event_store)


def _dispatch_send_prompt(arguments: dict, session_state) -> object:
    if arguments.get("confirmed") is not True:
        raise PermissionError("send_prompt requires arguments['confirmed'] is True")
    return tmux_send(session_state.active_tab, arguments["text"], confirmed=True)


def _dispatch_resume_reading(arguments: dict, session_state) -> object:
    if session_state.reader is None:
        return None
    return session_state.reader.resume()


_ROUTES = {
    "tmux_list": _dispatch_tmux_list,
    "switch_tab": _dispatch_switch_tab,
    "claude_summary": _dispatch_claude_summary,
    "send_prompt": _dispatch_send_prompt,
    "resume_reading": _dispatch_resume_reading,
}


def dispatch(name: str, arguments: dict, session_state) -> object:
    if name not in _ROUTES:
        raise KeyError(name)
    return _ROUTES[name](arguments, session_state)

# spec: specs/cascade-b.md::Acceptance criteria::AC-1..4
"""Tests for the system prompt and the tool registry (leaf-01, spec_lines 9-13).

Composition rule: ``dispatch`` is exercised against the REAL cascade-A
callables it routes to -- ``jarvis.tools.tmux.resolve_tab`` really runs its
``SequenceMatcher`` fuzzy match here, and ``jarvis.reader.Reader.resume`` is a
real reader.  Only the process boundary (the ``tmux`` subprocess wrappers and
``claude_summary``'s pane read) is patched, and it is patched at the point of
use inside ``jarvis.tools.registry`` -- the same pattern cascade A's own tests
use.
"""
from __future__ import annotations

from unittest.mock import patch

import pytest

from jarvis.prompt import SYSTEM_PROMPT
from jarvis.reader import Reader
from jarvis.tools.registry import TOOL_SCHEMAS, dispatch
from jarvis.types import SessionState, TmuxWindow

REQUIRED_TOOL_NAMES = {
    "tmux_list",
    "switch_tab",
    "claude_summary",
    "send_prompt",
    "resume_reading",
}

WINDOWS = [
    TmuxWindow(index=1, name="jarvis", pane_path="/home/w/jarvis", pane_command="claude"),
    TmuxWindow(index=2, name="api server", pane_path="/home/w/api", pane_command="node"),
]


# ---------------------------------------------------------------------------
# AC-1 -- system prompt (spec_lines 10)
# ---------------------------------------------------------------------------

def test_system_prompt_names_jarvis_tmux_and_states_the_confirmation_rule():
    # spec_lines 10
    assert isinstance(SYSTEM_PROMPT, str) and SYSTEM_PROMPT.strip()
    assert "Jarvis" in SYSTEM_PROMPT
    assert "tmux" in SYSTEM_PROMPT
    assert "confirm" in SYSTEM_PROMPT.lower()


# ---------------------------------------------------------------------------
# AC-2 -- tool schemas (spec_lines 11)
# ---------------------------------------------------------------------------

def test_tool_schemas_cover_the_five_required_names_in_openai_function_shape():
    # spec_lines 11
    assert isinstance(TOOL_SCHEMAS, list)
    names = [schema["function"]["name"] for schema in TOOL_SCHEMAS]
    assert REQUIRED_TOOL_NAMES.issubset(set(names))
    assert len(names) == len(set(names))
    # spec_lines 38: "TOOL_SCHEMAS has exactly 5 entries (fixed, not data-dependent)."
    assert len(TOOL_SCHEMAS) == 5

    for schema in TOOL_SCHEMAS:
        function = schema["function"]
        parameters = function["parameters"]
        assert schema["type"] == "function"
        assert isinstance(function["name"], str) and function["name"]
        assert isinstance(function["description"], str) and function["description"]
        assert parameters["type"] == "object"
        assert isinstance(parameters["properties"], dict)
        assert isinstance(parameters["required"], list)

    # Tie a schema's declared properties to the argument name dispatch
    # actually reads for that tool (spec_lines 12: arguments["query"]), so an
    # empty/mismatched `properties` dict cannot pass silently.
    schemas_by_name = {schema["function"]["name"]: schema["function"] for schema in TOOL_SCHEMAS}
    assert "query" in schemas_by_name["switch_tab"]["parameters"]["properties"]


# ---------------------------------------------------------------------------
# AC-3 -- dispatch routing (spec_lines 12)
# ---------------------------------------------------------------------------

def test_dispatch_tmux_list_calls_the_real_tmux_list_collaborator():
    """Interaction assertion: the routed collaborator is really called."""
    # spec_lines 12
    state = SessionState()
    with patch("jarvis.tools.registry.tmux_list", return_value=WINDOWS) as spy:
        result = dispatch("tmux_list", {}, state)

    assert spy.call_count == 1
    assert result == WINDOWS


def test_dispatch_switch_tab_runs_real_resolve_tab_and_updates_active_tab():
    """Interaction assertion: patch only the real point of use inside
    ``resolve_tab`` (``jarvis.tools.tmux.tmux_list``) -- a spy call proves
    the real fuzzy-matching ``resolve_tab`` actually ran end-to-end, not a
    dispatch-local reimplementation against a redundantly patched
    ``jarvis.tools.registry.tmux_list``."""
    # spec_lines 12
    state = SessionState(active_tab=1)
    with patch("jarvis.tools.tmux.tmux_list", return_value=WINDOWS) as spy:
        result = dispatch("switch_tab", {"query": "api server"}, state)

    assert (spy.call_count, result, state.active_tab) == (1, 2, 2)


def test_dispatch_switch_tab_leaves_active_tab_unchanged_when_nothing_matches():
    """Existence boundary: a ``None`` resolution must not clobber the tab."""
    # spec_lines 12 ("updating session_state.active_tab on a non-None result")
    state = SessionState(active_tab=7)
    with patch("jarvis.tools.tmux.tmux_list", return_value=WINDOWS) as spy:
        result = dispatch("switch_tab", {"query": "zzzzzzzzzzzz"}, state)

    assert (spy.call_count, result, state.active_tab) == (1, None, 7)


def test_dispatch_claude_summary_threads_session_states_own_event_store():
    """Composition assertion: the route must call the real
    ``claude_summary(session_state.active_tab, session_state.event_store)``
    -- not a fresh/throwaway store -- so a message recorded into
    ``state.event_store`` for the session mapped to the active tab comes
    back verbatim. ``claude_summary`` itself is real here; only its own
    process-boundary collaborators (``jarvis.events.tmux_list`` /
    ``tmux_read``) are patched at their point of use. (leaf-01.md:45-47)"""
    # spec_lines 12
    state = SessionState(active_tab=2)
    state.event_store.record(
        {
            "session_id": "abc",
            "cwd": "/home/w/api",
            "event": "stop",
            "message": "build passed",
        }
    )
    windows = [TmuxWindow(index=2, name="api server", pane_path="/home/w/api", pane_command="node")]

    with patch("jarvis.events.tmux_list", return_value=windows), patch(
        "jarvis.events.tmux_read"
    ) as read_spy:
        result = dispatch("claude_summary", {}, state)

    assert (result, read_spy.call_count) == ("build passed", 0)


@pytest.mark.parametrize(
    "arguments",
    [
        {"text": "run the tests"},
        {"text": "run the tests", "confirmed": False},
        {"text": "run the tests", "confirmed": "yes"},
        {"text": "run the tests", "confirmed": 1},
    ],
    ids=["missing", "false", "truthy-string", "truthy-int"],
)
def test_dispatch_send_prompt_requires_confirmed_is_exactly_true(arguments):
    """Conformance boundary: ``is True``, not merely truthy (spec_lines 12)."""
    state = SessionState(active_tab=2)
    with patch("jarvis.tools.registry.tmux_send") as spy:
        with pytest.raises(PermissionError):
            dispatch("send_prompt", arguments, state)

    assert spy.call_count == 0


def test_dispatch_send_prompt_confirmed_sends_to_the_active_tab():
    # spec_lines 12
    state = SessionState(active_tab=2)
    with patch("jarvis.tools.registry.tmux_send") as spy:
        dispatch("send_prompt", {"text": "run the tests", "confirmed": True}, state)

    assert spy.call_args.args[:2] == (2, "run the tests")
    assert spy.call_args.kwargs.get("confirmed") is True


def test_dispatch_resume_reading_calls_resume_on_the_real_reader():
    # spec_lines 12
    reader = Reader("First sentence. Second sentence.")
    reader.next()
    state = SessionState(active_tab=1, reader=reader)

    assert dispatch("resume_reading", {}, state) == "First sentence."


def test_dispatch_resume_reading_without_a_reader_returns_none():
    """Existence boundary: ``session_state.reader is None`` (spec_lines 12)."""
    assert dispatch("resume_reading", {}, SessionState()) is None


# ---------------------------------------------------------------------------
# AC-4 -- unknown tool names (spec_lines 13)
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    "name",
    ["delete_everything", "", "TMUX_LIST", "tmux_list "],
    ids=["unknown", "empty", "wrong-case", "trailing-space"],
)
def test_dispatch_raises_keyerror_for_a_name_not_in_tool_schemas(name):
    # spec_lines 13
    with pytest.raises(KeyError):
        dispatch(name, {}, SessionState())

# spec: specs/cascade-b.md::Acceptance criteria::AC-11..12
"""Tests for the text REPL driver (leaf-04, spec_lines 25-27).

``run_turn`` is exercised end to end: the intent classification is a REAL
call to the local Ollama instance serving ``qwen3:8b`` (spec_lines 26 routes
through AC-5), and the tool dispatch is the REAL ``jarvis.tools.registry``
composed with the REAL ``jarvis.tools.tmux.resolve_tab`` /
``jarvis.events.claude_summary``.  Only the ``tmux`` subprocess boundary is
mocked -- at every point of use -- so no tmux server is required.

The window fixture deliberately contains a window literally named ``three``:
that makes the "tab three" assertion tolerant of several plausible
``run_turn`` implementations -- forwarding the raw transcript, extracting
the word ``three``, or mapping the word to the digit ``3`` all resolve to
the same tab via the real ``resolve_tab`` (verified). This does NOT make the
assertion tolerant of every plausible normalization: a prefix-preserving
word-to-digit rewrite (e.g. turning "tab three" into "tab 3") does not
resolve, because the real ``resolve_tab`` (jarvis/tools/tmux.py) does no
number-word parsing and that rewritten string falls below its fuzzy-match
similarity threshold. ``run_turn`` is not expected to invent number-word
parsing beyond what ``resolve_tab`` already provides.
"""
from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from jarvis.repl import run_turn
from jarvis.tools.registry import dispatch as registry_dispatch
from jarvis.types import SessionState, TmuxWindow

WINDOWS = [
    TmuxWindow(index=1, name="jarvis", pane_path="/home/w/jarvis", pane_command="claude"),
    TmuxWindow(index=2, name="api", pane_path="/home/w/api", pane_command="node"),
    TmuxWindow(index=3, name="three", pane_path="/home/w/notes", pane_command="claude"),
]

PANE_TEXT = "All 41 tests passed. Waiting for your next instruction."

# spec_lines 16 -- the six fixed transcripts AC-11 says run_turn must survive.
FIXED_TRANSCRIPTS = [
    "what tabs are open",
    "tab three",
    "update me on this chat",
    "tell it to use pydantic instead of dataclasses",
    "what are you",
    "how do you work",
]


@pytest.fixture
def tmux_mocks():
    """Mock the tmux subprocess boundary at every point of use."""
    tmux_list = MagicMock(return_value=WINDOWS)
    tmux_read = MagicMock(return_value=PANE_TEXT)
    tmux_send = MagicMock(return_value=None)

    with patch("jarvis.tools.tmux.tmux_list", tmux_list), patch(
        "jarvis.tools.tmux.tmux_read", tmux_read
    ), patch(
        "jarvis.tools.tmux.tmux_send", tmux_send
    ), patch("jarvis.events.tmux_list", tmux_list), patch(
        "jarvis.events.tmux_read", tmux_read
    ), patch(
        "jarvis.tools.registry.tmux_list", tmux_list
    ), patch(
        "jarvis.tools.registry.tmux_send", tmux_send
    ):
        # NOTE: jarvis.tools.registry never imports tmux_read by that name
        # (leaf-01's brief routes only tmux_list, resolve_tab, tmux_send from
        # jarvis.tools.tmux, plus claude_summary from jarvis.events into the
        # registry namespace) -- the real point of use for the pane read is
        # jarvis.events.tmux_read, already patched above. A registry-level
        # tmux_read patch would just invent a nonexistent attribute.
        yield {"list": tmux_list, "read": tmux_read, "send": tmux_send}


def _speakable(reply):
    return isinstance(reply, str) and bool(reply.strip())


# ---------------------------------------------------------------------------
# AC-11 -- run_turn (spec_lines 26)
# ---------------------------------------------------------------------------

@pytest.mark.slow
@pytest.mark.parametrize(
    "transcript", FIXED_TRANSCRIPTS, ids=[t.replace(" ", "-") for t in FIXED_TRANSCRIPTS]
)
def test_run_turn_returns_speakable_text_and_never_raises(transcript, tmux_mocks):
    """Every one of AC-5's six transcripts, from a fresh (tab-less) state."""
    # spec_lines 26
    reply = run_turn(transcript, SessionState())
    assert _speakable(reply)


@pytest.mark.slow
def test_run_turn_dispatches_a_tab_listing_command_through_the_registry(tmux_mocks):
    """Interaction: a "command" intent really reaches the tool registry."""
    # spec_lines 26
    state = SessionState()
    reply = run_turn("what tabs are open", state)

    assert tmux_mocks["list"].call_count >= 1
    assert _speakable(reply)
    assert "tab" in reply.lower()


@pytest.mark.slow
def test_run_turn_dispatches_through_the_registrys_dispatch_function(tmux_mocks):
    """Direct, unambiguous proof of AC-11's "dispatches through the tool
    registry" clause.

    The tmux-level call-count checks (this file's other tests) cannot tell a
    registry-mediated call apart from an implementation that calls
    ``jarvis.tools.tmux.tmux_list`` directly, bypassing the registry -- the
    same mock object is installed at both names. This test instead spies on
    ``jarvis.repl.dispatch`` -- the name ``run_turn`` calls, per its own
    ``from jarvis.tools.registry import dispatch`` (leaf-04's brief) -- with
    ``wraps=`` the REAL ``jarvis.tools.registry.dispatch``, so behavior is
    unchanged, and asserts it was actually invoked with the tool name and
    session_state a "what tabs are open" command-intent turn requires.
    """
    # spec_lines 26
    state = SessionState()
    with patch("jarvis.repl.dispatch", wraps=registry_dispatch) as dispatch_spy:
        reply = run_turn("what tabs are open", state)

    assert dispatch_spy.call_count >= 1
    name, _arguments, dispatched_state = dispatch_spy.call_args.args
    assert name == "tmux_list"
    assert dispatched_state is state
    assert _speakable(reply)


@pytest.mark.slow
def test_run_turn_switches_the_active_tab(tmux_mocks):
    """The command routes through the real ``resolve_tab`` and mutates state."""
    # spec_lines 26-27
    state = SessionState(active_tab=1)
    reply = run_turn("tab three", state)

    assert state.active_tab == 3
    assert _speakable(reply)


@pytest.mark.slow
def test_run_turn_summarizes_the_active_chat(tmux_mocks):
    """The "update me" command routes through the real ``claude_summary``."""
    # spec_lines 26
    state = SessionState(active_tab=1)
    reply = run_turn("update me on this chat", state)

    assert tmux_mocks["read"].call_count >= 1
    assert _speakable(reply)


@pytest.mark.slow
def test_run_turn_acknowledges_a_draft_without_sending_anything(tmux_mocks):
    """A "draft" intent must not dispatch a send (spec_lines 26)."""
    reply = run_turn("tell it to use pydantic instead of dataclasses", SessionState())

    assert _speakable(reply)
    assert tmux_mocks["send"].call_count == 0


@pytest.mark.slow
def test_run_turn_answers_smalltalk_without_touching_tmux(tmux_mocks):
    """Only a "command" intent dispatches through the registry (spec_lines 26)."""
    reply = run_turn("what are you", SessionState())

    assert _speakable(reply)
    assert tmux_mocks["list"].call_count == 0


@pytest.mark.slow
def test_run_turn_survives_an_empty_transcript(tmux_mocks):
    """Existence boundary; the spec is silent on empty input (see BOUNDARIES.md).

    Default taken: return speakable text rather than raise, per spec_lines 26's
    "never raises" posture.
    """
    assert _speakable(run_turn("", SessionState()))


# ---------------------------------------------------------------------------
# AC-12 -- session state shape (spec_lines 27)
# ---------------------------------------------------------------------------

def test_session_state_carries_active_tab_and_reader():
    # spec_lines 27
    state = SessionState()
    assert state.active_tab is None
    assert state.reader is None

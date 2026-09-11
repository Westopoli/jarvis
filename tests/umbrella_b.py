# spec: specs/cascade-b.md::Acceptance criteria::AC-1..12
"""Cascade B umbrella test — behavioral, cross-leaf composition only.

Exercises: tool registry dispatch composed with cascade A's real tmux tools,
the intent classifier's output vocabulary composed with cascade A's real
Conversation, pipeline assembly composed with cascade A's real
Conversation/Reader/InterruptGate via the fake transport harness, and the
text REPL driver composed with the tool registry + intent classifier.

Imports are deferred INSIDE each test function (same reason as
umbrella_a.py: cascade B spans multiple leaves admitted one at a time, and
a module-level import would fail collection for the whole file until every
leaf exists).

AC-5's test and the REPL test call a real local Ollama endpoint at
temperature 0 and are marked slow.
"""
import pytest
from unittest.mock import patch


def test_dispatch_switch_tab_composes_with_real_resolve_tab():
    from jarvis.tools.registry import dispatch
    from jarvis.types import SessionState, TmuxWindow

    state = SessionState()
    windows = [
        TmuxWindow(index=1, name="jarvis", pane_path="/home/w/jarvis", pane_command="claude"),
        TmuxWindow(index=2, name="api server", pane_path="/home/w/api", pane_command="node"),
    ]
    # resolve_tab (jarvis/tools/tmux.py, cascade A) calls its OWN module-level
    # tmux_list internally — that is the real point of use, not whatever name
    # registry.py imported it under.
    with patch("jarvis.tools.tmux.tmux_list", return_value=windows):
        result = dispatch("switch_tab", {"query": "api server"}, state)
    assert result == 2
    assert state.active_tab == 2


def test_dispatch_unknown_tool_raises_keyerror():
    from jarvis.tools.registry import dispatch
    from jarvis.types import SessionState

    with pytest.raises(KeyError):
        dispatch("delete_everything", {}, SessionState())


@pytest.mark.slow
def test_classify_intent_output_drives_real_conversation_into_drafting():
    from jarvis.intent import classify_intent
    from jarvis.conversation import Conversation

    intent = classify_intent("tell it to use pydantic instead of dataclasses")
    assert intent == "draft"

    convo = Conversation(active_tab=1)
    convo.speech_start()
    convo.llm_intent(intent)

    from jarvis.types import ConversationState
    assert convo.state == ConversationState.DRAFTING


def test_pipeline_composes_real_conversation_reader_and_gate_via_fake_transport():
    from jarvis.pipeline import build_pipeline
    from jarvis.conversation import Conversation
    from jarvis.reader import Reader
    from jarvis.audio.gate import InterruptGate
    from jarvis.types import ConversationState, VADParams
    from tests.harness.fake_transport import FakeTransport

    transport = FakeTransport()
    convo = Conversation(active_tab=1)
    convo.speech_start()
    convo.llm_intent("narrate")
    reader = Reader("First narrated sentence. Second narrated sentence.")
    gate = InterruptGate(
        vad_params=VADParams(confidence=0.7, start_secs=0.3, stop_secs=0.8, min_volume=0.6),
        min_words=3,
        wake_word_detector=lambda text: "jarvis" in text.lower(),
    )

    pipeline = build_pipeline(transport, convo, reader, gate)
    assert pipeline is not None

    # AC-10 asks for "a sequence of transcript-text frames" driving IDLE
    # through NARRATING to an interrupt — a single interrupting frame with
    # no prior narration tick has nothing to interrupt yet. First tick: a
    # sub-threshold segment (word count below the gate's minimum) neither
    # interrupts nor exits NARRATING, so it advances narration by one
    # sentence. Second tick: the wake-word segment interrupts.
    transport.push_transcript("um", words=1, has_wake_word=False)
    transport.push_transcript("jarvis stop reading that", words=4, has_wake_word=True)

    emitted = transport.emitted_tts_text()
    assert len(emitted) >= 1
    assert any("narrated sentence" in t for t in emitted)
    assert convo.state != ConversationState.NARRATING


@pytest.mark.slow
def test_repl_dispatches_a_real_command_intent_through_the_registry():
    from jarvis.repl import run_turn
    from jarvis.types import SessionState

    windows = [{"index": 1, "name": "jarvis", "pane_path": "/home/w/jarvis", "pane_command": "claude"}]
    state = SessionState()
    with patch("jarvis.tools.registry.tmux_list", return_value=windows) as mock_list:
        reply = run_turn("what tabs are open", state)

    assert isinstance(reply, str) and reply
    mock_list.assert_called()

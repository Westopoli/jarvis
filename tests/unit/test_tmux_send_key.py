from __future__ import annotations

import pytest

from jarvis.tools import tmux as tmux_tools


def test_send_key_allowlist_and_target(monkeypatch):
    calls = []
    monkeypatch.setattr(tmux_tools.subprocess, "run", lambda cmd, check: calls.append(cmd))
    tmux_tools.tmux_send_key(3, "Enter", session="main")
    assert calls == [["tmux", "send-keys", "-t", "main:3", "Enter"]]
    with pytest.raises(ValueError):
        tmux_tools.tmux_send_key(3, "rm -rf /", session="main")
    assert len(calls) == 1


def test_parse_tab_number_words_and_digits():
    for q, n in [("4", 4), ("tab 4", 4), ("four", 4), ("Tab four.", 4), ("number seven", 7), ("api", None), ("tab four five", None)]:
        assert tmux_tools.parse_tab_number(q) == n, q

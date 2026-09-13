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

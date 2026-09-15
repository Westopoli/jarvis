from jarvis.tools import tmux as tmux_tools
from jarvis.types import TmuxWindow


def test_unique_window_names_scheme(monkeypatch):
    windows = [
        TmuxWindow(0, "bash", "/home/u/Projects/agora", "bash"),
        TmuxWindow(1, "agora", "/home/u/Projects/agora", "bash"),
        TmuxWindow(2, "agora", "/home/u/Projects/agora", "claude"),
        TmuxWindow(3, "ouroboros-manifold", "/home/u/Projects/ouroboros-manifold", "bash"),
        TmuxWindow(4, "jarvis", "/home/u/Projects/jarvis", "bash"),
        TmuxWindow(6, "claude", "/home/u/Projects/ouroboros-manifold", "node"),
    ]
    monkeypatch.setattr(tmux_tools, "tmux_list", lambda session=None: windows)
    calls = []
    monkeypatch.setattr(tmux_tools.subprocess, "run", lambda cmd, check: calls.append(cmd))

    renames = tmux_tools.unique_window_names("main")

    assert renames == [
        (0, "bash", "agora"),
        (1, "agora", "agora-2"),
        (2, "agora", "agora-claude"),
        (6, "claude", "ouroboros-manifold-claude"),
    ]
    assert ["tmux", "rename-window", "-t", "main:2", "agora-claude"] in calls
    assert any(c[:2] == ["tmux", "set-option"] and "automatic-rename" in c for c in calls)


def test_unique_window_names_dry_run_touches_nothing(monkeypatch):
    windows = [TmuxWindow(0, "x", "/tmp/proj", "claude")]
    monkeypatch.setattr(tmux_tools, "tmux_list", lambda session=None: windows)
    monkeypatch.setattr(tmux_tools.subprocess, "run", lambda *a, **k: (_ for _ in ()).throw(AssertionError("ran tmux")))
    assert tmux_tools.unique_window_names("main", dry_run=True) == [(0, "x", "proj-claude")]

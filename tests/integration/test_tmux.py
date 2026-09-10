# spec: specs/cascade-a.md::Acceptance criteria::AC-1..7
"""Integration tests for ``jarvis/tools/tmux.py`` (leaf-01, AC 1-7).

Isolation: every tmux invocation in this file -- ours *and* the code under
test's -- is redirected to a throwaway server by pointing ``TMUX_TMPDIR`` at a
private temp dir and deleting ``$TMUX``/``$TMUX_PANE`` (tmux prefers the socket
named in ``$TMUX`` when it is set, which would be the user's real server).  The
throwaway server is killed on teardown.  ``TMUX_TMPDIR`` is used rather than
``tmux -L jarvis-test`` because the spec gives the tools a ``session``
parameter but no *socket* parameter (AC-7), so the redirection has to be
something the implementation inherits without knowing about it; the isolation
guarantee is the same -- the user's default socket, and their "main" session,
are never contacted.
"""
from __future__ import annotations

import os
import shlex
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import pytest

from jarvis.types import TmuxWindow
from jarvis.tools.tmux import resolve_tab, tmux_list, tmux_read, tmux_send

pytestmark = pytest.mark.slow

SESSION = "jarvis-test"
SOLO_SESSION = "jarvis-solo"
# Literally "main" -- AC-7's spec-named default session. Safe to create here
# because it lives on the throwaway TMUX_TMPDIR socket, never the user's real
# server (see module docstring).
MAIN_SESSION = "main"

# Chrome the reader must strip (AC-3): box-drawing borders and TUI spinner
# lines.  ANSI escapes are exercised separately, because `tmux capture-pane -p`
# already drops them before the implementation ever sees the bytes.
PANE_TEXT = (
    "BUILD OK marker-42\n"
    "╭──────────╮\n"
    "│ boxed txt │\n"
    "╰──────────╯\n"
    "─ dash lead\n"
    "✳ Herding… (12s · esc to interrupt)\n"
    "⠋ Working…\n"
    "plain tail line\n"
)


def _tmux(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["tmux", "-f", "/dev/null", *args],
        capture_output=True,
        text=True,
        check=True,
    )


def _wait_until(predicate, timeout: float = 8.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.1)
    return predicate()


@pytest.fixture(scope="module")
def tmux_fixture():
    """A throwaway tmux server with a known five-window session."""
    # Short path: the unix socket path (root + "tmux-<uid>/default") must stay
    # under the ~108 byte sun_path limit, which pytest's tmp_path can exceed.
    root = Path(tempfile.mkdtemp(prefix="jvx", dir="/tmp"))
    saved = {k: os.environ.get(k) for k in ("TMUX_TMPDIR", "TMUX", "TMUX_PANE", "JARVIS_TMUX_SESSION")}
    os.environ["TMUX_TMPDIR"] = str(root / "sock")
    for key in ("TMUX", "TMUX_PANE", "JARVIS_TMUX_SESSION"):
        os.environ.pop(key, None)
    (root / "sock").mkdir()

    for name in ("editor-proj", "api-service", "notes", "scratch"):
        (root / name).mkdir()
    bindir = root / "bin"
    bindir.mkdir()
    # `pane_current_command` is the exec'd file's basename, so symlinking a
    # real long-lived binary under the name we want gives a deterministic
    # command without needing claude/node installed.
    for alias in ("claude", "node", "pager"):
        (bindir / alias).symlink_to(Path(sys.executable).resolve())
    script = root / "chrome.py"
    script.write_text(
        "import sys, time\n"
        f"sys.stdout.write({PANE_TEXT!r})\n"
        "sys.stdout.flush()\n"
        "time.sleep(600)\n"
    )
    main_script = root / "main_chrome.py"
    main_script.write_text(
        "import sys, time\n"
        "sys.stdout.write('main-session-marker-99\\n')\n"
        "sys.stdout.flush()\n"
        "time.sleep(600)\n"
    )
    idle = "import time; time.sleep(600)"

    _tmux("new-session", "-d", "-s", SESSION, "-n", "editor",
          "-c", str(root / "editor-proj"), "/bin/sleep 600")
    _tmux("new-window", "-t", f"{SESSION}:1", "-n", "claude-api",
          "-c", str(root / "api-service"),
          f"{shlex.quote(str(bindir / 'claude'))} -c {shlex.quote(idle)}")
    _tmux("new-window", "-t", f"{SESSION}:2", "-n", "notes",
          "-c", str(root / "notes"),
          f"{shlex.quote(str(bindir / 'node'))} -c {shlex.quote(idle)}")
    _tmux("new-window", "-t", f"{SESSION}:3", "-n", "chrome",
          "-c", str(root / "editor-proj"),
          f"{shlex.quote(str(bindir / 'pager'))} {shlex.quote(str(script))}")
    _tmux("new-window", "-t", f"{SESSION}:4", "-n", "shellwin",
          "-c", str(root / "scratch"), "sh")
    _tmux("new-session", "-d", "-s", SOLO_SESSION, "-n", "only-window",
          "-c", str(root / "scratch"), "/bin/sleep 600")
    # AC-7 / spec_lines 16 (🟡4): a throwaway session literally named "main"
    # so the spec's stated default session value gets exercised, not just its
    # env-var-override path (which `test_tmux_list_defaults_session_to_...`
    # already covers). Runs `claude` so it also satisfies tmux_send's pane
    # command check.
    _tmux("new-session", "-d", "-s", MAIN_SESSION, "-n", "mainwin",
          "-c", str(root / "scratch"),
          f"{shlex.quote(str(bindir / 'claude'))} {shlex.quote(str(main_script))}")

    def _panes_ready() -> bool:
        out = _tmux("list-windows", "-t", SESSION, "-F", "#I|#{pane_current_command}").stdout
        return "1|claude" in out and "2|node" in out

    _wait_until(_panes_ready)
    _wait_until(
        lambda: "marker-42" in _tmux("capture-pane", "-p", "-t", f"{SESSION}:3").stdout
    )
    _wait_until(
        lambda: "main-session-marker-99"
        in _tmux("capture-pane", "-p", "-t", f"{MAIN_SESSION}:0").stdout
    )

    try:
        yield root
    finally:
        subprocess.run(["tmux", "kill-server"], capture_output=True, text=True)
        for key, value in saved.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
        shutil.rmtree(root, ignore_errors=True)


@pytest.fixture
def send_keys_spy(monkeypatch):
    """Record `tmux send-keys` argv without letting the keys reach a pane.

    Every other tmux subprocess (list-windows, capture-pane) still runs for
    real against the throwaway server.
    """
    real_run = subprocess.run
    calls: list[list[str]] = []

    def fake_run(cmd, *args, **kwargs):
        if not isinstance(cmd, str) and "send-keys" in list(cmd):
            calls.append(list(cmd))
            return subprocess.CompletedProcess(list(cmd), 0, "", "")
        return real_run(cmd, *args, **kwargs)

    monkeypatch.setattr(subprocess, "run", fake_run)
    return calls


@pytest.fixture
def run_call_spy(monkeypatch):
    """Count real `subprocess.run` invocations without altering behavior.

    Unlike `send_keys_spy`, every call is let through to the real throwaway
    server -- this only counts how many `subprocess.run` calls a tool call
    makes.
    """
    real_run = subprocess.run
    calls: list[list[str]] = []

    def fake_run(cmd, *args, **kwargs):
        calls.append(list(cmd) if not isinstance(cmd, str) else [cmd])
        return real_run(cmd, *args, **kwargs)

    monkeypatch.setattr(subprocess, "run", fake_run)
    return calls


# --------------------------------------------------------------------------
# AC-1 / AC-7: tmux_list
# --------------------------------------------------------------------------

def test_tmux_list_parses_index_name_path_and_command_per_window(tmux_fixture):
    root = tmux_fixture
    windows = tmux_list(session=SESSION)

    assert windows[:3] == [
        TmuxWindow(index=0, name="editor",
                   pane_path=str(root / "editor-proj"), pane_command="sleep"),
        TmuxWindow(index=1, name="claude-api",
                   pane_path=str(root / "api-service"), pane_command="claude"),
        TmuxWindow(index=2, name="notes",
                   pane_path=str(root / "notes"), pane_command="node"),
    ]
    assert len(windows) == 5


def test_tmux_list_handles_a_single_window_session(tmux_fixture):
    # Cardinality "one": tmux cannot hold a zero-window session, so one is the
    # smallest reachable list.
    windows = tmux_list(session=SOLO_SESSION)

    assert [(w.index, w.name) for w in windows] == [(0, "only-window")]


def test_tmux_list_issues_exactly_one_subprocess_call_per_invocation(tmux_fixture, run_call_spy):
    # spec_lines 74 (🟡5): "one tmux subprocess invocation per tool call --
    # no per-window subprocess calls inside tmux_list's loop". SESSION has 5
    # windows (>= 2), so a naive per-window implementation (list windows, then
    # one capture/display-message per window) would show up here.
    tmux_list(session=SESSION)

    assert len(run_call_spy) == 1


def test_tmux_list_defaults_session_to_jarvis_tmux_session_env(tmux_fixture, monkeypatch):
    monkeypatch.setenv("JARVIS_TMUX_SESSION", SOLO_SESSION)

    assert [w.name for w in tmux_list()] == ["only-window"]


# --------------------------------------------------------------------------
# AC-2: resolve_tab
# --------------------------------------------------------------------------

def test_resolve_tab_returns_integer_like_query_directly(tmux_fixture):
    assert resolve_tab("3", session=SESSION) == 3
    assert resolve_tab("0", session=SESSION) == 0


def test_resolve_tab_fuzzy_matches_window_name_and_path_basename(tmux_fixture):
    assert resolve_tab("claude", session=SESSION) == 1  # window name
    assert resolve_tab("api-service", session=SESSION) == 1  # pane path basename


def test_resolve_tab_returns_none_below_similarity_threshold(tmux_fixture):
    assert resolve_tab("qzvwxjjkbb", session=SESSION) is None


def test_resolve_tab_defaults_session_to_main_when_env_unset(tmux_fixture, monkeypatch):
    # AC-7 / spec_lines 16 (🟡4): the literal default is "main", not merely
    # whatever JARVIS_TMUX_SESSION happens to hold -- called with no explicit
    # `session=` and the env var unset, this must resolve against MAIN_SESSION.
    monkeypatch.delenv("JARVIS_TMUX_SESSION", raising=False)

    assert resolve_tab("mainwin") == 0


# --------------------------------------------------------------------------
# AC-3: tmux_read
# --------------------------------------------------------------------------

def test_tmux_read_returns_pane_text_without_box_or_spinner_chrome(tmux_fixture):
    out = tmux_read(3, 50, session=SESSION)

    assert "BUILD OK marker-42" in out
    assert "plain tail line" in out
    assert not any(ch in out for ch in "╭╰│─✳⠋")


def test_tmux_read_defaults_session_to_main_when_env_unset(tmux_fixture, monkeypatch):
    # AC-7 / spec_lines 16 (🟡4): no explicit `session=` and no env var -- the
    # text must come from MAIN_SESSION's pane, not SESSION's or SOLO_SESSION's.
    monkeypatch.delenv("JARVIS_TMUX_SESSION", raising=False)

    out = tmux_read(0, 50)

    assert "main-session-marker-99" in out


def test_tmux_read_strips_ansi_box_and_spinner_lines(monkeypatch):
    # `tmux capture-pane -p` already drops ANSI, so the escape-stripping half of
    # AC-3 is exercised against a captured payload that still carries them.
    raw = (
        "\x1b[32mBUILD OK\x1b[0m marker-42\n"
        + PANE_TEXT.split("\n", 1)[1]
    )
    calls: list[list[str]] = []

    def fake_run(cmd, *args, **kwargs):
        calls.append(list(cmd))
        return subprocess.CompletedProcess(list(cmd), 0, raw, "")

    monkeypatch.setattr(subprocess, "run", fake_run)

    out = tmux_read(3, 50, session=SESSION)

    assert [line for line in out.splitlines() if line.strip()] == [
        "BUILD OK marker-42",
        "plain tail line",
    ]
    # AC-3 / spec_lines 73 (🟡3): the caller's `lines` value must actually
    # reach the underlying `capture-pane` argv as `-S -<lines>`, not just be
    # accepted and discarded.
    [call] = calls
    assert "-S" in call
    assert call[call.index("-S") + 1] == "-50"


# --------------------------------------------------------------------------
# AC-4 / AC-5 / AC-6: tmux_send
# --------------------------------------------------------------------------

def test_tmux_send_unconfirmed_raises_and_sends_nothing(tmux_fixture, send_keys_spy):
    with pytest.raises(PermissionError):
        tmux_send(1, "rm -rf /", confirmed=False, session=SESSION)

    assert send_keys_spy == []


def test_tmux_send_confirmed_rejects_pane_not_running_claude_or_node(tmux_fixture, send_keys_spy):
    # window 0 runs `sleep`, which is neither claude nor node.
    with pytest.raises(PermissionError):
        tmux_send(0, "hello", confirmed=True, session=SESSION)

    assert send_keys_spy == []


def test_tmux_send_confirmed_on_claude_pane_sends_literal_then_enter(tmux_fixture, send_keys_spy):
    tmux_send(1, "add a retry", confirmed=True, session=SESSION)

    assert send_keys_spy == [
        ["tmux", "send-keys", "-t", f"{SESSION}:1", "-l", "add a retry"],
        ["tmux", "send-keys", "-t", f"{SESSION}:1", "Enter"],
    ]


def test_tmux_send_confirmed_on_node_pane_is_allowed(tmux_fixture, send_keys_spy):
    tmux_send(2, "npm test", confirmed=True, session=SESSION)

    assert [c[-1] for c in send_keys_spy] == ["npm test", "Enter"]


def test_tmux_send_defaults_session_to_main_when_env_unset(tmux_fixture, send_keys_spy, monkeypatch):
    # AC-7 / spec_lines 16 (🟡4): no explicit `session=` and no env var -- the
    # send-keys argv must target MAIN_SESSION's window, not SESSION's.
    monkeypatch.delenv("JARVIS_TMUX_SESSION", raising=False)

    tmux_send(0, "hello from main", confirmed=True)

    assert send_keys_spy == [
        ["tmux", "send-keys", "-t", "main:0", "-l", "hello from main"],
        ["tmux", "send-keys", "-t", "main:0", "Enter"],
    ]


def test_tmux_send_force_bypasses_pane_command_check_and_reaches_the_pane(tmux_fixture):
    # No spy here: this one goes all the way to the real pane, which proves the
    # literal text and the separate Enter actually land (window 4 runs `sh`).
    tmux_send(4, "echo jarvis-marker-42", confirmed=True, force=True, session=SESSION)

    assert _wait_until(
        lambda: _tmux("capture-pane", "-p", "-t", f"{SESSION}:4").stdout.count(
            "jarvis-marker-42"
        )
        >= 2
    )

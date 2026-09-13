"""Turn Claude/terminal text into something worth hearing.

Nobody wants ``git commit -m "..." && git push origin main`` read out
character by character while driving. ``to_speakable`` rewrites text before
it reaches the narrator or the summarising LLM:

- fenced code blocks become "a bash command that commits and pushes" or
  "a python code block, twelve lines";
- inline code that looks like a shell command becomes a short description,
  other inline code is read as plain words;
- Claude Code tool lines (``Bash(...)``, ``Read(...)``, ``Edit(...)``)
  become "ran a grep search", "read a file called foo";
- markdown decoration (headings, bullets, emphasis, tables) is stripped.

``describe_command`` is the heuristic behind the command summaries: a small
verb table for common tools, then "a <name> command" for everything else.
"""
from __future__ import annotations

import re
import shlex

# First-word -> how to describe it. Callables get the argv list.
_GIT_VERBS = {
    "status": "checks the git status",
    "commit": "makes a git commit",
    "push": "pushes to git",
    "pull": "pulls from git",
    "add": "stages files in git",
    "diff": "shows a git diff",
    "log": "shows the git log",
    "checkout": "switches git branches",
    "switch": "switches git branches",
    "merge": "merges a git branch",
    "rebase": "rebases in git",
    "stash": "stashes git changes",
    "branch": "lists or changes git branches",
    "clone": "clones a git repository",
    "reset": "resets git state",
}

_SIMPLE = {
    "grep": "a grep search",
    "rg": "a grep search",
    "ag": "a grep search",
    "find": "a file search",
    "fd": "a file search",
    "ls": "lists files",
    "cat": "prints a file",
    "head": "prints the start of a file",
    "tail": "prints the end of a file",
    "sed": "a sed text edit",
    "awk": "an awk script",
    "cd": "changes directory",
    "mkdir": "makes a directory",
    "rm": "deletes files",
    "mv": "moves files",
    "cp": "copies files",
    "touch": "creates a file",
    "chmod": "changes file permissions",
    "curl": "makes a web request",
    "wget": "downloads a file",
    "pytest": "runs the tests",
    "make": "runs a make target",
    "npm": "an npm command",
    "npx": "an npx command",
    "node": "runs a node script",
    "pip": "a pip install",
    "docker": "a docker command",
    "ssh": "an ssh connection",
    "tmux": "a tmux command",
    "systemctl": "a systemctl command",
    "echo": "prints text",
    "export": "sets an environment variable",
    "source": "sources a shell file",
    "ollama": "an ollama command",
}


def describe_command(command: str) -> str:
    """One short phrase for a shell command line."""
    command = command.strip()
    if not command:
        return "an empty command"
    # Only describe the first command of a pipeline / && chain.
    first = re.split(r"\s*(?:\|\||&&|\||;)\s*", command, maxsplit=1)[0]
    try:
        argv = shlex.split(first)
    except ValueError:
        argv = first.split()
    # Skip leading env assignments and sudo.
    while argv and (re.match(r"^[A-Za-z_][A-Za-z0-9_]*=", argv[0]) or argv[0] == "sudo"):
        argv = argv[1:]
    if not argv:
        return "a shell command"
    name = argv[0].rsplit("/", 1)[-1]
    chained = len(re.split(r"\s*(?:\|\||&&|\||;)\s*", command)) > 1

    if name == "git" and len(argv) > 1 and argv[1] in _GIT_VERBS:
        phrase = _GIT_VERBS[argv[1]]
    elif name == "uv" and len(argv) > 1:
        sub = argv[1]
        if sub == "run" and len(argv) > 2:
            phrase = _run_phrase(argv[2:])
        elif sub in ("sync", "add", "remove", "lock", "pip"):
            phrase = "a uv dependency command"
        else:
            phrase = f"a uv {sub} command"
    elif name in ("python", "python3"):
        phrase = _run_phrase(argv)
    elif name in _SIMPLE:
        phrase = _SIMPLE[name]
    else:
        phrase = f"a {name} command"

    if chained:
        phrase += ", chained with more"
    return phrase


def _run_phrase(argv: list[str]) -> str:
    name = argv[0].rsplit("/", 1)[-1]
    if name in ("python", "python3"):
        if len(argv) > 2 and argv[1] == "-m":
            return f"runs the python module {argv[2].replace('.', ' ')}"
        if len(argv) > 1 and argv[1].endswith(".py"):
            return f"runs the python script {_spoken_filename(argv[1])}"
        return "runs python"
    if name == "pytest":
        return "runs the tests"
    if name in _SIMPLE:
        return _SIMPLE[name]
    return f"runs {name}"


def _spoken_filename(path: str) -> str:
    base = path.rsplit("/", 1)[-1]
    return base.replace("_", " ").replace("-", " ").replace(".py", "")


# ---------------------------------------------------------------------------

_FENCE_RE = re.compile(r"```([\w+-]*)[^\n]*\n(.*?)```", re.DOTALL)
_INLINE_RE = re.compile(r"`([^`\n]+)`")
_TOOL_LINE_RE = re.compile(r"^[●•*]?\s*(Bash|Read|Edit|Write|Grep|Glob|Update)\((.*)\)\s*$")
_SHELL_LEADS = set(_SIMPLE) | {"git", "uv", "python", "python3", "sudo", "cd", "./"}


def _looks_like_command(text: str) -> bool:
    first = text.strip().split(" ", 1)[0]
    return first.rsplit("/", 1)[-1] in _SHELL_LEADS or first.startswith("./")


def _fence_replacement(match: re.Match) -> str:
    lang = (match.group(1) or "").lower()
    body = match.group(2).strip("\n")
    lines = [line for line in body.splitlines() if line.strip()]
    if lang in ("bash", "sh", "shell", "zsh", "console") or (
        not lang and lines and _looks_like_command(lines[0].lstrip("$ "))
    ):
        if len(lines) == 1:
            return f" a shell command that {_verbify(describe_command(lines[0].lstrip('$ ')))}. "
        return f" a shell script, {len(lines)} commands, starting with one that {_verbify(describe_command(lines[0].lstrip('$ ')))}. "
    label = f"a {lang} code block" if lang else "a code block"
    return f" {label}, {len(lines)} lines. "


def _verbify(phrase: str) -> str:
    """'a grep search' -> 'does a grep search'; 'runs the tests' stays."""
    if phrase.startswith(("a ", "an ")):
        return f"does {phrase}"
    return phrase


def _inline_replacement(match: re.Match) -> str:
    text = match.group(1).strip()
    if _looks_like_command(text):
        return describe_command(text)
    return text


def _tool_line_replacement(match: re.Match) -> str:
    tool, arg = match.group(1), match.group(2).strip()
    if tool == "Bash":
        desc = describe_command(arg)
        return f"Ran {desc}." if desc.startswith(("a ", "an ")) else f"Ran a command that {desc}."
    target = _spoken_filename(arg.split(",")[0].strip().strip("'\""))
    verbs = {"Read": "Read", "Edit": "Edited", "Write": "Wrote",
             "Grep": "Searched for", "Glob": "Looked for", "Update": "Updated"}
    return f"{verbs[tool]} {target}."


def to_speakable(text: str) -> str:
    """Rewrite ``text`` so a TTS voice reads sense, not syntax."""
    out = _FENCE_RE.sub(_fence_replacement, text)
    out = _INLINE_RE.sub(_inline_replacement, out)

    lines = []
    for line in out.splitlines():
        stripped = line.strip()
        if not stripped:
            lines.append("")
            continue
        tool = _TOOL_LINE_RE.match(stripped)
        if tool:
            lines.append(_tool_line_replacement(tool))
            continue
        if stripped.startswith("⎿"):
            stripped = stripped.lstrip("⎿ ").strip()
        if re.match(r"^\|.*\|$", stripped):
            if lines and lines[-1] == "a table.":
                continue
            lines.append("a table.")
            continue
        stripped = re.sub(r"^#{1,6}\s+", "", stripped)
        stripped = re.sub(r"^\s*(?:[-*+]|\d+[.)])\s+", "", stripped)
        stripped = re.sub(r"\*\*([^*]+)\*\*", r"\1", stripped)
        stripped = re.sub(r"(?<!\w)[*_]([^*_]+)[*_](?!\w)", r"\1", stripped)
        stripped = stripped.replace("→", " to ").replace("->", " to ")
        lines.append(stripped)
    result = "\n".join(lines)
    result = re.sub(r"\n{3,}", "\n\n", result)
    return re.sub(r"[ \t]{2,}", " ", result).strip()

from jarvis.speakable import describe_command, to_speakable


def test_describe_common_commands():
    assert describe_command("git commit -m 'x' && git push") == "makes a git commit, chained with more"
    assert describe_command("grep -rn foo src/") == "a grep search"
    assert describe_command("uv run pytest tests/unit -q") == "runs the tests"
    assert describe_command("uv run python -m jarvis.run_desktop") == "runs the python module jarvis run_desktop"
    assert describe_command("python3 scripts/gen_audio.py") == "runs the python script gen audio"
    assert describe_command("JARVIS_PORT=8011 sudo systemctl restart jarvis") == "a systemctl command"
    assert describe_command("cargo build --release") == "a cargo command"
    assert describe_command("") == "an empty command"


def test_fenced_bash_block_is_summarised():
    text = "Run this:\n```bash\ngit status --short\n```\nthen tell me."
    out = to_speakable(text)
    assert "git status --short" not in out
    assert "a shell command that checks the git status" in out
    assert "then tell me" in out


def test_fenced_code_block_becomes_a_count():
    text = "```python\nimport os\nprint(os.getcwd())\n```"
    assert to_speakable(text) == "a python code block, 2 lines."


def test_inline_command_vs_identifier():
    assert to_speakable("Run `pytest -x` first") == "Run runs the tests first"
    assert to_speakable("The `Narrator` class") == "The Narrator class"


def test_claude_tui_tool_lines():
    text = "● Bash(grep -rn foo jarvis/)\n  ⎿  jarvis/x.py:3: foo\n● Read(jarvis/tools/tmux.py)\n● Edit(jarvis/reader.py)"
    out = to_speakable(text)
    assert "Ran a grep search." in out
    assert "Read tmux." in out
    assert "Edited reader." in out
    assert "jarvis/x.py:3: foo" in out


def test_markdown_noise_removed():
    text = "## Summary\n- **Fixed** the *bug*\n| a | b |\n|---|---|\n| 1 | 2 |\nDone -> next"
    out = to_speakable(text)
    assert out.splitlines()[0] == "Summary"
    assert "Fixed the bug" in out
    assert out.count("a table.") == 1
    assert "Done to next" in out

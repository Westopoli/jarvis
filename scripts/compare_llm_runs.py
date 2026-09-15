"""Diff two run_desktop.py --say transcripts' TOOLCALL sequences, turn by turn.

Used for the Ollama-vs-Groq desktop A/B verification (see docs/groq-backend.md):
run the same --say script twice, once per JARVIS_LLM_PROVIDER, and compare
which tool each backend routed each turn to. Tool-name match per turn is the
objective, repeatable signal; argument values are printed for inspection but
not hard-asserted equal, since e.g. find_tab's `topic` argument is free-text
the LLM paraphrases and may legitimately differ in wording between two
different models while still being the same routing decision.

This is a script, not a pytest test: it depends on live model output and
real tmux state, so it can never be a CI assertion. Its job is to turn two
long transcripts into one aligned table a human can eyeball in seconds.

Usage:
    uv run python scripts/compare_llm_runs.py /tmp/jarvis_ollama_run.log /tmp/jarvis_groq_run.log
"""
from __future__ import annotations

import re
import sys

TOOLCALL_RE = re.compile(r"TOOLCALL (\S+) args=(\{.*\})\s*$")


def extract_calls(path: str) -> list[tuple[str, str]]:
    calls = []
    with open(path) as f:
        for line in f:
            m = TOOLCALL_RE.search(line)
            if m:
                calls.append((m.group(1), m.group(2)))
    return calls


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        print(__doc__)
        return 2
    path_a, path_b = argv
    calls_a, calls_b = extract_calls(path_a), extract_calls(path_b)

    ok = True
    print(f"{'turn':>4}  {'ollama':<28} {'groq':<28}  match?")
    for i, (a, b) in enumerate(zip(calls_a, calls_b)):
        match = a[0] == b[0]
        ok &= match
        print(f"{i:>4}  {a[0]:<28} {b[0]:<28}  {'OK' if match else 'MISMATCH'}")
        if not match:
            print(f"      args a: {a[1]}")
            print(f"      args b: {b[1]}")

    for i in range(min(len(calls_a), len(calls_b)), max(len(calls_a), len(calls_b))):
        ok = False
        extra = calls_a[i] if i < len(calls_a) else calls_b[i]
        side = "ollama" if i < len(calls_a) else "groq"
        print(f"{i:>4}  EXTRA CALL on {side}: {extra[0]} args={extra[1]}")

    if len(calls_a) != len(calls_b):
        print(f"\nDIFFERENT CALL COUNTS: ollama={len(calls_a)} groq={len(calls_b)}")

    print("\nALL TURNS MATCH" if ok else "\nMISMATCHES FOUND -- see above")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))

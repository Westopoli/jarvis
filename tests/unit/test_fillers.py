# spec: specs/cascade-c.md::Acceptance criteria::AC-12..13
"""Tests for the filler-phrase strategy (leaf-04, AC 12-13): ``jarvis/fillers.py``.

Two deliberate choices:

* The five expected tool names are spelled out here *and* cross-checked
  against the real ``jarvis.tools.registry.TOOL_SCHEMAS`` (cascade B, already
  landed).  AC-12 says "the same 5 tool names as TOOL_SCHEMAS", so the
  registry is the oracle; the literal set is the drift guard that makes the
  cross-check non-vacuous if the registry itself ever changes.
* ``pick_filler``'s use of the injected ``rng`` is proved with a recording
  stub, not only with two equally-seeded ``random.Random``s — a ``pick_filler``
  that always returned ``FILLERS[name][0]`` would satisfy determinism alone.
"""
from __future__ import annotations

import random

import pytest

from jarvis.fillers import FILLERS, pick_filler
from jarvis.tools.registry import TOOL_SCHEMAS

TOOL_NAMES = {
    "tmux_list",
    "switch_tab",
    "claude_summary",
    "send_prompt",
    "resume_reading",
}


class _RngSpy:
    """Records what ``pick_filler`` hands to ``rng.choice``."""

    def __init__(self, index: int = 0) -> None:
        self.calls: list[list] = []
        self.index = index

    def choice(self, seq):
        self.calls.append(list(seq))
        return seq[self.index]


# --------------------------------------------------------------------------
# AC-12: FILLERS' shape
# --------------------------------------------------------------------------

def test_the_registry_still_exposes_the_five_expected_tool_names():
    assert {schema["function"]["name"] for schema in TOOL_SCHEMAS} == TOOL_NAMES


def test_fillers_covers_every_tool_the_registry_exposes():
    assert isinstance(FILLERS, dict) and set(FILLERS) >= TOOL_NAMES


def test_every_tool_has_at_least_two_distinct_non_empty_phrases():
    offenders = {
        name: phrases
        for name, phrases in FILLERS.items()
        if not isinstance(phrases, list)
        or len({p for p in phrases if isinstance(p, str) and p.strip()}) < 2
    }

    assert offenders == {}


# --------------------------------------------------------------------------
# AC-13: pick_filler
# --------------------------------------------------------------------------

def test_pick_filler_returns_one_of_the_configured_phrases():
    assert all(pick_filler(name) in FILLERS[name] for name in sorted(TOOL_NAMES))
    assert all(pick_filler(name, rng=None) in FILLERS[name] for name in sorted(TOOL_NAMES))


@pytest.mark.parametrize("tool_name", sorted(TOOL_NAMES))
def test_a_seeded_rng_makes_the_choice_reproducible(tool_name):
    assert pick_filler(tool_name, rng=random.Random(7)) == pick_filler(
        tool_name, rng=random.Random(7)
    )


@pytest.mark.parametrize("tool_name", sorted(TOOL_NAMES))
def test_different_seeds_actually_reach_different_phrases(tool_name):
    # Without this, "deterministic" would also be satisfied by always
    # returning FILLERS[tool_name][0] and ignoring rng entirely.
    picked = {pick_filler(tool_name, rng=random.Random(seed)) for seed in range(50)}

    assert len(picked) > 1


def test_pick_filler_delegates_the_choice_to_the_supplied_rng():
    spy = _RngSpy(index=1)

    result = pick_filler("switch_tab", rng=spy)

    assert spy.calls == [list(FILLERS["switch_tab"])]
    assert result == FILLERS["switch_tab"][1]


@pytest.mark.parametrize(
    "unknown",
    ["no_such_tool", "", "TMUX_LIST", "tmux_list ", "switch_tab\n"],
)
@pytest.mark.parametrize("rng", [None, random.Random(3)])
def test_an_unrecognised_tool_name_falls_back_instead_of_raising(unknown, rng):
    phrase = pick_filler(unknown, rng=rng)

    assert isinstance(phrase, str) and phrase.strip()

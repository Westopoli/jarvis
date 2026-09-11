# spec: specs/cascade-c.md::Acceptance criteria::AC-14
"""Tests for the latency log (leaf-04, AC-14): ``jarvis/metrics.py``.

Two deliberate choices:

* The 100-entry bound is walked at 99 / 100 / 101 / 150, not just "many" —
  spec L29/L42 pin the cap at *the most recent 100*, so both which entries
  survive and how many are asserted at the boundary itself.
* ``timed`` is asserted to record on **exit**, by checking ``entries`` is
  still empty from inside the ``with`` block.  Only a lower bound is placed
  on the measured duration (``sleep`` guarantees at least that much real
  time); leaves spawn in parallel, so an upper bound would flake.
"""
from __future__ import annotations

import time

import pytest

from jarvis.metrics import LatencyLog


# --------------------------------------------------------------------------
# AC-14: record / entries
# --------------------------------------------------------------------------

def test_a_fresh_log_is_empty():
    assert list(LatencyLog().entries) == []


def test_record_appends_an_event_seconds_timestamp_triple():
    # spec L29 names only "timestamp" and is silent on clock source, so this
    # only requires a real number that does not decrease between two calls —
    # true for both time.time() and time.monotonic() implementations.
    log = LatencyLog()

    log.record("stt", 0.25)
    log.record("llm", 0.5)

    first, second = log.entries[0], log.entries[1]
    assert isinstance(log.entries, (list, tuple))
    assert (first[0], first[1]) == ("stt", 0.25)
    assert isinstance(first[2], (int, float))
    assert second[2] >= first[2]


def test_entries_come_back_in_insertion_order():
    log = LatencyLog()
    for name, seconds in (("stt", 0.1), ("llm", 0.9), ("tts", 0.4)):
        log.record(name, seconds)

    assert [(e[0], e[1]) for e in log.entries] == [("stt", 0.1), ("llm", 0.9), ("tts", 0.4)]


def test_timestamps_never_go_backwards():
    log = LatencyLog()
    for index in range(5):
        log.record(f"e{index}", float(index))

    stamps = [e[2] for e in log.entries]
    assert stamps == sorted(stamps)


@pytest.mark.parametrize(
    "recorded, expected_len, expected_first, expected_last",
    [
        (1, 1, "e0", "e0"),
        (99, 99, "e0", "e98"),
        (100, 100, "e0", "e99"),      # the cap itself — nothing dropped yet
        (101, 100, "e1", "e100"),     # one past the cap — the oldest goes
        (150, 100, "e50", "e149"),
    ],
)
def test_the_log_keeps_only_the_most_recent_hundred_entries(
    recorded, expected_len, expected_first, expected_last
):
    log = LatencyLog()
    for index in range(recorded):
        log.record(f"e{index}", float(index))

    entries = list(log.entries)
    assert (len(entries), entries[0][0], entries[-1][0]) == (
        expected_len,
        expected_first,
        expected_last,
    )


# --------------------------------------------------------------------------
# AC-14: timed
# --------------------------------------------------------------------------

def test_timed_records_the_measured_duration_on_exit():
    log = LatencyLog()

    with log.timed("tool_call"):
        assert list(log.entries) == []   # recorded on exit, not on entry
        time.sleep(0.02)

    assert len(log.entries) == 1
    assert log.entries[0][0] == "tool_call"
    assert log.entries[0][1] >= 0.019


def test_several_timed_blocks_accumulate_in_order():
    log = LatencyLog()
    for name in ("stt", "llm", "tts"):
        with log.timed(name):
            pass

    assert [e[0] for e in log.entries] == ["stt", "llm", "tts"]

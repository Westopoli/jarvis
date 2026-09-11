# spec: specs/cascade-c.md::Acceptance criteria::AC-14
"""Latency logging for Jarvis (leaf-04, AC-14): ``jarvis/metrics.py``.

``LatencyLog`` records ``(event, seconds, timestamp)`` triples, keeping only
the most recent 100 entries, and exposes ``timed`` as a context manager that
measures and records wall-clock duration on exit.
"""
from __future__ import annotations

import time
from contextlib import contextmanager
from typing import Iterator

_MAX_ENTRIES = 100


class LatencyLog:
    """An append-only log of recent (event, seconds, timestamp) triples."""

    def __init__(self) -> None:
        self.entries: list[tuple[str, float, float]] = []

    def record(self, event: str, seconds: float) -> None:
        self.entries.append((event, seconds, time.monotonic()))
        if len(self.entries) > _MAX_ENTRIES:
            self.entries = self.entries[-_MAX_ENTRIES:]

    @contextmanager
    def timed(self, event: str) -> Iterator[None]:
        start = time.perf_counter()
        try:
            yield
        finally:
            self.record(event, time.perf_counter() - start)

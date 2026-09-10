"""Narration reader: splits text into sentences and tracks a read cursor.

Spec: specs/cascade-a.md, "Narration reader (`jarvis/reader.py`)", AC 14-18.
"""
from __future__ import annotations

import re

_SENTENCE_BOUNDARY = re.compile(r"(?<=[.!?])\s+")


class Reader:
    """Walks a block of text one sentence at a time.

    ``next()`` advances the cursor and returns the sentence it was on;
    ``resume()`` returns that same sentence again without advancing, so
    narration can be resumed at the point it was interrupted; ``skip()``
    advances without returning anything.
    """

    def __init__(self, text: str) -> None:
        self._sentences = self._split(text)
        self._cursor = 0

    @staticmethod
    def _split(text: str) -> list[str]:
        stripped = text.strip()
        if not stripped:
            return []
        return [part for part in _SENTENCE_BOUNDARY.split(stripped) if part]

    def next(self) -> str | None:
        if self._cursor >= len(self._sentences):
            return None
        sentence = self._sentences[self._cursor]
        self._cursor += 1
        return sentence

    def resume(self) -> str | None:
        if not self._sentences:
            return None
        index = min(max(self._cursor - 1, 0), len(self._sentences) - 1)
        return self._sentences[index]

    def skip(self) -> None:
        self._cursor += 1
        return None

    @property
    def done(self) -> bool:
        return self._cursor >= len(self._sentences)

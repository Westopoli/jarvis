# spec: specs/cascade-c.md::Acceptance criteria::AC-1,AC-2
"""Telnyx transport helpers (leaf-01, AC-1/AC-2).

``build_telnyx_serializer`` wraps pipecat's real ``TelnyxFrameSerializer`` so
callers never have to know pipecat's constructor shape. ``is_allowed_caller``
is the caller allow-list gate that ``jarvis.server``'s ``/ws/telnyx`` route
must run before it ever builds a serializer.
"""
from __future__ import annotations

import os

from pipecat.serializers.telnyx import TelnyxFrameSerializer


def build_telnyx_serializer(
    stream_id: str, call_control_id: str | None = None, outbound_encoding: str = "PCMU"
) -> TelnyxFrameSerializer:
    """Construct a real ``TelnyxFrameSerializer`` for one call's media stream.

    ``api_key`` is read from ``TELNYX_API_KEY`` at call time (may be
    empty/unset — the serializer itself owns what a missing key means).
    """
    api_key = os.environ.get("TELNYX_API_KEY")
    return TelnyxFrameSerializer(
        stream_id,
        outbound_encoding=outbound_encoding,
        inbound_encoding="PCMU",
        call_control_id=call_control_id,
        api_key=api_key,
    )


def is_allowed_caller(from_number: str) -> bool:
    """Return True only if ``from_number`` exactly matches a configured entry.

    Reads ``TELNYX_ALLOWED_CALLER`` (comma-separated E.164 numbers) from the
    environment at call time, not import time, so tests can monkeypatch it.
    Fails closed: an unset/empty/whitespace-only env var (or a blank entry
    within it) never matches anything, including a blank caller number.
    """
    raw = os.environ.get("TELNYX_ALLOWED_CALLER", "")
    if not raw.strip():
        return False

    allowed = {entry.strip() for entry in raw.split(",") if entry.strip()}
    caller = from_number.strip() if from_number else ""
    if not caller:
        return False
    return caller in allowed

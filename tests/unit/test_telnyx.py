# spec: specs/cascade-c.md::Acceptance criteria::AC-1..4
"""Tests for the Telnyx transport + the new server routes (leaf-01, AC 1-4):
``jarvis/transports/telnyx.py`` and the ``/ws/telnyx`` + ``/health`` routes
added to ``jarvis/server.py``.

Three deliberate choices:

* ``build_telnyx_serializer`` is exercised against the **real**
  ``pipecat.serializers.telnyx.TelnyxFrameSerializer`` (installed in this
  venv), not a stub — AC-1 pins the concrete class, so a hand-rolled fake
  would certify nothing.
* ``TELNYX_API_KEY`` / ``TELNYX_ALLOWED_CALLER`` are **unset** in the primary
  cases.  That is the placeholder state this whole cascade ships in (spec
  L33), not an edge case, so "fail closed" is asserted there first.
* The WebSocket route is driven through Starlette's own ``TestClient``
  (``httpx`` is available in this venv).  ``build_telnyx_serializer`` is
  replaced by a call-recording spy on *both* the defining module and, when
  present, ``jarvis.server`` — so the composition assertion holds whichever
  import style the route uses, and a disallowed caller reaching the builder
  is a hard failure rather than an invisible one.
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from pipecat.serializers.telnyx import TelnyxFrameSerializer
from starlette.websockets import WebSocketDisconnect

import jarvis.server as server_mod
from jarvis.transports import telnyx as telnyx_mod
from jarvis.transports.telnyx import build_telnyx_serializer, is_allowed_caller

ALLOWED = "+15551234567"
OTHER = "+19998887777"
THIRD = "+14445556666"
STREAM_ID = "stream-abc-123"
CALL_CONTROL_ID = "v3:call-control-xyz"


# --------------------------------------------------------------------------
# helpers
# --------------------------------------------------------------------------

def _start_event(from_number: str, *, include_from: bool = True) -> dict:
    """A Telnyx media-streaming ``start`` event, mirroring the real payload.

    ``stream_id`` appears both at the top level and under ``start`` exactly as
    Telnyx sends it; spec L12 names ``start.stream_id`` and ``start.from``.
    """
    start = {
        "user_id": "user-1",
        "call_control_id": CALL_CONTROL_ID,
        "client_state": None,
        "stream_id": STREAM_ID,
        "media_format": {"encoding": "PCMU", "sample_rate": 8000, "channels": 1},
        "to": "+15550000000",
    }
    if include_from:
        start["from"] = from_number
    return {
        "event": "start",
        "sequence_number": "1",
        "start": start,
        "stream_id": STREAM_ID,
    }


class _SerializerSpy:
    """Stands in for ``build_telnyx_serializer`` and records every call."""

    def __init__(self) -> None:
        self.calls: list[tuple] = []

    def __call__(self, stream_id, call_control_id=None, *args, **kwargs):
        self.calls.append((stream_id, call_control_id))
        return object()


@pytest.fixture
def client() -> TestClient:
    return TestClient(server_mod.app)


@pytest.fixture
def serializer_spy(monkeypatch) -> _SerializerSpy:
    spy = _SerializerSpy()
    monkeypatch.setattr(telnyx_mod, "build_telnyx_serializer", spy)
    if hasattr(server_mod, "build_telnyx_serializer"):
        monkeypatch.setattr(server_mod, "build_telnyx_serializer", spy)
    return spy


def _drive_start_event(client: TestClient, payload: dict) -> bool:
    """Send one ``start`` event; return True if the server closed on us."""
    try:
        with client.websocket_connect("/ws/telnyx") as ws:
            ws.send_json(payload)
            try:
                ws.receive_text()
            except WebSocketDisconnect:
                return True
            return False
    except WebSocketDisconnect:
        return True


# --------------------------------------------------------------------------
# AC-1: build_telnyx_serializer
# --------------------------------------------------------------------------

def test_serializer_is_built_with_pcmu_both_ways_and_the_env_api_key(monkeypatch):
    monkeypatch.setenv("TELNYX_API_KEY", "fake-telnyx-key")

    serializer = build_telnyx_serializer(STREAM_ID, call_control_id=CALL_CONTROL_ID)

    assert isinstance(serializer, TelnyxFrameSerializer)
    assert (
        serializer._params.outbound_encoding,
        serializer._params.inbound_encoding,
    ) == ("PCMU", "PCMU")
    assert (serializer._stream_id, serializer._call_control_id, serializer._api_key) == (
        STREAM_ID,
        CALL_CONTROL_ID,
        "fake-telnyx-key",
    )


def test_the_api_key_is_read_at_call_time_not_at_import_time(monkeypatch):
    monkeypatch.setenv("TELNYX_API_KEY", "first-key")
    first = build_telnyx_serializer(STREAM_ID, call_control_id=CALL_CONTROL_ID)
    monkeypatch.setenv("TELNYX_API_KEY", "second-key")
    second = build_telnyx_serializer(STREAM_ID, call_control_id=CALL_CONTROL_ID)

    assert (first._api_key, second._api_key) == ("first-key", "second-key")


def test_no_api_key_is_invented_when_the_env_var_is_unset(monkeypatch):
    # Placeholder state (spec L5/L33): the serializer, not this function, owns
    # what a missing key means — so either pipecat's own credential error
    # surfaces unmodified, or the serializer carries an empty key.  What must
    # never happen is a hardcoded fallback credential.
    monkeypatch.delenv("TELNYX_API_KEY", raising=False)

    try:
        serializer = build_telnyx_serializer(STREAM_ID, call_control_id=CALL_CONTROL_ID)
    except ValueError as exc:
        assert "api_key" in str(exc)
    else:
        assert not serializer._api_key


# --------------------------------------------------------------------------
# AC-2: is_allowed_caller — exact match, fail closed
# --------------------------------------------------------------------------

@pytest.mark.parametrize(
    "configured, caller, expected",
    [
        # --- placeholder state: nothing configured, nobody gets in (L11) ---
        (None, ALLOWED, False),
        ("", ALLOWED, False),
        ("   ", ALLOWED, False),
        (None, "", False),
        ("", "", False),
        ("   ", "", False),
        (ALLOWED + ",", "", False),
        # --- configured: exact match only ---
        (ALLOWED, ALLOWED, True),
        (ALLOWED, "  " + ALLOWED + "\t", True),
        (" " + ALLOWED + " ", ALLOWED, True),
        (ALLOWED + "," + OTHER, ALLOWED, True),
        (ALLOWED + ", " + OTHER, OTHER, True),
        (ALLOWED + ", " + OTHER + " , " + THIRD, THIRD, True),
        (ALLOWED + ", " + OTHER + " , " + THIRD, OTHER, True),
        (ALLOWED, OTHER, False),
        (ALLOWED, ALLOWED[:-1], False),
        (ALLOWED, ALLOWED + "8", False),
        (ALLOWED, ALLOWED.lstrip("+"), False),
        (ALLOWED + "," + OTHER, "", False),
        (ALLOWED + "," + OTHER, THIRD, False),
    ],
)
def test_is_allowed_caller_fails_closed_and_matches_exactly(
    monkeypatch, configured, caller, expected
):
    if configured is None:
        monkeypatch.delenv("TELNYX_ALLOWED_CALLER", raising=False)
    else:
        monkeypatch.setenv("TELNYX_ALLOWED_CALLER", configured)

    assert is_allowed_caller(caller) is expected


# --------------------------------------------------------------------------
# AC-4: GET /health
# --------------------------------------------------------------------------

def test_health_route_reports_ok(client):
    response = client.get("/health")

    assert response.status_code == 200
    assert response.json().get("status") == "ok"


def test_the_cascade_a_routes_survive_alongside_the_new_ones():
    paths = {getattr(route, "path", None) for route in server_mod.app.routes}

    assert {"/events", "/health", "/ws/telnyx"} <= paths
    assert server_mod.HOST == "127.0.0.1"


# --------------------------------------------------------------------------
# AC-3: /ws/telnyx — the allow-list gate runs before the serializer is built
# --------------------------------------------------------------------------

def test_a_disallowed_caller_is_closed_and_never_reaches_the_serializer(
    client, serializer_spy, monkeypatch
):
    monkeypatch.setenv("TELNYX_ALLOWED_CALLER", ALLOWED)

    closed = _drive_start_event(client, _start_event(OTHER))

    assert closed is True
    assert serializer_spy.calls == []


def test_an_unset_allow_list_closes_even_the_number_the_user_will_call_from(
    client, serializer_spy, monkeypatch
):
    # The placeholder state again: no TELNYX_ALLOWED_CALLER in the environment.
    monkeypatch.delenv("TELNYX_ALLOWED_CALLER", raising=False)

    closed = _drive_start_event(client, _start_event(ALLOWED))

    assert closed is True
    assert serializer_spy.calls == []


def test_a_start_event_with_no_caller_number_is_closed_not_crashed(
    client, serializer_spy, monkeypatch
):
    monkeypatch.setenv("TELNYX_ALLOWED_CALLER", ALLOWED)

    closed = _drive_start_event(client, _start_event(ALLOWED, include_from=False))

    assert closed is True
    assert serializer_spy.calls == []


def test_an_allowed_caller_does_reach_the_serializer_builder(
    client, serializer_spy, monkeypatch
):
    # The other half of the gate assertion: without this, "never called" above
    # would pass against a route that never builds a serializer at all.
    monkeypatch.setenv("TELNYX_ALLOWED_CALLER", ALLOWED)
    monkeypatch.setenv("TELNYX_API_KEY", "fake-telnyx-key")
    ran = []

    async def fake_run_call(websocket, serializer, store):
        ran.append(serializer)

    monkeypatch.setattr(server_mod, "run_call", fake_run_call)
    client = TestClient(server_mod.create_app(server_mod.store))

    with client.websocket_connect("/ws/telnyx") as ws:
        ws.send_json(_start_event(ALLOWED))

    assert [call[0] for call in serializer_spy.calls] == [STREAM_ID]
    assert len(ran) == 1


def test_connected_event_before_start_is_tolerated(serializer_spy, monkeypatch):
    monkeypatch.setenv("TELNYX_ALLOWED_CALLER", ALLOWED)
    ran = []

    async def fake_run_call(websocket, serializer, store):
        ran.append(serializer)

    monkeypatch.setattr(server_mod, "run_call", fake_run_call)
    client = TestClient(server_mod.create_app(server_mod.store))
    with client.websocket_connect("/ws/telnyx") as ws:
        ws.send_json({"event": "connected", "version": "1.0.0"})
        ws.send_json(_start_event(ALLOWED))
    assert len(ran) == 1


def test_texml_points_telnyx_at_our_websocket(monkeypatch):
    client = TestClient(server_mod.app)
    monkeypatch.delenv("JARVIS_PUBLIC_HOSTNAME", raising=False)
    assert client.post("/texml").status_code == 503
    monkeypatch.setenv("JARVIS_PUBLIC_HOSTNAME", "box.example.ts.net")
    r = client.post("/texml")
    assert r.status_code == 200
    assert 'url="wss://box.example.ts.net/ws/telnyx"' in r.text
    assert "<Connect>" in r.text and "bidirectionalMode" in r.text

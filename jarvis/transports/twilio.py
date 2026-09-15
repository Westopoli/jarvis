"""Twilio helpers: TwiML for the voice webhook, media-stream serializer,
webhook signature verification.

Call flow: you dial the Twilio number -> Twilio POSTs ``/twiml`` (form
encoded, includes ``From``) -> we answer with TwiML that opens a
bidirectional media stream to ``wss://<host>/ws/twilio`` and passes the
caller number along as a stream parameter (Twilio's ``start`` event does not
carry it otherwise) -> the WebSocket route checks the allow-list and runs
the pipeline.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import os
from xml.sax.saxutils import quoteattr

from pipecat.serializers.twilio import TwilioFrameSerializer


def twiml_for(hostname: str, from_number: str) -> str:
    return (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        "<Response>\n"
        "  <Connect>\n"
        f'    <Stream url="wss://{hostname}/ws/twilio">\n'
        f"      <Parameter name=\"from_number\" value={quoteattr(from_number)} />\n"
        "    </Stream>\n"
        "  </Connect>\n"
        "</Response>\n"
    )


def build_twilio_serializer(stream_sid: str, call_sid: str | None = None) -> TwilioFrameSerializer:
    """Serializer for one call. Account SID + auth token let it hang up the
    call when the pipeline ends; both read from the environment at call time."""
    account_sid = os.environ.get("TWILIO_ACCOUNT_SID") or None
    auth_token = os.environ.get("TWILIO_AUTH_TOKEN") or None
    kwargs = {}
    if not (account_sid and auth_token):
        # Without credentials pipecat cannot hang the call up for us.
        kwargs["params"] = TwilioFrameSerializer.InputParams(auto_hang_up=False)
    return TwilioFrameSerializer(
        stream_sid=stream_sid,
        call_sid=call_sid,
        account_sid=account_sid,
        auth_token=auth_token,
        **kwargs,
    )


def compute_signature(auth_token: str, url: str, params: dict[str, str]) -> str:
    """Twilio's request signature: base64(HMAC-SHA1(token, url + sorted k+v))."""
    payload = url + "".join(f"{k}{params[k]}" for k in sorted(params))
    digest = hmac.new(auth_token.encode(), payload.encode(), hashlib.sha1).digest()
    return base64.b64encode(digest).decode()


def signature_valid(auth_token: str, url: str, params: dict[str, str], signature: str | None) -> bool:
    if not signature:
        return False
    return hmac.compare_digest(compute_signature(auth_token, url, params), signature)

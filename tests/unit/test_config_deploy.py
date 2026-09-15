# spec: specs/cascade-c.md::Acceptance criteria::AC-5..8
"""Tests for the environment config loader and the deploy files (leaf-02,
AC 5-8): ``jarvis/config.py``, ``deploy/jarvis.service``, and
``deploy/tailscale-funnel.service``.

Three deliberate choices:

* The expected defaults below are transcribed from ``.env.example`` — AC-5
  says "the exact same defaults ``.env.example`` documents", so that file is
  the oracle, not the implementation.
* Every env var is **deleted** before the default-reading test, because the
  placeholder state (no ``.env`` loaded, Telnyx/hostname fields blank) is
  the state this cascade actually ships in (spec L5/L33).
* The systemd units are checked by *section-aware* structure — the unit text
  is split into ``[Section]`` blocks and keys are looked up inside the right
  block — rather than a strict ``configparser`` (systemd units are not strict
  INI: they allow repeated keys) or a bare substring test (which would match
  inside a comment).  There is no Python object to assert on for these files.
"""
from __future__ import annotations

import dataclasses
import re
from pathlib import Path

import pytest

from jarvis.config import Config, load_config

REPO_ROOT = Path(__file__).resolve().parents[2]
DEPLOY = REPO_ROOT / "deploy"

# Transcribed from .env.example, minus the two whose Python type the spec does
# not pin (OLLAMA_KEEP_ALIVE, JARVIS_PORT) — those are asserted separately.
EXPECTED_DEFAULTS = {
    "ollama_host": "http://127.0.0.1:11434",
    "ollama_model": "qwen3:8b",
    "jarvis_tmux_session": "main",
    "telnyx_api_key": "",
    "telnyx_public_key": "",
    "telnyx_allowed_caller": "",
    "jarvis_public_hostname": "",
    "jarvis_host": "127.0.0.1",
}

ENV_KEYS = [
    "OLLAMA_HOST",
    "OLLAMA_MODEL",
    "OLLAMA_KEEP_ALIVE",
    "JARVIS_TMUX_SESSION",
    "TELNYX_API_KEY",
    "TELNYX_PUBLIC_KEY",
    "TELNYX_ALLOWED_CALLER",
    "JARVIS_PUBLIC_HOSTNAME",
    "JARVIS_HOST",
    "JARVIS_PORT",
]

OVERRIDES = {
    "OLLAMA_HOST": "http://10.0.0.9:1234",
    "OLLAMA_MODEL": "qwen3:14b",
    "OLLAMA_KEEP_ALIVE": "30m",
    "JARVIS_TMUX_SESSION": "driving",
    "TELNYX_API_KEY": "KEY0123456789",
    "TELNYX_PUBLIC_KEY": "pub0123456789",
    "TELNYX_ALLOWED_CALLER": "+15551234567",
    "JARVIS_PUBLIC_HOSTNAME": "jarvis.example.com",
    "JARVIS_HOST": "0.0.0.0",
    "JARVIS_PORT": "9001",
}


# --------------------------------------------------------------------------
# helpers
# --------------------------------------------------------------------------

@pytest.fixture
def clean_env(monkeypatch):
    """The placeholder state: none of the documented env vars are set."""
    for key in ENV_KEYS:
        monkeypatch.delenv(key, raising=False)
    return monkeypatch


def _unit_sections(text: str) -> dict[str, list[str]]:
    """Split a systemd unit into ``{section: [stripped directive lines]}``."""
    sections: dict[str, list[str]] = {}
    current: str | None = None
    for line in text.splitlines():
        header = re.match(r"^\[([A-Za-z]+)\]\s*$", line)
        if header:
            current = header.group(1)
            sections.setdefault(current, [])
        elif current is not None and line.strip():
            sections[current].append(line.strip())
    return sections


# --------------------------------------------------------------------------
# AC-5: Config / load_config
# --------------------------------------------------------------------------

def test_config_is_a_dataclass():
    assert dataclasses.is_dataclass(Config)


def test_defaults_match_env_example_when_nothing_is_set(clean_env):
    config = load_config(dotenv=False)

    assert {name: getattr(config, name) for name in EXPECTED_DEFAULTS} == EXPECTED_DEFAULTS
    # .env.example documents OLLAMA_KEEP_ALIVE=-1 and JARVIS_PORT=8000; the
    # spec does not pin whether they land as str or int, so compare by value.
    assert str(config.ollama_keep_alive) == "-1"
    assert int(config.jarvis_port) == 8000


def test_every_documented_env_var_overrides_its_default(clean_env):
    for key, value in OVERRIDES.items():
        clean_env.setenv(key, value)

    config = load_config(dotenv=False)

    assert {name: getattr(config, name) for name in EXPECTED_DEFAULTS} == {
        "ollama_host": "http://10.0.0.9:1234",
        "ollama_model": "qwen3:14b",
        "jarvis_tmux_session": "driving",
        "telnyx_api_key": "KEY0123456789",
        "telnyx_public_key": "pub0123456789",
        "telnyx_allowed_caller": "+15551234567",
        "jarvis_public_hostname": "jarvis.example.com",
        "jarvis_host": "0.0.0.0",
    }
    assert (str(config.ollama_keep_alive), int(config.jarvis_port)) == ("30m", 9001)


@pytest.mark.parametrize(
    "api_key, allowed_caller, expected",
    [
        (None, None, False),          # the placeholder state .env.example ships
        ("", "", False),
        ("KEY0123456789", None, False),
        ("KEY0123456789", "", False),
        (None, "+15551234567", False),
        ("", "+15551234567", False),
        ("KEY0123456789", "+15551234567", True),
    ],
)
def test_telnyx_configured_requires_both_credentials(
    clean_env, api_key, allowed_caller, expected
):
    if api_key is not None:
        clean_env.setenv("TELNYX_API_KEY", api_key)
    if allowed_caller is not None:
        clean_env.setenv("TELNYX_ALLOWED_CALLER", allowed_caller)

    assert load_config(dotenv=False).telnyx_configured is expected


# --------------------------------------------------------------------------
# AC-6: deploy/jarvis.service
# --------------------------------------------------------------------------

def test_jarvis_unit_has_the_three_systemd_sections():
    sections = _unit_sections((DEPLOY / "jarvis.service").read_text())

    assert set(sections) >= {"Unit", "Service", "Install"}


def test_jarvis_unit_starts_the_server_through_uv_run():
    service = _unit_sections((DEPLOY / "jarvis.service").read_text())["Service"]

    assert any(re.match(r"^ExecStart=.*\buv\s+run\b", line) for line in service)


def test_jarvis_unit_declares_a_working_directory():
    service = _unit_sections((DEPLOY / "jarvis.service").read_text())["Service"]

    assert any(line.startswith("WorkingDirectory=") for line in service)


def test_jarvis_unit_pins_ollama_keep_alive_in_the_service_section():
    service = _unit_sections((DEPLOY / "jarvis.service").read_text())["Service"]

    assert "Environment=OLLAMA_KEEP_ALIVE=-1" in service


# --------------------------------------------------------------------------
# AC-7/AC-8: deploy/tailscale-funnel.service
#
# Tailscale Funnel needs no separate YAML config the way cloudflared did —
# the exposed port and hostname are driven entirely by the `tailscale
# funnel` CLI invocation plus the already-running `tailscaled` daemon's own
# state, so one systemd unit replaces cloudflared.yml + cloudflared.service.
# --------------------------------------------------------------------------

def test_tailscale_funnel_unit_has_the_three_systemd_sections():
    sections = _unit_sections((DEPLOY / "tailscale-funnel.service").read_text())

    assert set(sections) >= {"Unit", "Service", "Install"}


def test_tailscale_funnel_unit_starts_the_funnel_on_the_default_port():
    service = _unit_sections((DEPLOY / "tailscale-funnel.service").read_text())["Service"]

    assert any(
        re.match(r"^ExecStart=.*\btailscale\b.*\bfunnel\b.*\b8000\b", line) for line in service
    )


def test_tailscale_funnel_unit_stays_active_after_the_oneshot_command_exits():
    """`tailscale funnel --bg` backgrounds itself and exits; RemainAfterExit
    is what keeps systemd reporting the unit as active rather than failed."""
    service = _unit_sections((DEPLOY / "tailscale-funnel.service").read_text())["Service"]

    assert any(re.match(r"^Type=oneshot\s*$", line) for line in service)
    assert any(re.match(r"^RemainAfterExit=yes\s*$", line) for line in service)

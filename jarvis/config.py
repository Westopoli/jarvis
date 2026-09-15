"""Environment config loader (spec: specs/cascade-c.md::spec_lines 15-19,
AC-5).

``Config`` fields are the mechanical lowercase of each documented env var
name (keeping its ``jarvis_``/``telnyx_``/``ollama_`` prefix). Defaults are
transcribed verbatim from ``.env.example``.
"""
from __future__ import annotations

import dataclasses
import os


@dataclasses.dataclass
class Config:
    ollama_host: str
    ollama_model: str
    ollama_keep_alive: str
    jarvis_tmux_session: str
    telnyx_api_key: str
    telnyx_public_key: str
    telnyx_allowed_caller: str
    jarvis_public_hostname: str
    jarvis_host: str
    jarvis_port: int
    jarvis_rename_windows: bool = True

    @property
    def telnyx_configured(self) -> bool:
        return bool(self.telnyx_api_key) and bool(self.telnyx_allowed_caller)


def load_config() -> Config:
    return Config(
        ollama_host=os.environ.get("OLLAMA_HOST", "http://127.0.0.1:11434"),
        ollama_model=os.environ.get("OLLAMA_MODEL", "qwen3:8b"),
        ollama_keep_alive=os.environ.get("OLLAMA_KEEP_ALIVE", "-1"),
        jarvis_tmux_session=os.environ.get("JARVIS_TMUX_SESSION", "main"),
        telnyx_api_key=os.environ.get("TELNYX_API_KEY", ""),
        telnyx_public_key=os.environ.get("TELNYX_PUBLIC_KEY", ""),
        telnyx_allowed_caller=os.environ.get("TELNYX_ALLOWED_CALLER", ""),
        jarvis_public_hostname=os.environ.get("JARVIS_PUBLIC_HOSTNAME", ""),
        jarvis_host=os.environ.get("JARVIS_HOST", "127.0.0.1"),
        jarvis_port=int(os.environ.get("JARVIS_PORT", "8000")),
        jarvis_rename_windows=os.environ.get("JARVIS_RENAME_WINDOWS", "1") not in ("0", "false", "no"),
    )

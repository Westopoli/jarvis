"""Environment config loader (spec: specs/cascade-c.md::spec_lines 15-19,
AC-5).

``Config`` fields are the mechanical lowercase of each documented env var
name (keeping its ``jarvis_``/``telnyx_``/``ollama_`` prefix). Defaults are
transcribed verbatim from ``.env.example``.
"""
from __future__ import annotations

import dataclasses
import os
from pathlib import Path

DOTENV = Path(__file__).resolve().parent.parent / ".env"


def load_dotenv(path: Path = DOTENV) -> None:
    """Load KEY=VALUE lines from the repo's .env into os.environ.

    Real environment variables win; the file only fills in what is unset.
    The file is gitignored: it holds Twilio credentials and your number.
    """
    if os.environ.get("JARVIS_DOTENV", "1") in ("0", "false", "no") or not path.is_file():
        return
    for raw in path.read_text().splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key, value = key.strip(), value.strip().strip("'\"")
        if key and key not in os.environ:
            os.environ[key] = value


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
    llm_provider: str = "ollama"
    groq_api_key: str = ""
    groq_model: str = "openai/gpt-oss-120b"
    deepseek_api_key: str = ""
    deepseek_model: str = "deepseek-chat"

    @property
    def telnyx_configured(self) -> bool:
        return bool(self.telnyx_api_key) and bool(self.telnyx_allowed_caller)


def load_config(*, dotenv: bool = True) -> Config:
    if dotenv:
        load_dotenv()
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
        llm_provider=os.environ.get("JARVIS_LLM_PROVIDER", "ollama"),
        groq_api_key=os.environ.get("GROQ_API_KEY", ""),
        groq_model=os.environ.get("GROQ_MODEL", "openai/gpt-oss-120b"),
        deepseek_api_key=os.environ.get("DEEPSEEK_API_KEY", ""),
        deepseek_model=os.environ.get("DEEPSEEK_MODEL", "deepseek-chat"),
    )

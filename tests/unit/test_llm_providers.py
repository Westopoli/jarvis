"""LLM backend dispatch: local Ollama (default) vs. Groq with auto-failover.

No network calls anywhere here. Constructing an ``OpenAILLMService``
subclass never touches the network (only its lazily-created HTTP client
does, on first real request), so these tests build real Groq/Ollama service
objects and assert on their configuration and on-object behavior directly,
offline.
"""
from __future__ import annotations

import pytest
from pipecat.pipeline.llm_switcher import LLMSwitcher
from pipecat.pipeline.service_switcher import ServiceSwitcherStrategyFailover
from pipecat.services.deepseek.llm import DeepSeekLLMService
from pipecat.services.ollama.llm import OLLamaLLMService

from jarvis import llm_providers
from jarvis.config import Config
from jarvis.llm_providers import build_deepseek_llm, build_groq_llm, build_llm, build_ollama_llm


def _cfg(**overrides) -> Config:
    defaults = dict(
        ollama_host="http://127.0.0.1:11434",
        ollama_model="qwen3:8b",
        ollama_keep_alive="-1",
        jarvis_tmux_session="main",
        telnyx_api_key="",
        telnyx_public_key="",
        telnyx_allowed_caller="",
        jarvis_public_hostname="",
        jarvis_host="127.0.0.1",
        jarvis_port=8000,
        jarvis_rename_windows=True,
        llm_provider="ollama",
        groq_api_key="",
        groq_model="openai/gpt-oss-120b",
        deepseek_api_key="",
        deepseek_model="deepseek-chat",
    )
    defaults.update(overrides)
    return Config(**defaults)


def test_build_ollama_llm_uses_configured_host_model_and_reasoning_extra():
    cfg = _cfg(ollama_host="http://10.0.0.9:1234", ollama_model="qwen3:14b")

    llm = build_ollama_llm(cfg)

    assert isinstance(llm, OLLamaLLMService)
    assert llm._settings.model == "qwen3:14b"
    assert llm._settings.extra == {"extra_body": {"reasoning_effort": "none"}}


def test_build_llm_defaults_to_plain_ollama_service():
    llm = build_llm(_cfg(llm_provider="ollama"))

    assert isinstance(llm, OLLamaLLMService)
    assert not isinstance(llm, LLMSwitcher)


def test_build_llm_groq_without_api_key_fails_fast(monkeypatch):
    built = []

    def _spy_build_groq(cfg):
        built.append(1)
        raise AssertionError("build_groq_llm should never be called without a key")

    monkeypatch.setattr(llm_providers, "build_groq_llm", _spy_build_groq)

    with pytest.raises(RuntimeError, match="GROQ_API_KEY"):
        build_llm(_cfg(llm_provider="groq", groq_api_key=""))

    assert built == []  # never even attempted to construct a Groq client


def test_build_llm_groq_with_key_returns_a_failover_switcher():
    result = build_llm(_cfg(llm_provider="groq", groq_api_key="gsk_test_key"))

    assert isinstance(result, LLMSwitcher)
    assert isinstance(result.strategy, ServiceSwitcherStrategyFailover)
    assert len(result.llms) == 2
    assert isinstance(result.llms[1], OLLamaLLMService)
    assert result.active_llm is result.llms[0]  # starts on Groq, list order


def test_build_llm_rejects_unknown_provider():
    with pytest.raises(ValueError, match="bogus"):
        build_llm(_cfg(llm_provider="bogus"))


def test_build_groq_llm_uses_configured_model_and_key():
    llm = build_groq_llm(
        _cfg(llm_provider="groq", groq_api_key="gsk_test_key", groq_model="openai/gpt-oss-20b")
    )

    assert llm._settings.model == "openai/gpt-oss-20b"


async def test_groq_llm_forces_unusable_on_any_error_not_only_permanent_ones():
    """The regression guard for the failover-gap fix: a plain connectivity-
    shaped error (no status code -> ErrorCategory.CONNECTIVITY/UNKNOWN, never
    permanent) must still flip is_usable, or ServiceSwitcherStrategyFailover
    silently never fails over to Ollama."""
    llm = build_groq_llm(_cfg(llm_provider="groq", groq_api_key="gsk_test_key"))
    assert llm.is_usable is True

    await llm.push_error("boom", exception=RuntimeError("connection refused"))

    assert llm.is_usable is False


def test_build_deepseek_llm_uses_the_non_reasoning_chat_endpoint_by_default():
    llm = build_deepseek_llm(_cfg(llm_provider="deepseek", deepseek_api_key="sk_test_key"))

    assert isinstance(llm, DeepSeekLLMService)
    assert llm._settings.model == "deepseek-chat"


def test_build_llm_deepseek_without_api_key_fails_fast(monkeypatch):
    built = []

    def _spy_build_deepseek(cfg):
        built.append(1)
        raise AssertionError("build_deepseek_llm should never be called without a key")

    monkeypatch.setattr(llm_providers, "build_deepseek_llm", _spy_build_deepseek)

    with pytest.raises(RuntimeError, match="DEEPSEEK_API_KEY"):
        build_llm(_cfg(llm_provider="deepseek", deepseek_api_key=""))

    assert built == []


def test_build_llm_deepseek_with_key_returns_a_failover_switcher():
    result = build_llm(_cfg(llm_provider="deepseek", deepseek_api_key="sk_test_key"))

    assert isinstance(result, LLMSwitcher)
    assert isinstance(result.strategy, ServiceSwitcherStrategyFailover)
    assert len(result.llms) == 2
    assert isinstance(result.llms[1], OLLamaLLMService)
    assert result.active_llm is result.llms[0]  # starts on DeepSeek, list order


async def test_deepseek_llm_forces_unusable_on_any_error_not_only_permanent_ones():
    """Same failover-gap fix as Groq: BaseOpenAILLMService's own error path
    (shared by every OpenAI-compatible subclass, DeepSeek included) never
    forces permanence on its own, so ServiceSwitcherStrategyFailover would
    silently never fail over to Ollama without this."""
    llm = build_deepseek_llm(_cfg(llm_provider="deepseek", deepseek_api_key="sk_test_key"))
    assert llm.is_usable is True

    await llm.push_error("boom", exception=RuntimeError("connection refused"))

    assert llm.is_usable is False


def test_deepseek_model_can_be_overridden():
    llm = build_deepseek_llm(
        _cfg(llm_provider="deepseek", deepseek_api_key="sk_test_key", deepseek_model="deepseek-reasoner")
    )

    assert llm._settings.model == "deepseek-reasoner"


async def test_plain_ollama_llm_is_not_affected_by_the_failover_wrapper():
    """Only the Groq member gets the always-unusable-on-error treatment --
    Ollama is the last resort, nothing to fail over to from it."""
    llm = build_ollama_llm(_cfg())

    await llm.push_error("boom", exception=RuntimeError("connection refused"))

    assert llm.is_usable is True

"""LLM backend selection: local Ollama (default) or Groq's cloud API, with
automatic failover from Groq back to Ollama if Groq errors mid-call.

Config-only trigger (``JARVIS_LLM_PROVIDER``, restart to change) -- no voice
command, no mid-call manual override. The default path (``"ollama"``, unset)
returns a single plain LLM service exactly as before; only ``"groq"`` builds
both services and wraps them, so the common case's pipeline shape and
behavior are completely unchanged by this module's existence.

The failover gap this closes: Pipecat's ``ServiceSwitcherStrategyFailover``
only fails over once the active service is already ``is_usable == False``
(``service_switcher.py``'s ``handle_error``: ``if failed_service.is_usable:
return None``), and a service only becomes unusable on a *permanent*-category
error (auth/invalid-request) or when ``force_treat_as_permanent=True`` is
passed to ``push_error`` -- confirmed by reading
``BaseOpenAILLMService``'s error handler, which calls ``push_error`` with
neither. The realistic "Groq had a bad moment" cases (timeouts, connection
errors, 5xx, rate limits) are not permanent, so wired together unmodified,
those errors would never trigger failover at all. ``_FailoverGroqLLMService``
forces every error from Groq to count, unconditionally.
"""
from __future__ import annotations

from pipecat.pipeline.llm_switcher import LLMSwitcher
from pipecat.pipeline.service_switcher import ServiceSwitcherStrategyFailover
from pipecat.services.deepseek.llm import DeepSeekLLMService
from pipecat.services.groq.llm import GroqLLMService
from pipecat.services.ollama.llm import OLLamaLLMService

from jarvis.config import Config


class _FailoverGroqLLMService(GroqLLMService):
    """Groq member of the failover switcher: any error makes it unusable,
    not only a permanent-category one, so a transient outage still triggers
    an immediate switch to Ollama instead of silently doing nothing."""

    async def push_error(self, *args, **kwargs):
        kwargs["force_treat_as_permanent"] = True
        await super().push_error(*args, **kwargs)


def build_ollama_llm(cfg: Config) -> OLLamaLLMService:
    """The local backend. Identical to what jarvis has always used."""
    return OLLamaLLMService(
        base_url=cfg.ollama_host.rstrip("/") + "/v1",
        settings=OLLamaLLMService.Settings(
            model=cfg.ollama_model,
            temperature=0.2,
            # Ollama's OpenAI-compatible endpoint ignores `think`; it honours
            # reasoning_effort. Without this qwen3 burns 100-170 hidden
            # reasoning tokens per call (~2.5 s on the 3060) before answering.
            extra={"extra_body": {"reasoning_effort": "none"}},
        ),
    )


def build_groq_llm(cfg: Config) -> GroqLLMService:
    """The cloud backend. Fails fast, before any network-capable object is
    even constructed, if no API key is configured."""
    if not cfg.groq_api_key:
        raise RuntimeError(
            "JARVIS_LLM_PROVIDER=groq requires GROQ_API_KEY to be set in .env"
        )
    return _FailoverGroqLLMService(
        api_key=cfg.groq_api_key,
        settings=GroqLLMService.Settings(
            model=cfg.groq_model,
            temperature=0.2,
            # Mirrors the Ollama shape above -- neither provider exposes a
            # first-class reasoning_effort field in this Pipecat version, so
            # the OpenAI SDK's extra_body passthrough is the only mechanism.
            # Whether Groq's live API honours it is verified in the gated
            # desktop A/B test, not assumed here.
            extra={"extra_body": {"reasoning_effort": "low"}},
        ),
    )


class _FailoverDeepSeekLLMService(DeepSeekLLMService):
    """DeepSeek member of the failover switcher: same fix as Groq's, for the
    same reason -- BaseOpenAILLMService's error path is shared by every
    OpenAI-compatible subclass, so the gap isn't provider-specific."""

    async def push_error(self, *args, **kwargs):
        kwargs["force_treat_as_permanent"] = True
        await super().push_error(*args, **kwargs)


def build_deepseek_llm(cfg: Config) -> DeepSeekLLMService:
    """The DeepSeek cloud backend.

    Defaults to ``deepseek-chat`` (DeepSeek's non-reasoning endpoint), not
    ``deepseek-reasoner`` -- the reasoning endpoint "thinks" before every
    reply, the same multi-second-TTFT trap already hit twice in this project
    (Ollama's qwen3, and the reason Groq's gpt-oss needs reasoning_effort
    suppressed). deepseek-chat needs no such workaround; it's non-reasoning
    by choice of endpoint, not by a settable flag.
    """
    if not cfg.deepseek_api_key:
        raise RuntimeError(
            "JARVIS_LLM_PROVIDER=deepseek requires DEEPSEEK_API_KEY to be set in .env"
        )
    return _FailoverDeepSeekLLMService(
        api_key=cfg.deepseek_api_key,
        settings=DeepSeekLLMService.Settings(model=cfg.deepseek_model, temperature=0.2),
    )


def build_llm(cfg: Config):
    """Dispatch on ``cfg.llm_provider``.

    ``"ollama"`` (default): a single plain service, pipeline shape unchanged.
    ``"groq"`` / ``"deepseek"``: an ``LLMSwitcher`` starting active on the
    cloud service (list order -- ``ServiceSwitcherStrategy`` picks
    ``services[0]``), falling over to Ollama on any cloud-service error via
    ``ServiceSwitcherStrategyFailover``.
    """
    provider = (cfg.llm_provider or "ollama").strip().lower()
    if provider == "ollama":
        return build_ollama_llm(cfg)
    if provider == "groq":
        if not cfg.groq_api_key:
            raise RuntimeError(
                "JARVIS_LLM_PROVIDER=groq requires GROQ_API_KEY to be set in .env"
            )
        cloud_llm = build_groq_llm(cfg)
    elif provider == "deepseek":
        if not cfg.deepseek_api_key:
            raise RuntimeError(
                "JARVIS_LLM_PROVIDER=deepseek requires DEEPSEEK_API_KEY to be set in .env"
            )
        cloud_llm = build_deepseek_llm(cfg)
    else:
        raise ValueError(
            f"unknown JARVIS_LLM_PROVIDER: {provider!r} (expected 'ollama', 'groq', or 'deepseek')"
        )
    ollama_llm = build_ollama_llm(cfg)
    return LLMSwitcher(llms=[cloud_llm, ollama_llm], strategy_type=ServiceSwitcherStrategyFailover)

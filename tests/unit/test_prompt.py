"""Jarvis must never claim to be fully local when the LLM is actually Groq.

jarvis/prompt.py:18-20 tells the user "nothing sent to the cloud except the
Claude sessions themselves" -- true for the default Ollama backend, false
the moment JARVIS_LLM_PROVIDER=groq. build_system_prompt(llm_provider) keeps
that claim accurate to whichever backend is actually active.
"""
from __future__ import annotations

from jarvis.prompt import SYSTEM_PROMPT, build_system_prompt

_LOCAL_CLAIM = "nothing sent to the cloud except the Claude sessions"


def test_ollama_prompt_still_makes_the_fully_local_claim():
    prompt = build_system_prompt("ollama")

    assert "Ollama" in prompt
    assert _LOCAL_CLAIM in prompt


def test_groq_prompt_mentions_groq_and_drops_the_fully_local_claim():
    prompt = build_system_prompt("groq")

    assert "Groq" in prompt
    assert _LOCAL_CLAIM not in prompt


def test_default_provider_falls_back_to_ollama_wording():
    assert build_system_prompt(None) == build_system_prompt("ollama")


def test_module_level_system_prompt_alias_still_exists_and_matches_ollama():
    """Back-compat: anything still importing the old flat constant keeps
    working, unchanged from before this feature."""
    assert SYSTEM_PROMPT == build_system_prompt("ollama")


def test_the_two_variants_share_everything_except_the_how_you_work_sentence():
    """A regression guard against an accidental prompt-merge mistake: the
    two variants should differ only in the local/cloud sentence, not in
    persona, tool rules, or anything else."""
    ollama_prompt = build_system_prompt("ollama")
    groq_prompt = build_system_prompt("groq")

    assert ollama_prompt.split("\n\n")[0] == groq_prompt.split("\n\n")[0]  # persona intro
    assert ollama_prompt.split("Tool rules:")[1] == groq_prompt.split("Tool rules:")[1]

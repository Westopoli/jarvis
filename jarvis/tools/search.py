"""Find which tmux tab a spoken topic refers to.

"The chat where we talked about the phone stuff" -> scan every window's
recent pane text (plus the last Claude message we got from hooks), score by
how many of the topic's words appear, and rank. Pure keyword matching, no
LLM: fast, deterministic, and good enough because the tab that discussed a
topic mentions its words dozens of times.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

from jarvis.events import EventStore
from jarvis.tools import tmux as tmux_tools
from jarvis.types import TmuxWindow

_STOPWORDS = {
    "the", "a", "an", "and", "or", "of", "to", "in", "on", "at", "for", "with",
    "about", "that", "this", "those", "these", "it", "its", "is", "was", "were",
    "be", "we", "i", "you", "he", "she", "they", "me", "my", "our", "your",
    "tab", "tabs", "chat", "chats", "window", "session", "one", "where", "which",
    "what", "when", "talked", "talking", "discussed", "discussing", "working",
    "worked", "doing", "did", "stuff", "thing", "things", "conversation",
    "claude", "jarvis", "please", "find", "go", "switch", "open", "up",
}
_PANE_LINES = 1500
_MIN_TERM_LEN = 3
_PER_TERM_CAP = 8
_PREFIX_LEN = 6  # "narration" -> "narrat" also hits "narrator"
_MIN_SCORE = 3.0  # a single stray word is not a match


@dataclass(frozen=True)
class TabMatch:
    tab: int
    name: str
    score: float
    terms_hit: int
    terms_total: int
    snippet: str


def topic_terms(topic: str) -> list[str]:
    words = re.findall(r"[a-z0-9][a-z0-9_\-]*", topic.lower())
    terms = []
    for w in words:
        w = w.strip("-_")
        if len(w) < _MIN_TERM_LEN or w in _STOPWORDS:
            continue
        # crude stemming so "phones" hits "phone", "calling" hits "call"
        for suffix in ("ing", "es", "s", "ed"):
            if len(w) > 4 and w.endswith(suffix):
                w = w[: -len(suffix)]
                break
        if w not in terms:
            terms.append(w)
    return terms


def score_text(terms: list[str], text: str) -> tuple[float, int, str]:
    """(score, terms hit, best snippet line)."""
    low = text.lower()
    score = 0.0
    hit = 0
    for term in terms:
        needle = term[:_PREFIX_LEN] if len(term) > _PREFIX_LEN else term
        count = len(re.findall(r"\b" + re.escape(needle), low))
        if count:
            hit += 1
            score += min(count, _PER_TERM_CAP)
    if terms and hit == len(terms):
        score *= 1.5  # every word present: strong signal
    snippet = ""
    if hit:
        best_line, best_hits = "", 0
        for line in text.splitlines():
            l = line.lower()
            n = sum(1 for t in terms if t in l)
            if n > best_hits and len(line.strip()) > 10:
                best_line, best_hits = line.strip(), n
        snippet = re.sub(r"\s+", " ", best_line)[:160]
    return score, hit, snippet


def find_tabs(
    topic: str,
    store: EventStore | None = None,
    session: str | None = None,
    *,
    windows: list[TmuxWindow] | None = None,
) -> list[TabMatch]:
    terms = topic_terms(topic)
    if not terms:
        return []
    windows = windows if windows is not None else tmux_tools.tmux_list(session=session)
    hook_text: dict[int, str] = {}
    if store is not None:
        for session_id in store.session_ids():
            event = store.get(session_id)
            tab = store.tab_for_session(session_id, windows)
            if event is not None and tab is not None and event.last_message:
                hook_text[tab] = event.last_message
    matches: list[TabMatch] = []
    for w in windows:
        try:
            text = tmux_tools.tmux_read(w.index, _PANE_LINES, session=session)
        except Exception:
            text = ""
        text = f"{hook_text.get(w.index, '')}\n{text}"
        score, hit, snippet = score_text(terms, text)
        if w.pane_command in ("claude", "node"):
            score *= 1.2  # the user usually means a Claude session
        if score >= _MIN_SCORE:
            matches.append(TabMatch(w.index, w.name, round(score, 1), hit, len(terms), snippet))
    matches.sort(key=lambda m: (-m.score, m.tab))
    return matches


def decisive(matches: list[TabMatch]) -> TabMatch | None:
    """The single clear winner, or None when it is ambiguous."""
    if not matches:
        return None
    first = matches[0]
    # Must match most of what the user said, not one stray word.
    if first.terms_hit * 2 < first.terms_total:
        return None
    if len(matches) == 1:
        return first
    second = matches[1]
    if first.score >= 2.0 * second.score and first.terms_hit >= second.terms_hit:
        return first
    return None

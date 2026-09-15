from jarvis.tools import search
from jarvis.tools.search import decisive, find_tabs, score_text, topic_terms
from jarvis.types import TmuxWindow

WINDOWS = [
    TmuxWindow(0, "agora", "/p/agora", "bash"),
    TmuxWindow(2, "agora-claude", "/p/agora", "claude"),
    TmuxWindow(4, "jarvis", "/p/jarvis", "bash"),
    TmuxWindow(6, "om-claude", "/p/om", "claude"),
]
PANES = {
    0: "ls\ngit status\n",
    2: "We need a login form. The login page should validate the password.\nlogin bug fixed in auth.py\n",
    4: "Twilio phone call setup. The funnel publishes /twiml. Telnyx alternative.\nphone number bought\n",
    6: "Refactor the manifold parser. Parser tests green. All tests pass.\n",
}


def _patch(monkeypatch):
    monkeypatch.setattr(search.tmux_tools, "tmux_read", lambda tab, lines, session=None: PANES[tab])


def test_topic_terms_drop_filler_and_stem():
    assert topic_terms("the chat where we talked about phone calls") == ["phone", "call"]
    assert topic_terms("tab about the login bug") == ["login", "bug"]
    assert topic_terms("the one") == []


def test_clear_topic_is_decisive(monkeypatch):
    _patch(monkeypatch)
    m = find_tabs("the phone call setup", windows=WINDOWS)
    assert m[0].tab == 4
    assert decisive(m).tab == 4


def test_claude_tab_preferred_and_ambiguity_detected(monkeypatch):
    _patch(monkeypatch)
    m = find_tabs("the login bug", windows=WINDOWS)
    assert m[0].tab == 2 and m[0].terms_hit == 2
    assert decisive(m).tab == 2
    # "parser" vs "login" both single-word topics elsewhere: one each, no tie
    m = find_tabs("tests", windows=WINDOWS)
    assert [x.tab for x in m] == [6]


def test_no_match_returns_empty(monkeypatch):
    _patch(monkeypatch)
    assert find_tabs("lasagna recipe", windows=WINDOWS) == []
    assert decisive([]) is None


def test_one_stray_word_is_not_decisive(monkeypatch):
    monkeypatch.setattr(search.tmux_tools, "tmux_read",
                        lambda tab, lines, session=None: "recipe recipe recipe" if tab == 6 else "")
    m = find_tabs("lasagna recipe from the manifold", windows=WINDOWS)
    assert [x.tab for x in m] == [6]
    assert decisive(m) is None  # 1 of 3 words


def test_prefix_stemming_hits_related_forms(monkeypatch):
    monkeypatch.setattr(search.tmux_tools, "tmux_read",
                        lambda tab, lines, session=None: "the Narrator pauses on interruption" * 2 if tab == 4 else "")
    m = find_tabs("narration and interruptions", windows=WINDOWS)
    assert m and m[0].tab == 4 and m[0].terms_hit == 2


def test_ambiguous_when_scores_close(monkeypatch):
    monkeypatch.setattr(search.tmux_tools, "tmux_read", lambda tab, lines, session=None: "parser parser")
    m = find_tabs("parser", windows=WINDOWS)
    assert len(m) == 4
    assert decisive(m) is None


def test_score_text_snippet_and_full_hit_bonus():
    score, hit, snippet = score_text(["phone", "call"], "x\nPhone call setup line here\ny")
    assert hit == 2 and score == 3.0 and snippet == "Phone call setup line here"

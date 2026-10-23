from types import SimpleNamespace

import pytest

from app.core.settings import settings
from app.services.agent.tools import terms


def make_ctx():
    ws = {"sections": {"s1": {"text": "LRU evicts the oldest item."}}, "search": {}, "glossary": {}}
    return SimpleNamespace(ctx=SimpleNamespace(workspace=ws)), ws


@pytest.fixture
def stub(monkeypatch):
    """Tavily and the LLM stubbed; returns a dict to steer them."""
    st = {"quota": True, "found": [{"title": "T", "url": "http://a.test/lru", "content": "LRU drops the least used item. " * 20}],
          "definition": "A. B. C."}
    monkeypatch.setattr(terms.limiter, "acquire", lambda *a, **k: st["quota"])
    monkeypatch.setattr(terms.limiter, "remaining", lambda k: 99)
    monkeypatch.setattr(terms.tavily, "search", lambda q, max_results=5: st["found"][:max_results])
    monkeypatch.setattr(terms.text, "chat_json", lambda *a, **k: {"definitions": [
        {"term": "LRU", "definition": st["definition"], "unsure": False}]})
    return st


def test_refuses_term_without_source(stub):
    t, _ = make_ctx()
    r = terms.explain_term({"section": "s1", "terms": ["LRU"]}, t)
    assert r == {"ok": False, "error": "search first: call web_search for 'LRU' and pass the result ids as source"}


def test_refuses_source_not_in_results(stub):
    t, _ = make_ctx()
    r = terms.explain_term({"section": "s1", "terms": ["LRU"], "source": "r9"}, t)
    assert not r["ok"] and "not among the search results" in r["error"]


def test_unsourced_after_quota_exhausted(stub):
    t, ws = make_ctx()
    stub["quota"] = False
    s = terms.web_search({"terms": ["LRU"]}, t)
    assert s["searched"][0]["status"] == "quota exhausted"
    r = terms.explain_term({"section": "s1", "terms": ["LRU"]}, t)
    assert r["ok"] and r["defined"][0]["unsourced"] and r["defined"][0]["source_url"] is None
    assert ws["glossary"]["lru"]["unsourced"] is True


def test_unsourced_after_no_results(stub):
    t, _ = make_ctx()
    stub["found"] = []
    assert terms.web_search({"terms": ["LRU"]}, t)["searched"][0]["status"] == "no results"
    assert terms.explain_term({"section": "s1", "terms": ["LRU"]}, t)["ok"]


def test_definition_cut_to_max_sentences(stub):
    t, _ = make_ctx()
    r = terms.web_search({"terms": ["LRU"]}, t)
    rid = r["searched"][0]["results"][0]["id"]
    out = terms.explain_term({"section": "s1", "terms": ["LRU"], "source": rid}, t)
    n = settings.explain_max_sentences  # a local: a failing assert must not print the settings (they hold secrets)
    assert out["defined"][0]["definition"] == "A. B." and n == 2
    assert out["defined"][0]["source_url"] == "http://a.test/lru" and "unsourced" not in out["defined"][0]


def test_search_per_term_and_snippet_cut(stub):
    t, _ = make_ctx()
    r = terms.web_search({"terms": ["LRU", "cache miss"]}, t)
    assert [x["term"] for x in r["searched"]] == ["LRU", "cache miss"]
    cap = settings.search_snippet_chars  # a local: a failing assert must not print the settings
    assert len(r["searched"][0]["results"][0]["snippet"]) == cap
    assert terms.web_search({}, t)["ok"] is False

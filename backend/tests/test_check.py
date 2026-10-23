import pytest

from app.core.settings import CHECK_PRESETS, settings
from app.helpers import factcheck as fc

N = CHECK_PRESETS["normal"]
needs_model = pytest.mark.skipif(not fc.nli_ready(), reason="NLI model files missing: run backend/scripts/fetch_nli.py")


def run(facts, rewrite, p=N, original=""):
    return fc.check_facts(facts if isinstance(facts, list) else [facts], rewrite, p, original)


def verdicts(r):
    return [f["verdict"] for f in r["failures"]]


# ---------- pure code ----------

def test_numbers():
    assert fc.numbers("It costs 1,500 dollars, about 10% more, 3.50 each") == {"1500", "10", "3.5"}
    assert fc.numbers("There are three policies") == fc.numbers("There are 3 policies") == {"3"}
    assert fc.numbers("one of the best") == set()


def test_names():
    assert fc.names("Redis supports LRU eviction.") == {"redis", "lru"}
    assert fc.names("Bélády proved it. When full it evicts.") == {"bélády"}
    assert fc.names("Writes go to disk.", original="the writes go on") == set()


def test_tokens_lowercase_stopwords_stems():
    assert fc.tokens("The Caches are Evicting items") == ["cach", "evict", "item"]


def test_units_keep_code_table_quote():
    rw = "Intro line. Second one.\n\n```py\nx = 1. y\n```\n\n| A | B |\n|---|---|\n| 1 | 2 |\n\n> Quoted words."
    assert [k for k, _ in fc.units(rw)] == ["prose", "prose", "code", "table", "table", "quote"]


def test_all_pairs_switch(monkeypatch):
    seen = []
    monkeypatch.setattr(fc, "nli", lambda pairs: seen.append(pairs) or [{"entailment": 1.0, "neutral": 0, "contradiction": 0}] * len(pairs))
    sents = [f"Sentence number {w} talks about topic {w}." for w in "alpha bravo charlie delta echo foxtrot golf hotel".split()]
    fact = "Sentence alpha talks about topic alpha."
    fc.check_facts([fact], " ".join(sents[:6]), N)
    assert len({c for c, _ in seen[0]}) == 6 + 5            # 6 sentences + 5 joined pairs: every sentence scored
    seen.clear()
    fc.check_facts([fact], " ".join(sents), N)
    assert len({c for c, _ in seen[0]}) < 8 + 7             # 8 sentences: BM25 picks candidates
    seen.clear()
    fc.check_facts([fact], " ".join(sents), CHECK_PRESETS["strict"])
    assert len({c for c, _ in seen[0]}) == 8 + 7            # strict: 12 or fewer is still all pairs


# ---------- the 12 cases of spec section 4, real model ----------

@needs_model
def test_1_paraphrase():
    assert not run("LRU removes the least recently used item.", "LRU throws out the item nobody has used for the longest time.")["failures"]


@needs_model
def test_2_negation():
    r = run("Eviction happens only when the cache is full.", "Eviction happens even when the cache has free space.")
    assert verdicts(r) == ["shifted"]


@needs_model
def test_3_comparative_flipped():
    r = run("LRU uses about 10% more memory than FIFO.", "LRU uses less memory than FIFO.")
    assert verdicts(r)[0] in ("number_or_name_changed", "shifted")


@needs_model
def test_4_number_changed():
    assert verdicts(run("The cache holds 512 entries.", "The cache holds 256 entries.")) == ["number_or_name_changed"]


@needs_model
def test_5_name_changed():
    assert verdicts(run("Redis supports LRU eviction.", "Memcached supports LRU eviction.")) == ["number_or_name_changed"]


@needs_model
def test_6_dropped_fact():
    r = run("Writes go to disk every 5 seconds.", "LRU throws out old items. Caches are everywhere today. FIFO is simpler.")
    assert verdicts(r) == ["missing"]


@needs_model
def test_7_split_across_sentences():
    assert not run("LRU is simple but needs extra memory for tracking.", "LRU is simple. It needs a bit more memory to track use.")["failures"]


@needs_model
def test_8_extra_unrelated_sentence():
    facts = ["LRU removes the least recently used item.", "The cache holds 512 entries."]
    rw = "LRU throws out the item nobody has used for the longest time. The cache holds 512 entries. Caches are everywhere today."
    r = run(facts, rw)
    assert r["kept"] == 2 and not r["failures"]


@needs_model
def test_9_spelled_out_number():
    assert not run("There are three eviction policies.", "There are 3 eviction policies.")["failures"]


@needs_model
def test_10_fact_in_table():
    assert not run("Plan B costs $20 per month.", "| Plan | Price |\n|---|---|\n| B | $20/month |")["failures"]


@needs_model
def test_11_short_section_scores_every_sentence(monkeypatch):
    seen = []
    real = fc.nli
    monkeypatch.setattr(fc, "nli", lambda pairs: seen.append(pairs) or real(pairs))
    rw = "Intro here. LRU removes old items. Caches are fast. Misses are slow. Disk is slow."
    run("LRU removes the least recently used item.", rw)
    assert {c for c, _ in seen[0]} >= {"Intro here.", "LRU removes old items.", "Caches are fast.", "Misses are slow.", "Disk is slow."}


@needs_model
def test_12_whole_section():
    facts = ["LRU removes the least recently used item.", "Eviction happens only when the cache is full.",
             "The cache holds 512 entries.", "Writes go to disk every 5 seconds.", "There are three eviction policies."]
    rw = ("LRU throws out the item nobody has used for the longest time. Eviction happens even when the cache has free space. "
          "The cache holds 512 entries. There are 3 eviction policies. Caches are everywhere today. FIFO is simpler. "
          "Misses cost time. Hits are fast.")
    r = run(facts, rw)
    assert r["kept"] == 3 and r["total"] == 5
    assert {(f["fact"], f["verdict"]) for f in r["failures"]} == {(facts[1], "shifted"), (facts[3], "missing")}
    assert r["failures"][0]["sentence"].startswith("Eviction happens even")


# ---------- presets change a borderline verdict ----------

@needs_model
def test_lenient_forgives_a_dropped_number():
    fact, rw = "The cache holds 512 entries.", "The cache can hold a few hundred entries."
    assert verdicts(run(fact, rw)) == ["number_or_name_changed"]
    assert not run(fact, rw, CHECK_PRESETS["lenient"])["failures"]


@needs_model
def test_strict_catches_a_weak_contradiction():
    fact, rw = "All caches use LRU.", "Most caches use LRU."  # NLI: contradiction about 0.37, between strict 0.35 and normal 0.50
    assert verdicts(run(fact, rw)) == ["missing"]
    assert verdicts(run(fact, rw, CHECK_PRESETS["strict"])) == ["shifted"]


@needs_model
def test_strict_runs_nli_on_tables(monkeypatch):
    fact, rw = "Plan B costs $20 per month.", "| Plan | Price |\n|---|---|\n| B | $20/month |"
    calls = []
    real = fc.nli
    monkeypatch.setattr(fc, "nli", lambda pairs: calls.append(pairs) or real(pairs))
    run(fact, rw)
    assert not calls                                    # normal: exact check only
    run(fact, rw, CHECK_PRESETS["strict"])
    assert calls                                        # strict: also NLI


# ---------- fixes from the 2026-10-22 logs ----------

def test_capitalised_first_word_is_not_a_name():
    orig = "Internal tools and external MCP clients share the same three functions at Parallel AI."
    assert fc.names("Internal AI employees use the same three tool functions.", orig) == {"ai"}
    assert fc.names("Parallel AI sells agents.", orig) == {"parallel", "ai"}  # capitalised mid-sentence in the original


@needs_model
def test_supporting_sentence_beats_contradicting_neighbour():
    fact = "Client registrations are made permanent to prevent expiry problems."
    rw = ("Once a registration expired, the client kept sending its old cached ID and got errors. "
          "So client registrations are made permanent to prevent expiry problems.")
    assert run(fact, rw)["failures"] == []


# ---------- the check tool: style and facts together ----------

def _tool(rewritten, rounds=1, facts=("Redis evicts the least recently used key when memory is full.",)):
    from types import SimpleNamespace
    text = "Redis evicts the least recently used key when memory is full, so hot keys stay in memory for later reads."
    s = {"title": "Eviction", "text": text, "lines": [1, 1], "facts": list(facts), "rewritten": rewritten, "rounds": rounds}
    return s, SimpleNamespace(ctx=SimpleNamespace(workspace={"sections": {"s1": s}}))


@needs_model
def test_tool_reports_style_and_facts_together():
    from app.services.agent.tools.check import check
    s, tctx = _tool("Hot keys stay.")  # far too short and the fact is gone
    r = check({"section": "s1"}, tctx)
    assert r["ok"] is False and r["style_problems"] and r["fact_failures"] and "next" in r
    assert s["check"]["style"] and s["check"]["failures"]  # rewrite reads both back


@needs_model
def test_tool_last_round_style_miss_keeps_rewrite_but_lost_fact_falls_back():
    from app.services.agent.tools.check import check
    good = "Redis evicts the least recently used key when memory is full. So hot keys stay in memory."
    s, tctx = _tool(good + " " + good + " " + good, rounds=settings.rewrite_max_rounds)  # 2.4x: too long
    r = check({"section": "s1"}, tctx)
    assert r["ok"] is True and r["style_problems"] and "note" in r and not s.get("fallback")
    s, tctx = _tool("Hot keys stay in memory for later reads, which keeps them fast.", rounds=settings.rewrite_max_rounds)
    r = check({"section": "s1"}, tctx)
    assert r["ok"] is False and s.get("fallback") is True


def test_rewrite_prompt_gets_fix_these_block():
    from app.services.agent.tools.content import _fix_these
    chk = {"passed": False, "style": ["too short: 10 words of 40"],
           "failures": [{"fact": "Redis evicts keys.", "verdict": "number_or_name_changed", "sentence": "It evicts keys."}]}
    block, n = _fix_these(chk)
    assert n == 2 and "FIX THESE" in block and "- too short" in block
    assert '- number or name changed: "Redis evicts keys." (you wrote: "It evicts keys.")' in block
    assert _fix_these({**chk, "passed": True}) == ("", 0) and _fix_these(None) == ("", 0)

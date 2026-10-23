"""Does a rewrite still say every fact? BM25 finds candidate sentences, a DeBERTa NLI model judges them,
exact checks cover numbers and names. English only. No imports from services."""
import json
import re
import threading
from pathlib import Path

import numpy as np
import snowballstemmer
from rank_bm25 import BM25Okapi

from app.core.settings import settings

STOP = set("a an the and or but of to in on at for with by from as is are was were be been it its this that these those "
           "there here which who what when where how if then than so not no do does did has have had can will may more "
           "about also very just".split())
COMMON_CAPS = set("a an the this that these those it its in on at for and but or so as with by from to of is are was were "
                  "we you they he she there here what why how which who when where while if each every some any all most "
                  "many both not no yes after before then also only once because since unlike like such i".split())
WORDS = {w: i for i, w in enumerate("two three four five six seven eight nine ten eleven twelve thirteen fourteen fifteen "
                                    "sixteen seventeen eighteen nineteen twenty".split(), 2)}  # "one" left out: too often a pronoun
NUM = re.compile(r"(?<![A-Za-z\d])\d+(?:,\d{3})*(?:\.\d+)?")
RELATED = 0.4  # min share of the fact's word stems a sentence must share for its contradiction to count
_stem = snowballstemmer.stemmer("english").stemWord


# ---------- text units ----------

def units(rewrite: str) -> list[tuple[str, str]]:
    """(kind, text): prose sentences, and each code block, table row and quote line as one unit."""
    out, code = [], None
    for line in rewrite.splitlines():
        s = line.strip()
        if code is not None:
            code.append(line)
            if s.startswith(("```", "~~~")):
                out.append(("code", "\n".join(code)))
                code = None
        elif s.startswith(("```", "~~~")):
            code = [line]
        elif s.startswith("|"):
            if not re.fullmatch(r"[|\s:\-]+", s):
                out.append(("table", " ".join(c.strip() for c in s.strip("|").split("|"))))
        elif s.startswith(">"):
            out.append(("quote", s.lstrip("> ").strip()))
        elif s:
            s = re.sub(r"^(?:[-*+]|\d+[.)])\s+|^#+\s+", "", s)
            s = re.sub(r"[*_`]+", "", s)
            out += [("prose", x) for x in re.split(r"(?<=[.!?])\s+", s) if x.strip()]
    if code:
        out.append(("code", "\n".join(code)))
    return out


def tokens(s: str) -> list[str]:
    return [_stem(w) for w in re.findall(r"[a-z0-9]+", s.lower()) if w not in STOP]


# ---------- exact numbers and names ----------

def numbers(s: str) -> set[str]:
    """Numbers as plain strings: commas dropped, '3.50' == '3.5', spelled-out two..twenty mapped."""
    found = {n.replace(",", "") for n in NUM.findall(s)}
    found = {str(float(n)).removesuffix(".0") for n in found}
    return found | {str(WORDS[w]) for w in re.findall(r"[a-z]+", s.lower()) if w in WORDS}


def names(fact: str, original: str = "") -> set[str]:
    """Capitalised words of the fact that are not common words nor seen lowercase in the original. The fact's first
    word is capitalised anyway ("Internal AI employees..."), so it counts only if the original capitalises it
    mid-sentence too ("...at Parallel AI")."""
    lower = set(re.findall(r"[^\W\d_]+", original.replace("\n", " "))) if original else set()
    lower = {w for w in lower if w.islower()}
    mid_caps = set(re.findall(r"(?<=[\w,;:] )[A-Z][^\W_]*", original))
    ws = re.findall(r"[^\W\d_][^\W_]*", fact)
    if original and ws and ws[0][1:].islower() and ws[0] not in mid_caps:
        ws = ws[1:]
    return {w.lower() for w in ws if w[0].isupper() and w.lower() not in COMMON_CAPS and w.lower() not in lower}


def exact_missing(fact: str, rewrite: str, p: dict, original: str = "") -> list[str]:
    miss = []
    if p["exact_numbers"]:
        miss += sorted(numbers(fact) - numbers(rewrite))
    if p["exact_names"]:
        have = set(re.findall(r"[^\W\d_]+", rewrite.lower()))
        miss += sorted(n for n in names(fact, original) if n not in have)
    return miss


# ---------- NLI model (loaded once per process) ----------

_lock = threading.Lock()
_model = None


class NliMissing(RuntimeError):
    pass


def nli_ready() -> bool:
    d = Path(settings.check_nli_dir)
    return all((d / f).exists() for f in ("model.onnx", "tokenizer.json", "config.json"))


def _load():
    global _model
    with _lock:
        if _model is None:
            if not nli_ready():
                raise NliMissing(f"NLI model files not found in {settings.check_nli_dir}; run scripts/fetch_nli.py")
            import onnxruntime as ort
            from tokenizers import Tokenizer
            d = Path(settings.check_nli_dir)
            labels = {int(i): v.lower() for i, v in json.loads((d / "config.json").read_text())["id2label"].items()}
            tok = Tokenizer.from_file(str(d / "tokenizer.json"))
            tok.enable_truncation(max_length=256)
            tok.enable_padding()
            sess = ort.InferenceSession(str(d / "model.onnx"), providers=["CPUExecutionProvider"])
            _model = (sess, tok, [labels[i] for i in range(len(labels))])
    return _model


def nli(pairs: list[tuple[str, str]]) -> list[dict]:
    """[(premise, hypothesis)] -> [{entailment, neutral, contradiction}] probabilities."""
    sess, tok, labels = _load()
    want = {i.name for i in sess.get_inputs()}
    out = []
    for i in range(0, len(pairs), 16):
        enc = tok.encode_batch(pairs[i:i + 16])
        feed = {"input_ids": np.array([e.ids for e in enc], dtype=np.int64),
                "attention_mask": np.array([e.attention_mask for e in enc], dtype=np.int64),
                "token_type_ids": np.array([e.type_ids for e in enc], dtype=np.int64)}
        logits = sess.run(None, {k: v for k, v in feed.items() if k in want})[0]
        p = np.exp(logits - logits.max(axis=1, keepdims=True))
        p /= p.sum(axis=1, keepdims=True)
        out += [dict(zip(labels, map(float, row))) for row in p]
    return out


# ---------- the check ----------

def _overlap(fact: str, text: str) -> float:
    """Share of the fact's non-numeric word stems that the text has."""
    f = {t for t in tokens(NUM.sub(" ", fact))}
    return len(f & set(tokens(text))) / len(f) if f else 1.0


def _candidates(fact: str, bm25: BM25Okapi, us: list, p: dict) -> tuple[int, list[tuple[str, int]]]:
    """(best unit index, [(candidate text, index of its first unit)])."""
    scores = bm25.get_scores(tokens(fact))
    order = sorted(range(len(us)), key=lambda i: -scores[i])
    best = order[0]
    if len(us) <= p["all_pairs_max_sentences"]:
        idx = list(range(len(us)))
        pairs = list(range(len(us) - 1))
    else:
        idx = set(order[:p["top_k"]]) | {j for j in (best - 1, best + 1) if 0 <= j < len(us)}
        pairs = [best] if best + 1 < len(us) else []  # best match joined with its next sentence
    cands = [(us[i][1], i) for i in sorted(idx)] + [(us[i][1] + " " + us[i + 1][1], i) for i in pairs]
    return best, cands


def check_facts(facts: list[str], rewrite: str, p: dict, original: str = "") -> dict:
    """{'failures': [{fact, sentence, verdict, score?}], 'kept': n, 'total': n}. p is settings.check_params()."""
    us = units(rewrite)
    failures, kept = [], 0
    if not us:
        return {"failures": [{"fact": f, "sentence": None, "verdict": "missing"} for f in facts], "kept": 0, "total": len(facts)}
    bm25 = BM25Okapi([tokens(t) or ["_"] for _, t in us], k1=p["bm25_k1"], b=p["bm25_b"])
    plans = []
    for f in facts:
        best, cands = _candidates(f, bm25, us, p)
        plans.append((best, cands, exact_missing(f, rewrite, p, original)))
    # the best unit is code or a table: NLI judges those badly, so (unless 'nli') only the exact check applies
    skip = [pl[0] is not None and us[pl[0]][0] in ("code", "table") and p["code_table_facts"] == "exact" for pl in plans]
    todo = sorted({(c, f) for (_, cands, miss), f, s in zip(plans, facts, skip) if not miss and not s for c, _ in cands})
    scored = dict(zip(todo, nli(todo))) if todo else {}
    for f, (best, cands, miss), s in zip(facts, plans, skip):
        bt = us[best][1]
        if miss:
            if _overlap(f, bt) >= 0.5:
                failures.append({"fact": f, "sentence": bt, "verdict": "number_or_name_changed"})
            else:
                failures.append({"fact": f, "sentence": None, "verdict": "missing"})
        elif s:
            kind = us[best][0]  # the whole table or code, not just the best row
            if _overlap(f, " ".join(t for k, t in us if k == kind)) >= 0.5:
                kept += 1
            else:
                failures.append({"fact": f, "sentence": None, "verdict": "missing"})
        else:
            sc = [(c, scored[(c, f)]) for c, _ in cands]
            # NLI "contradicts" unrelated sentences too (3 policies vs 512 entries): only trust it where the words overlap
            worst = max(sc, key=lambda x: x[1]["contradiction"] if _overlap(f, x[0]) >= RELATED else 0)
            top = max(sc, key=lambda x: x[1]["entailment"])
            if top[1]["entailment"] >= p["entail_min"]:
                kept += 1  # a sentence that says the fact wins over a neighbour NLI calls contradicting
            elif _overlap(f, worst[0]) >= RELATED and worst[1]["contradiction"] >= p["contra_min"] and worst[1]["contradiction"] > worst[1]["entailment"]:
                failures.append({"fact": f, "sentence": worst[0], "verdict": "shifted", "score": round(worst[1]["contradiction"], 2)})
            else:
                failures.append({"fact": f, "sentence": None, "verdict": "missing"})
    return {"failures": failures, "kept": kept, "total": len(facts)}

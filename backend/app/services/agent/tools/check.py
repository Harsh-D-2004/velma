import hashlib
import logging
import time

from app.core.settings import settings
from app.helpers import factcheck, style
from app.helpers.lines import body_of
from app.models.agent import Tool
from app.services.agent.tools.content import _section

log = logging.getLogger("velma.check")


def check(args: dict, tctx) -> dict:
    """Style rules, then fact rules; both always run, so one rewrite can fix every problem at once."""
    ws = tctx.ctx.workspace
    sid = str(args.get("section", ""))
    s = _section(ws, sid)
    if s is None or "rewritten" not in s:
        return {"ok": False, "error": "unknown section or no rewrite yet; run outline and rewrite first"}
    p, t0 = settings.check_params(), time.monotonic()
    m = style.measure(body_of(s), s["rewritten"])
    style_errs = style.problems(m)
    try:
        r = factcheck.check_facts(s["facts"], s["rewritten"], p, s["text"])
    except factcheck.NliMissing as e:
        return {"ok": False, "error": str(e)}
    for e in style_errs:
        log.warning("check section=%s style=%r", sid, e[:160])
    for f in r["failures"]:
        log.warning("check section=%s fact=%r verdict=%s score=%s sentence=%r", sid, f["fact"][:120], f["verdict"],
                    f.get("score"), (f["sentence"] or "")[:120])
    facts_ok = r["kept"] >= p["min_kept_ratio"] * r["total"]
    last_round = s.get("rounds", 0) >= settings.rewrite_max_rounds
    # out of rounds with only style misses: keep the rewrite, its facts are intact
    passed = facts_ok and (not style_errs or last_round)
    log.info("check section=%s round=%d/%d words=%d/%d ratio=%s grade=%s style=%s facts=%d/%d passed=%s strictness=%s ms=%d",
             sid, s.get("rounds", 0), settings.rewrite_max_rounds, m["words"], m["orig_words"], m["ratio"], m["grade"],
             "ok" if not style_errs else "fail", r["kept"], r["total"], str(passed).lower(), settings.check_strictness,
             (time.monotonic() - t0) * 1000)
    # `rev` lets later steps see that the rewrite changed after this check; rewrite reads `style` and `failures` back
    s["check"] = {**r, "style": style_errs, "passed": passed, "rev": hashlib.sha1(s["rewritten"].encode()).hexdigest()[:8]}
    # "section" first: a reply starting {"ok": false is logged as a tool error, and a failed check is a normal result
    res = {"section": sid, "ok": passed, "words": m["words"], "orig_words": m["orig_words"], "ratio": m["ratio"],
           "grade": m["grade"], "style_problems": style_errs, "kept": r["kept"], "total": r["total"],
           "fact_failures": r["failures"][:15]}
    if passed and style_errs:
        log.warning("check section=%s accepted with style misses after %d rounds", sid, s["rounds"])
        res["note"] = "accepted: facts are intact; style targets were not met after the last round"
    elif not passed and last_round:  # facts still lost after the last round
        s["fallback"] = True
        log.warning("check section=%s fallback=original rounds=%d", sid, s["rounds"])
        res["fallback"] = "original: this section will be shown as written; do not rewrite it again"
    elif not passed:
        res["next"] = f"call rewrite for {sid}; it receives these problems itself"
    return res


TOOLS = [
    Tool("check", "Check a section's rewrite in code, without a model call. Style: length against the original and "
         "reading grade. Facts: every fact from the outline is still there with the same numbers, names and meaning. "
         "Both always run. Returns ok, the style problems and the fact failures (fact, nearest sentence, verdict: "
         "missing, shifted, number_or_name_changed). When ok is false, call rewrite for the section again: it reads "
         "these problems itself.",
         {"type": "object", "properties": {"section": {"type": "string"}}, "required": ["section"]}, check),
]

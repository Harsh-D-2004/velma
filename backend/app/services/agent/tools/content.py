import hashlib
import json
import re
from pathlib import Path

import textstat

from app.helpers.lines import body_of, first_content, invariant_ok, prose_only, protected, slice_text
from app.models.agent import Tool
from app.services.agent.refine import refine
from app.services.llm import text

PROMPTS = Path(__file__).parent.parent / "prompts"
OUTLINE_PROMPT = (PROMPTS / "outline.md").read_text()
REWRITE_PROMPT = (PROMPTS / "rewrite.md").read_text()
CHECK_PROMPT = (PROMPTS / "check.md").read_text()

WINDOW_WORDS = 8000
FALLBACK_WORDS = 400
TITLE_MAX_WORDS = 15
STYLES = ["plain", "steps", "analogy", "qa"]
DIAGRAM_KINDS = ["flow", "timeline", "chart", "concept", "compare"]
PAGE_CHARS = 3000  # `read` page size; the registry cuts every result at 4,000 chars
REFINE_PROMPT = (PROMPTS / "outline_refine.md").read_text()


# ---------- outline ----------

def _is_int(x) -> bool:
    return isinstance(x, int) and not isinstance(x, bool)


def start_errors(starts, lo: int, hi: int, lines: list[str], prot: set[int], title_line=None) -> list[str]:
    """Errors for section starts that must run from line lo to line hi."""
    if not isinstance(starts, list) or not starts or not all(_is_int(x) for x in starts):
        return ["'sections' must be a non-empty list of objects with an integer 'start'"]
    errs = []
    if starts[0] != lo:
        errs.append(f"the first section must start at line {lo}")
    if any(b <= a for a, b in zip(starts, starts[1:])):
        errs.append("starts must be strictly increasing")
    for x in starts:
        if not lo <= x <= hi:
            errs.append(f"start {x} is outside lines {lo}-{hi}")
        elif x in prot:
            errs.append(f"start {x} is inside a code block or table; start before it or after it")
        elif not lines[x - 1].strip() or x == title_line:
            errs.append(f"start {x} is a blank line or the title line")
    return errs[:6]


def fallback_starts(lines: list[str], prot: set[int], lo: int, hi: int) -> list[int]:
    """No model: cut at blank lines about every 400 words, never inside a fence or table."""
    starts, words, ready = [lo], 0, False
    for n in range(lo, hi + 1):
        l = lines[n - 1]
        if ready and l.strip() and n not in prot:
            starts.append(n)
            words, ready = 0, False
        if not l.strip() and n not in prot and words >= FALLBACK_WORDS:
            ready = True
        words += len(l.split())
    return starts


def _numbered(lines: list[str], lo: int, hi: int) -> str:
    return "\n".join(f"L{n}: {lines[n - 1]}" for n in range(lo, hi + 1))


def _ask(prompt: str, user: str, ctx, validate):
    """Model call with up to 3 tries; the error list is fed back each time. Returns (reply, errors)."""
    last = {}

    def gen(errors):
        msgs = [{"role": "system", "content": prompt}, {"role": "user", "content": user}]
        if errors:
            msgs += [{"role": "assistant", "content": json.dumps(last["c"])},
                     {"role": "user", "content": "Fix these problems and reply with the full JSON again:\n- " + "\n- ".join(errors)}]
        last["c"] = text.chat_json(msgs, task="outline", ctx=ctx)
        return last["c"]

    return refine(gen, validate, 3)


def _title_errors(lines: list[str], tl) -> list[str]:
    if tl is None:
        return []
    firsts = [n for n, l in enumerate(lines, 1) if l.strip()][:3]
    if tl not in firsts:
        return [f"title_line must be one of the first 3 non-blank lines {firsts}, or null"]
    if len(lines[tl - 1].split()) > TITLE_MAX_WORDS:
        return [f"title_line is over {TITLE_MAX_WORDS} words; use null if the article has no short title line"]
    return []


def _pass1(lines, prot, lo, hi, first_window, carried, ctx):
    """One window -> (title_line, [(start, title)]). Falls back to blank-line cuts after 3 failed tries."""
    user = (f"Lines {lo}-{hi} of the article. " + (f"Line {lo} continues the section that began in the previous window.\n\n" if carried else "\n\n")
            + _numbered(lines, lo, hi))

    def validate(c):
        tl = c.get("title_line") if first_window else None
        errs = _title_errors(lines, tl) if first_window else []
        secs = c.get("sections")
        starts = [s.get("start") if isinstance(s, dict) else None for s in secs] if isinstance(secs, list) else None
        wlo = lo if not first_window else (first_content(lines, tl) or lo)
        return errs + start_errors(starts, wlo, hi, lines, prot, tl)

    best, errs = _ask(OUTLINE_PROMPT, user, ctx, validate)
    if errs:
        wlo = first_content(lines, None) if first_window else lo
        return None, [(n, "") for n in fallback_starts(lines, prot, wlo, hi)]
    tl = best.get("title_line") if first_window else None
    return tl, [(s["start"], str(s.get("title") or "").strip()[:80]) for s in best["sections"]]


def _clean(s: dict) -> dict:
    d = s.get("diagram")
    ok_d = isinstance(d, dict) and d.get("kind") in DIAGRAM_KINDS
    return {"facts": [str(f) for f in s.get("facts") or [] if f][:60],
            "style": s.get("style") if s.get("style") in STYLES else "plain",
            "diagram": {"kind": d["kind"], "idea": str(d.get("idea", ""))[:200]} if ok_d else None,
            "terms": [str(t) for t in s.get("terms") or [] if t][:5],
            "rewrite": s.get("rewrite") is not False}  # rewrite by default; keep is the exception


def _pass2(lines, prot, title_line, secs, ctx, first_batch):
    """Refine one batch of pass-1 sections (list of dicts start/end/title). Returns (tldr, takeaways, sections)."""
    lo, hi = secs[0]["start"], secs[-1]["end"]

    def show(s):
        body = slice_text(lines, s["start"], s["end"], title_line)
        ls = [l for l in body.split("\n") if l.strip()]
        return (f"SECTION start={s['start']} end={s['end']} words={len(body.split())} title={s['title']!r}\n"
                f"first line: {ls[0][:200] if ls else ''}\nlast line: {ls[-1][:200] if ls else ''}\n"
                f"TEXT:\n{_numbered(lines, s['start'], s['end'])}")

    base = "\n\n".join(show(s) for s in secs)
    want = "tldr and takeaways: yes\n" if first_batch else "tldr and takeaways: no, leave them out\n"
    pass1_starts = [s["start"] for s in secs]

    def make_validate(locked):
        def validate(c):
            errs = []
            if first_batch:
                if not isinstance(c.get("tldr"), str) or not c["tldr"].strip():
                    errs.append("tldr must be a non-empty string")
                tk = c.get("takeaways")
                if not (isinstance(tk, list) and 3 <= len(tk) <= 5 and all(isinstance(t, str) and t.strip() for t in tk)):
                    errs.append("takeaways must be 3 to 5 non-empty strings")
            rs = c.get("sections")
            starts = [s.get("start") if isinstance(s, dict) else None for s in rs] if isinstance(rs, list) else None
            errs += start_errors(starts, lo, hi, lines, prot, title_line)
            if locked and not errs and starts != pass1_starts:
                errs.append(f"do not change the starts; they must be exactly {pass1_starts}")
            return errs
        return validate

    best, errs = _ask(REFINE_PROMPT, want + "Boundaries may change.\n\n" + base, ctx, make_validate(False))
    if errs:  # keep pass-1 boundaries, ask only for facts and flags
        best, errs = _ask(REFINE_PROMPT, want + f"Boundaries are fixed: reply with exactly these starts {pass1_starts}.\n\n" + base,
                          ctx, make_validate(True))
    if errs:
        best = {"sections": [{"start": s["start"], "title": s["title"]} for s in secs]}
        if first_batch:
            best |= {"tldr": "", "takeaways": []}
    out = []
    for i, r in enumerate(best["sections"]):
        end = best["sections"][i + 1]["start"] - 1 if i + 1 < len(best["sections"]) else hi
        out.append({"start": r["start"], "end": end, "title": str(r.get("title") or "").strip()[:80], **_clean(r)})
    tk = best.get("takeaways")
    return str(best.get("tldr") or "").strip(), [str(t).strip() for t in tk] if isinstance(tk, list) else [], out


def _sentence_title(sec_text: str) -> str:
    first = re.split(r"(?<=[.!?])\s+", " ".join(sec_text.split()), maxsplit=1)[0].split()
    return " ".join(first[:12]) + ("…" if len(first) > 12 else "")


def outline(args: dict, tctx) -> dict:
    ws, ctx = tctx.ctx.workspace, tctx.ctx
    if not ws["section_order"]:
        lines = ctx.article.splitlines()
        prot, n = protected(lines), len(lines)
        # pass 1: boundaries, window by window
        title_line, found, cur, first, carried = None, {}, 1, True, False
        while True:
            lo = next((k for k in range(cur, n + 1) if lines[k - 1].strip()), None)
            if lo is None:
                break
            hi, words = lo, 0
            while hi <= n and (words < WINDOW_WORDS or hi == lo):
                words += len(lines[hi - 1].split())
                hi += 1
            hi -= 1
            while hi < n and hi + 1 in prot:
                hi += 1
            tl, starts = _pass1(lines, prot, lo, hi, first, carried, ctx)
            if first:
                title_line = tl
            for s, t in starts:
                found.setdefault(s, t)
            first = False
            if hi >= n:
                break
            last = max(s for s, _ in starts)
            cur, carried = (last, True) if last > lo else (hi + 1, False)
        if not found:
            return {"ok": False, "error": "the article is empty"}
        starts = sorted(found)
        secs = [{"start": s, "end": (starts[i + 1] - 1 if i + 1 < len(starts) else n), "title": found[s]}
                for i, s in enumerate(starts)]
        # pass 2: facts, flags, tldr; may merge or split
        tldr, takeaways, refined, batch, words = "", [], [], [], 0
        batches = []
        for s in secs:
            w = len(slice_text(lines, s["start"], s["end"], title_line).split())
            if batch and words + w > WINDOW_WORDS:
                batches.append(batch)
                batch, words = [], 0
            batch.append(s)
            words += w
        batches.append(batch)
        for i, b in enumerate(batches):  # shortcut: tldr/takeaways come from the first batch; finish can override
            t, k, r = _pass2(lines, prot, title_line, b, ctx, i == 0)
            if i == 0:
                tldr, takeaways = t, k
            refined += r
        order, sections = [], {}
        for i, s in enumerate(refined, 1):
            sid = f"s{i}"
            s["text"] = slice_text(lines, s["start"], s["end"], title_line)
            s["title"] = s["title"] or f"Part {i}"
            sections[sid] = s
            order.append(sid)
        if not invariant_ok(lines, title_line, [sections[k] for k in order]):
            return {"ok": False, "error": "internal error: sections do not add up to the article"}
        ws["title_line"] = title_line
        ws["title"] = (re.sub(r"^#+\s*", "", lines[title_line - 1].strip()) if title_line
                       else _sentence_title(sections[order[0]]["text"]))
        ws["sections"], ws["section_order"] = sections, order
        ws["outline"] = {"tldr": tldr, "takeaways": takeaways}
    rows = [{"id": k, "title": s["title"], "lines": f"{s['start']}-{s['end']}", "words": len(s["text"].split()),
             "style": s["style"], "rewrite": s["rewrite"], "diagram": s["diagram"] and s["diagram"]["kind"],
             "terms": s["terms"], "facts": len(s["facts"])} for k, s in ((k, ws["sections"][k]) for k in ws["section_order"])]
    if len(json.dumps(rows)) > 3300:
        rows = [{k: r[k] for k in ("id", "title", "lines")} for r in rows]
    return {"ok": True, "page_title": ws["title"], "sections": rows}


# ---------- read ----------

def read(args: dict, tctx) -> dict:
    ws, rid = tctx.ctx.workspace, str(args.get("id", "")).strip()
    offset = max(int(args.get("offset") or 0), 0)
    meta = {}
    if rid == "title":
        return {"ok": True, "id": rid, "text": ws["title"]}
    if rid == "outline":
        if not ws["outline"]:
            return {"ok": False, "error": "no outline yet; run outline first"}
        return {"ok": True, "id": rid, **ws["outline"], "sections": [
            {"id": k, "title": ws["sections"][k]["title"], "lines": f"{ws['sections'][k]['start']}-{ws['sections'][k]['end']}"}
            for k in ws["section_order"]]}
    if m := re.fullmatch(r"(s\d+)\.(rewrite|facts)", rid):
        s = ws["sections"].get(m.group(1))
        if s is None:
            return {"ok": False, "error": f"unknown id '{rid}'"}
        if m.group(2) == "facts":
            return {"ok": True, "id": rid, "facts": s["facts"]}
        if s.get("rewritten") is None:
            return {"ok": False, "error": f"{m.group(1)} has no rewrite yet"}
        body = s["rewritten"]
    elif rid in ws["sections"]:
        s = ws["sections"][rid]
        body = s["text"]
        meta = {k: s[k] for k in ("title", "start", "end", "style", "rewrite", "diagram", "terms")}
        meta |= {"rewritten": "rewritten" in s,
                 "check": s.get("check") and {k: s["check"][k] for k in ("passed", "missing", "unsupported")}}
    elif rid in ws["search"]:
        return {"ok": True, "id": rid, **ws["search"][rid]}
    elif rid in ws["diagrams"]:
        return {"ok": True, "id": rid, **ws["diagrams"][rid]}
    elif rid.startswith("g:") and rid[2:].strip().lower() in ws["glossary"]:
        return {"ok": True, "id": rid, **ws["glossary"][rid[2:].strip().lower()]}
    else:
        return {"ok": False, "error": f"unknown id '{rid}'. Ids: sN sections, sN.rewrite, sN.facts, rN search results, "
                                      "dN diagrams, g:<term>, title, outline"}
    page = body[offset:offset + PAGE_CHARS]
    out = {"ok": True, "id": rid, **meta, "total_chars": len(body)}
    if offset + PAGE_CHARS < len(body):
        out["next_offset"] = offset + PAGE_CHARS
    out["text"] = page
    return out


# ---------- rewrite / check ----------

def readability_errors(rewrite: str) -> list[str]:
    plain = prose_only(rewrite)
    errs = []
    if len(plain.split()) >= 40:
        grade = textstat.flesch_kincaid_grade(plain)
        if grade > 10:
            errs.append(f"reading grade {grade:.0f} is too high (aim for 8 or lower): use shorter sentences and simpler words")
    long = [s for s in re.split(r"(?<=[.!?])\s+", plain) if len(s.split()) > 30]
    if long:
        errs.append(f"{len(long)} sentence(s) over 30 words; split them, e.g. '{' '.join(long[0].split()[:8])}...'")
    return errs


def _strip_fence(s: str) -> str:
    """Unwrap a reply that is one outer ``` (or ```md/```markdown) block; leave anything else alone."""
    s = s.strip()
    lines = s.split("\n")
    if len(lines) < 2 or lines[0].strip().lower() not in ("```", "```markdown", "```md") or lines[-1].strip() != "```":
        return s
    inner = lines[1:-1]
    if sum(1 for l in inner if l.startswith(("```", "~~~"))) % 2:
        return s
    return "\n".join(inner).strip()


def _section(ws: dict, sid: str) -> dict | None:
    return ws["sections"].get(sid)


def rewrite(args: dict, tctx) -> dict:
    ws = tctx.ctx.workspace
    s = _section(ws, str(args.get("section", "")))
    if s is None:
        return {"ok": False, "error": "unknown section; run outline first and use an id like s1"}
    style = args.get("style") or s["style"]
    if style not in STYLES:
        return {"ok": False, "error": f"style must be one of {STYLES}"}
    body = body_of(s)
    user = (f"STYLE: {style}\nFACTS (all must stay):\n" + "\n".join(f"- {f}" for f in s["facts"])
            + (f"\nFIX THESE PROBLEMS FROM THE LAST CHECK:\n{args['notes']}" if args.get("notes") else "")
            + "\n\nORIGINAL:\n" + body)
    last = {}

    def gen(errors):
        msgs = [{"role": "system", "content": REWRITE_PROMPT}, {"role": "user", "content": user}]
        if errors:
            msgs += [{"role": "assistant", "content": last["t"]},
                     {"role": "user", "content": "Rewrite again and fix:\n- " + "\n- ".join(errors)}]
        last["t"] = _strip_fence(text.chat(msgs, task="rewrite", ctx=tctx.ctx).content)
        return last["t"]

    best, errs = refine(gen, readability_errors, 2)
    s["rewritten"], s["style"] = best, style
    s.pop("check", None)
    return {"ok": True, "section": args["section"], "style": style, "words": len(best.split()),
            "readability_warnings": errs, "text": best[:1500], "truncated": len(best) > 1500}


def check(args: dict, tctx) -> dict:
    ws = tctx.ctx.workspace
    s = _section(ws, str(args.get("section", "")))
    if s is None or "rewritten" not in s:
        return {"ok": False, "error": "unknown section or no rewrite yet; run outline and rewrite first"}
    user = ("ORIGINAL:\n" + s["text"] + "\n\nFACTS:\n" + "\n".join(f"- {f}" for f in s["facts"])
            + "\n\nREWRITE:\n" + s["rewritten"])
    out = text.chat_json([{"role": "system", "content": CHECK_PROMPT}, {"role": "user", "content": user}],
                         task="check", ctx=tctx.ctx)
    lists = {k: [str(x) for x in (out.get(k) if isinstance(out.get(k), list) else []) if x][:20]
             for k in ("missing", "unsupported")}
    # `rev` lets later steps see that the rewrite changed after this check
    s["check"] = {**lists, "passed": not lists["missing"] and not lists["unsupported"],
                  "rev": hashlib.sha1(s["rewritten"].encode()).hexdigest()[:8]}
    return {"ok": True, "section": args["section"], **s["check"]}


# ---------- tool table ----------

_ID = {"type": "string"}
TOOLS = [
    Tool("outline", "Split the article into sections (by line numbers, original order) and extract the page title, "
         "tldr and takeaways drafts, fact lists, section titles, suggested style, diagram idea, terms and a rewrite "
         "flag. Rewrite every section with rewrite=true (the default); keep (no rewrite) only sections marked "
         "rewrite=false. Run once, first.",
         {"type": "object", "properties": {}}, outline),
    Tool("read", "Read any job item by id: section sN (text), sN.rewrite, sN.facts, search result rN, "
         "diagram dN, glossary entry g:<term>, title, outline. Long text comes in pages: pass offset from next_offset.",
         {"type": "object", "properties": {"id": _ID, "offset": {"type": "integer"}}, "required": ["id"]}, read),
    Tool("rewrite", "Rewrite a section in plain language in a chosen style. Keeps code, tables and quotes in Markdown; "
         "quotes stay word for word. Pass notes from a failed check to fix it.",
         {"type": "object", "properties": {"section": _ID, "style": {"type": "string", "enum": STYLES},
                                           "notes": {"type": "string"}}, "required": ["section"]}, rewrite),
    Tool("check", "List facts missing from a section's rewrite, and claims in it not supported by the original.",
         {"type": "object", "properties": {"section": _ID}, "required": ["section"]}, check),
]

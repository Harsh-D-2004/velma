import hashlib
import json
import re
from pathlib import Path

from app.helpers import diagrams
from app.models.agent import Tool
from app.services import storage
from app.services.agent.tools.content import DIAGRAM_KINDS, _section
from app.services.llm import text

DRAW_PROMPT = (Path(__file__).parent.parent / "prompts" / "draw.md").read_text()
RENDER_TRIES = 3


def _strip_fence(s: str) -> str:
    return re.sub(r"^```\w*\s*|\s*```$", "", s.strip())


def section_text(ws: dict, sid: str, s: dict) -> tuple[str, str]:
    """(the section's current rewrite, the section's glossary definitions)."""
    gl = [f"{g['term']}: {g['definition']}" for g in ws["glossary"].values() if g["section"] == sid]
    return s["rewritten"], "\n".join(gl)


def _table_errors(t) -> list[str]:
    if not (isinstance(t, dict) and isinstance(t.get("columns"), list) and isinstance(t.get("rows"), list)):
        return ['reply must be {"columns": [...], "rows": [[...]]}']
    n = len(t["columns"])
    if not 1 <= n <= 5 or not 1 <= len(t["rows"]) <= 8:
        return ["need 1-5 columns and 1-8 rows"]
    if any(not isinstance(r, list) or len(r) != n for r in t["rows"]):
        return [f"every row must have exactly {n} cells"]
    return []


def draw(args: dict, tctx) -> dict:
    ws = tctx.ctx.workspace
    sid = str(args.get("section", ""))
    s = _section(ws, sid)
    kind = args.get("kind")
    if s is None:
        return {"ok": False, "error": "unknown section; run outline first and use an id like s1"}
    if kind not in DIAGRAM_KINDS:
        return {"ok": False, "error": f"kind must be one of {DIAGRAM_KINDS}"}
    chk = s.get("check")
    if not (chk and chk.get("passed") and chk.get("rev") == _rev(s)):
        return {"ok": False, "error": "draw needs a rewrite whose current text passed check; rewrite and check first"}
    idea = str(args.get("idea") or (s["diagram"] or {}).get("idea") or s["title"])[:200]
    rw, glossary = section_text(ws, sid, s)
    user = f"KIND: {kind}\nIDEA: {idea}\n\nSECTION TEXT:\n{rw}\n\nGLOSSARY:\n{glossary or '(none)'}\n"
    msgs = [{"role": "system", "content": DRAW_PROMPT}, {"role": "user", "content": user}]
    fails = 0
    while True:
        if kind == "compare":
            shown = out = text.chat_json(msgs, task="draw", ctx=tctx.ctx)
            errs = _table_errors(out)
        else:
            if kind == "chart":
                shown = text.chat_json(msgs, task="draw", ctx=tctx.ctx)
                svg, err = diagrams.render_chart(shown)
            else:
                shown = _strip_fence(text.chat(msgs, task="draw", ctx=tctx.ctx).content)
                svg, err = diagrams.render_dot(shown)
            errs = [f"render error: {err}"] if err else []
        if not errs:
            break
        fails += 1
        if fails >= RENDER_TRIES:
            return {"ok": False, "error": "could not draw a valid diagram; skip it or try another kind", "problems": errs[:5]}
        msgs += [{"role": "assistant", "content": shown if isinstance(shown, str) else json.dumps(shown)},
                 {"role": "user", "content": "Fix these problems and reply again:\n- " + "\n- ".join(errs[:8])}]
    # same section + kind: replace the earlier diagram, so a second call is safe
    did = next((k for k, v in ws["diagrams"].items() if v["section"] == sid and v["type"] == kind),
               f"d{len(ws['diagrams']) + 1}")
    d = {"section": sid, "type": kind, "alt": f"Diagram: {idea}", "rev": chk["rev"]}
    if kind == "compare":
        d |= {"kind": "table", "columns": [str(c) for c in out["columns"]],
              "rows": [[str(c) for c in r] for r in out["rows"]]}
    else:
        url = storage.put_public(ws["public_prefix"], f"{did}.svg", svg, "image/svg+xml")
        w, h = diagrams.svg_size(svg)
        d |= {"kind": "svg", "url": url, "source": shown, "width": w, "height": h}
    ws["diagrams"][did] = d
    return {"ok": True, "id": did, "section": sid, "type": kind, "kind": d["kind"], "url": d.get("url")}


def _rev(st: dict) -> str:
    return hashlib.sha1(st["rewritten"].encode()).hexdigest()[:8]


TOOLS = [
    Tool("draw", "Draw a diagram for a section from its checked rewrite: Graphviz diagrams (flow, timeline, concept), a "
         "matplotlib chart (chart) or a comparison table (compare). Renders it and uploads it.",
         {"type": "object", "properties": {
             "section": {"type": "string", "description": "section id, e.g. s1"},
             "kind": {"type": "string", "enum": DIAGRAM_KINDS},
             "idea": {"type": "string", "description": "what the diagram should show; defaults to the outline's idea"}},
          "required": ["section", "kind"]}, draw),
]

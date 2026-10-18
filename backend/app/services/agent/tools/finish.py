import logging

from app.core import db
from app.helpers.lines import invariant_ok
from app.models.agent import Tool
from app.services import storage
from app.services.jobs import repository
from app.services.page import render

log = logging.getLogger("velma.finish")


def open_items(ws: dict, article: str) -> list[str]:
    if not ws["section_order"]:
        return ["outline missing: run outline first"]
    items = []
    if not invariant_ok(article.splitlines(), ws.get("title_line"), [ws["sections"][k] for k in ws["section_order"]]):
        items.append("outline broken: the sections do not add up to the article; run outline again")
    if not str(ws.get("tldr") or "").strip():
        items.append("tldr is missing: pass tldr to finish")
    if not 3 <= len(ws.get("takeaways") or []) <= 5:
        items.append("takeaways must be 3 to 5 items: pass takeaways to finish")
    for sid in ws["section_order"]:
        st = ws["sections"][sid]
        # no rewrite = kept as is; a rewrite, or an outline mark rewrite=true, needs a passing current check
        if (st.get("rewritten") or st.get("rewrite")) and render.current_rewrite(ws, sid) is None:
            chk = st.get("check")
            items.append(f"{sid}: rewrite needs a passing check" + (
                f" (missing {len(chk['missing'])}, unsupported {len(chk['unsupported'])}, or text changed since)" if chk else ""))
    for did, d in ws["diagrams"].items():
        cur = render.current_rewrite(ws, d["section"])
        if d["kind"] == "svg" and not d.get("url"):
            items.append(f"{did}: diagram is not uploaded")
        elif cur is None or d.get("rev") != ((ws["sections"][d["section"]].get("check") or {}).get("rev")):
            items.append(f"{did}: drawn from text that is no longer the checked rewrite; draw {d['section']} again")
    return items


def finish(args: dict, tctx) -> dict:
    ws = tctx.ctx.workspace
    draft = ws.get("outline") or {}  # the outline's own tldr and takeaways are the default
    ws.setdefault("tldr", draft.get("tldr"))
    ws.setdefault("takeaways", draft.get("takeaways"))
    if isinstance(args.get("tldr"), str) and args["tldr"].strip():
        ws["tldr"] = args["tldr"].strip()
    if isinstance(args.get("takeaways"), list):
        ws["takeaways"] = [str(t).strip() for t in args["takeaways"] if str(t).strip()]
    if todo := open_items(ws, tctx.ctx.article):
        log.info("finish todo n=%d items=%s", len(todo), "; ".join(todo)[:200])
        return {"ok": False, "todo": todo}
    data = render.build_page_data(ws)
    page = render.render_page(data)
    if problems := render.check_page(page, ws):
        return {"ok": False, "error": "page check failed", "problems": problems}
    url = storage.put_public(ws["public_prefix"], "index.html", page.encode(), "text/html; charset=utf-8")
    conn = db.connect()
    try:
        owned = repository.mark_done(conn, tctx.job_id, tctx.worker_id, url)
    finally:
        conn.close()
    tctx.ctx.finished = True  # also when not owned: the loop must stop
    if not owned:
        return {"ok": False, "error": "job no longer owned"}
    return {"ok": True, "url": url}


TOOLS = [
    Tool("finish", "Build the page, check it, upload it and mark the job done. Optionally pass tldr (1-3 sentences) and "
         "takeaways (3-5 short items) to replace the outline's drafts. If anything is still open, returns a todo list instead.",
         {"type": "object", "properties": {
             "tldr": {"type": "string"},
             "takeaways": {"type": "array", "items": {"type": "string"}, "minItems": 3, "maxItems": 5}}},
         finish),
]

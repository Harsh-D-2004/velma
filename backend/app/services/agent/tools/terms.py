import re
from pathlib import Path

from app.helpers import tavily
from app.models.agent import Tool
from app.services.llm import limiter, text

EXPLAIN_PROMPT = (Path(__file__).parent.parent / "prompts" / "explain.md").read_text()


def web_search(args: dict, tctx) -> dict:
    query = str(args.get("query", "")).strip()
    if not query:
        return {"ok": False, "error": "query is required"}
    if not limiter.acquire("tavily", block=False):
        return {"ok": False, "error": "quota exhausted"}
    try:
        found = tavily.search(query, max_results=5)
    except Exception as e:
        return {"ok": False, "error": f"search failed: {type(e).__name__}"}
    store = tctx.ctx.workspace["search"]
    out = []
    for r in found:
        rid = next((k for k, v in store.items() if v["url"] == r["url"]), None)  # same page -> same id
        if rid is None:
            rid = f"r{len(store) + 1}"
            store[rid] = r
        out.append({"id": rid, "title": r["title"], "url": r["url"], "snippet": r["content"][:300]})
    return {"ok": True, "results": out}


def explain_term(args: dict, tctx) -> dict:
    ws = tctx.ctx.workspace
    section = args.get("section")
    sec = ws["sections"].get(section)
    if sec is None:
        return {"ok": False, "error": f"unknown section '{section}'; run outline first"}
    raw = args.get("terms") if isinstance(args.get("terms"), list) else [args.get("term")]
    items, seen = [], set()  # (term, search result or None)
    for t in raw:
        name, source = (t.get("term"), t.get("source")) if isinstance(t, dict) else (t, None)
        name, source = str(name or "").strip(), source or args.get("source")
        if not name or name.lower() in seen:
            continue
        seen.add(name.lower())
        src = None
        if source:
            src = next((v for k, v in ws["search"].items() if source in (k, v["url"])), None)
            if src is None:
                return {"ok": False, "error": "source is not among the search results; use an id or url from web_search"}
        items.append((name, src))
    if not items:
        return {"ok": False, "error": "term or terms is required"}
    items = items[:8]
    user = f"CONTEXT:\n{sec['text'][:2000]}\n\nTERMS:\n"
    for i, (name, src) in enumerate(items, 1):
        user += f"{i}. {name}\n"
        if src:
            user += f"   SOURCE ({src['url']}): {src['content'][:1000]}\n"
    out = text.chat_json([{"role": "system", "content": EXPLAIN_PROMPT}, {"role": "user", "content": user}],
                         task="explain", ctx=tctx.ctx)
    got = {str(d.get("term", "")).strip().lower(): d for d in out.get("definitions") or [] if isinstance(d, dict)}
    defined, unsure = [], []
    for name, src in items:
        d = got.get(name.lower()) or {}
        definition = " ".join(re.split(r"(?<=[.!?])\s+", str(d.get("definition", "")).strip())[:2])
        if d.get("unsure") or not definition:
            unsure.append(name)
            continue
        url = src["url"] if src else None
        ws["glossary"][name.lower()] = {"term": name, "definition": definition, "section": section, "source_url": url}
        defined.append({"term": name, "definition": definition, "source_url": url})
    return {"ok": True, "defined": defined, "unsure": unsure}


TOOLS = [
    Tool("web_search", "Search the web via Tavily for an unfamiliar term. Returns results with ids (r1, r2, ...) "
         "you can pass to explain_term as source.",
         {"type": "object", "properties": {"query": {"type": "string"}}, "required": ["query"]}, web_search),
    Tool("explain_term", "Write one- or two-line definitions of terms for a section in ONE call, optionally based "
         "on a web_search result (source = its id or url). Shown below the section as added explanation. "
         "Unsure terms come back in 'unsure' and stay undefined.",
         {"type": "object", "properties": {
             "terms": {"type": "array", "items": {"type": "string"}, "description": "terms to define; or one 'term'"},
             "term": {"type": "string"}, "section": {"type": "string", "description": "section id, e.g. s1"},
             "source": {"type": "string", "description": "result id (r1) or url from web_search, used for every term"}},
          "required": ["section"]}, explain_term),
]

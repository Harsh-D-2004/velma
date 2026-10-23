import logging
import re
from pathlib import Path

from app.core.settings import settings
from app.helpers import tavily
from app.models.agent import Tool
from app.services.llm import limiter, text

EXPLAIN_PROMPT = (Path(__file__).parent.parent / "prompts" / "explain.md").read_text()


log = logging.getLogger("velma.terms")
NO_USE = ("quota exhausted", "no results")  # search outcomes that allow an unsourced definition


def _search_one(term: str, ws: dict) -> dict:
    """One Tavily query -> {"term", "status", "results"}; status is ok, no results, quota exhausted or search failed."""
    if not limiter.acquire("tavily", block=False):
        return {"term": term, "status": "quota exhausted", "results": []}
    try:
        found = tavily.search(term, max_results=settings.search_results_per_term)
    except Exception as e:
        return {"term": term, "status": f"search failed: {type(e).__name__}", "results": []}
    store, out = ws["search"], []
    for r in found[:settings.search_results_per_term]:
        rid = next((k for k, v in store.items() if v["url"] == r["url"]), None)  # same page -> same id
        if rid is None:
            rid = f"r{len(store) + 1}"
            store[rid] = r
        out.append({"id": rid, "title": r["title"], "url": r["url"], "snippet": r["content"][:settings.search_snippet_chars]})
    return {"term": term, "status": "ok" if out else "no results", "results": out}


def web_search(args: dict, tctx) -> dict:
    raw = args.get("terms") if isinstance(args.get("terms"), list) else [args.get("query")]
    terms = list(dict.fromkeys(str(t).strip() for t in raw if t and str(t).strip()))
    if not terms:
        return {"ok": False, "error": "terms or query is required"}
    ws = tctx.ctx.workspace
    status = ws.setdefault("search_status", {})
    searched = [_search_one(t, ws) for t in terms]
    for r in searched:
        status[r["term"].lower()] = r["status"]
        log.info("search term=%r status=%s results=%d", r["term"][:80], r["status"], len(r["results"]))
    log.info("search terms=%d tavily_remaining=%s", len(terms), limiter.remaining("tavily"))
    return {"ok": True, "searched": searched}


def explain_term(args: dict, tctx) -> dict:
    ws = tctx.ctx.workspace
    section = args.get("section")
    sec = ws["sections"].get(section)
    if sec is None:
        return {"ok": False, "error": f"unknown section '{section}'; run outline first"}
    raw = args.get("terms") if isinstance(args.get("terms"), list) else [args.get("term")]
    items, seen = [], set()  # (term, [search results]); empty list = unsourced
    for t in raw:
        name, source = (t.get("term"), t.get("source")) if isinstance(t, dict) else (t, None)
        name, source = str(name or "").strip(), source or args.get("source")
        if not name or name.lower() in seen:
            continue
        seen.add(name.lower())
        srcs = []
        for ref in (source if isinstance(source, list) else [source] if source else []):
            hit = next((v for k, v in ws["search"].items() if ref in (k, v["url"])), None)
            if hit is None:
                return {"ok": False, "error": "source is not among the search results; use an id or url from web_search"}
            srcs.append(hit)
        if not srcs and settings.explain_require_source and ws.get("search_status", {}).get(name.lower()) not in NO_USE:
            log.warning("explain_term refused term=%r reason=no_source", name[:80])
            return {"ok": False, "error": f"search first: call web_search for '{name}' and pass the result ids as source"}
        items.append((name, srcs))
    if not items:
        return {"ok": False, "error": "term or terms is required"}
    items = items[:8]
    user = f"CONTEXT:\n{sec['text'][:2000]}\n\nTERMS:\n"
    for i, (name, srcs) in enumerate(items, 1):
        user += f"{i}. {name}\n"
        for src in srcs:
            user += f"   SOURCE ({src['url']}): {src['content'][:1000]}\n"
        if not srcs and not settings.explain_require_source:
            user += "   (no SOURCE: you may use your own knowledge)\n"
    try:
        out = text.chat_json([{"role": "system", "content": EXPLAIN_PROMPT}, {"role": "user", "content": user}],
                             task="explain", ctx=tctx.ctx)
    except text.ModelCallsFailed as e:  # a definition is optional: don't spend the job's only retry on it
        log.warning("explain_term skipped terms=%s reason=%s", [n[:40] for n, _ in items], e)
        return {"ok": False, "error": "model unavailable, skip these terms and continue"}
    got = {str(d.get("term", "")).strip().lower(): d for d in out.get("definitions") or [] if isinstance(d, dict)}
    defined, unsure = [], []
    for name, srcs in items:
        d = got.get(name.lower()) or {}
        definition = " ".join(re.split(r"(?<=[.!?])\s+", str(d.get("definition", "")).strip())[:settings.explain_max_sentences])
        if d.get("unsure") or not definition:
            unsure.append(name)
            log.info("explain_term term=%r result=unsure", name[:80])
            continue
        url = srcs[0]["url"] if srcs else None
        ws["glossary"][name.lower()] = {"term": name, "definition": definition, "section": section,
                                        "source_url": url, "unsourced": not srcs}
        defined.append({"term": name, "definition": definition, "source_url": url, **({"unsourced": True} if not srcs else {})})
        log.info("explain_term term=%r result=defined unsourced=%s", name[:80], str(not srcs).lower())
    return {"ok": True, "defined": defined, "unsure": unsure}


TOOLS = [
    Tool("web_search", "Look up terms on the web (Tavily) before you define them. Pass terms: [\"LRU cache\", \"cache miss\"] "
         "(one or more) or a free-text query. For each term you get up to 3 results, each with an id (r1, r2, ...), title, URL "
         "and a short snippet. Use the ids as source in explain_term. Search the exact term plus a hint of the topic "
         "(for example \"LRU cache eviction\"). A term can come back with status \"quota exhausted\" or \"no results\"; "
         "then define it from the article context only. No other web access exists; bash has no network.",
         {"type": "object", "properties": {
             "terms": {"type": "array", "items": {"type": "string"}, "description": "terms to look up, one search each"},
             "query": {"type": "string", "description": "free-text query, instead of terms"}}}, web_search),
    Tool("explain_term", "Write a one- or two-sentence plain-English definition for each term in one section that a "
         "non-expert would not know. Always call web_search first and pass its result ids as source. Definitions come "
         "from those search snippets, never from your own memory, and keep the source link. Pass all of a section's "
         "terms in one call: terms, section, source (result ids, shared by all terms). Terms the snippets do not explain "
         "come back as unsure and stay undefined. Definitions appear in italics under the section as added explanation.",
         {"type": "object", "properties": {
             "terms": {"type": "array", "items": {"type": "string"}, "description": "terms to define; or one 'term'"},
             "term": {"type": "string"}, "section": {"type": "string", "description": "section id, e.g. s1"},
             "source": {"anyOf": [{"type": "string"}, {"type": "array", "items": {"type": "string"}}],
                        "description": "result id (r1) or url, or a list of them, from web_search; used for every term"}},
          "required": ["section"]}, explain_term),
]

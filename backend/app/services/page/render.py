import hashlib
import html
import re
from pathlib import Path

from jinja2 import Environment, FileSystemLoader
from markupsafe import Markup, escape

from app.core.settings import settings
from app.helpers.lines import body_of

_ENV = Environment(loader=FileSystemLoader(Path(__file__).parent / "templates"), autoescape=True)
_FENCE = re.compile(r"^\s*(```|~~~)\s*([\w+-]*)")
_ITEM = re.compile(r"^\s*(?:[-*+]|\d+[.)])\s+(.*)")
_ORDERED = re.compile(r"^\s*\d+[.)]\s")


def current_rewrite(ws: dict, sid: str) -> str | None:
    """The section's rewrite if its latest check passed on exactly this text, else None."""
    st = ws["sections"].get(sid) or {}
    rw, chk = st.get("rewritten"), st.get("check")
    if rw and chk and chk.get("passed") and chk.get("rev") == hashlib.sha1(rw.encode()).hexdigest()[:8]:
        return rw
    return None


def _list(lines: list[str]) -> str:
    items: list[str] = []
    for l in lines:
        if m := _ITEM.match(l):
            items.append(m.group(1))
        elif items:
            items[-1] += " " + l.strip()  # continuation line
    tag = "ol" if _ORDERED.match(lines[0]) else "ul"
    return f"<{tag}>" + "".join(f"<li>{escape(i)}</li>" for i in items) + f"</{tag}>"


def _table(text: str) -> str:
    rows = [[c.strip() for c in l.strip().strip("|").split("|")] for l in text.splitlines()]
    rows = [r for r in rows if not all(re.fullmatch(r":?-{2,}:?", c) for c in r)]  # drop the --- row
    out = ["<table>"]
    for i, r in enumerate(rows):
        tag = "th" if i == 0 else "td"
        out.append("<tr>" + "".join(f"<{tag}>{escape(c)}</{tag}>" for c in r) + "</tr>")
    return "".join(out) + "</table>"


def _code(lines: list[str], lang: str) -> str:
    cls = f' class="language-{escape(lang)}"' if lang else ""
    return f"<pre><code{cls}>{escape(chr(10).join(lines))}</code></pre>"


def _html(text: str, br: bool = True) -> Markup:
    """Escape first, then wrap: ``` = code, | = table, > = quote, blank line = new paragraph, - / 1. = list, # = heading."""
    out: list[str] = []
    para: list[str] = []
    items: list[str] = []
    lines = text.splitlines()

    def flush():
        if para:
            out.append("<p>" + ("<br>" if br else " ").join(str(escape(l.strip())) for l in para) + "</p>")
            para.clear()
        if items:
            out.append(_list(items))
            items.clear()

    i = 0
    while i < len(lines):
        line = lines[i]
        i += 1
        if m := _FENCE.match(line):
            flush()
            code = []
            while i < len(lines) and not lines[i].strip().startswith(m.group(1)):
                code.append(lines[i])
                i += 1
            i += 1  # closing fence
            out.append(_code(code, m.group(2)))
        elif line.lstrip().startswith(("|", ">")):
            flush()
            ch, run = line.lstrip()[0], [line]
            while i < len(lines) and lines[i].lstrip().startswith(ch):
                run.append(lines[i])
                i += 1
            if ch == "|":
                out.append(_table("\n".join(run)))
            else:
                out.append(f"<blockquote><p>{escape(chr(32).join(l.lstrip().lstrip(chr(62)).strip() for l in run))}</p></blockquote>")
        elif not line.strip():
            flush()
        elif (h := re.match(r"^\s*#{1,6}\s+(.*)", line)):
            flush()
            out.append(f"<p><strong>{escape(h.group(1))}</strong></p>")
        elif _ITEM.match(line):
            if para:
                flush()
            items.append(line)
        elif items and line[:1] in " \t":
            items.append(line)
        else:
            if items:
                flush()
            para.append(line)
    flush()
    return Markup("".join(out))


def build_page_data(ws: dict) -> dict:
    sections = []
    for sid in ws["section_order"]:
        s = ws["sections"][sid]
        rw = current_rewrite(ws, sid)
        visuals = []
        for d in ws["diagrams"].values():
            if d["section"] != sid:
                continue
            if d["kind"] == "svg" and d.get("url"):
                visuals.append({"kind": "svg", "url": d["url"], "alt": d["alt"],
                                "wide": d.get("width", 0) > 1.6 * d.get("height", 0) > 0})
            elif d["kind"] == "table":
                visuals.append({"kind": "table", "columns": d["columns"], "rows": d["rows"]})
        if rw is not None:
            mode = "simplified_visual" if visuals else "simplified"
            body = _html(rw)
        else:
            mode, visuals = "original", []
            body = _html(body_of(s), br=False)  # body_of: h2 already shows the title
        notes = [{"term": g["term"], "definition": g["definition"], "source_url": g["source_url"] if str(g.get("source_url") or "").startswith(("http://", "https://")) else None}
                 for g in ws["glossary"].values() if g["section"] == sid]
        sections.append({"id": sid, "heading": s["title"], "mode": mode, "body_html": body,
                         "visuals": visuals, "notes": notes, "wide": any(v.get("wide") for v in visuals)})
    return {"title": ws["title"], "tldr": ws["tldr"], "sections": sections, "takeaways": ws["takeaways"]}


def render_page(data: dict) -> str:
    return _ENV.get_template("explainer.html.j2").render(**data)


def check_page(page: str, ws: dict) -> list[str]:
    errs = []
    known = {d["url"] for d in ws["diagrams"].values() if d.get("url")}
    for src in re.findall(r'<img[^>]*\ssrc="([^"]*)"', page):
        if html.unescape(src) not in known:
            errs.append(f"unknown image src: {src}")
    if len(page.encode()) > settings.max_html_bytes:
        errs.append(f"page is over {settings.max_html_bytes} bytes")
    if '<meta name="robots" content="noindex">' not in page:
        errs.append("noindex is missing")
    if re.search(r"<script", page, re.I):
        errs.append("page contains a script")
    if re.findall(r'<section id="(s\d+)"', page) != ws["section_order"]:
        errs.append("section order does not match the article")
    return errs

import hashlib
import html
import re
from pathlib import Path

from jinja2 import Environment, FileSystemLoader
from markupsafe import Markup, escape

from app.core.settings import settings
from app.helpers.lines import body_of

_ENV = Environment(loader=FileSystemLoader(Path(__file__).parent / "templates"), autoescape=True)
_ENV.filters["inline"] = lambda s: Markup(_inline(s))  # model-written TL;DR, takeaways, notes
_FENCE = re.compile(r"^\s*(```|~~~)\s*([\w+-]*)")
_ITEM = re.compile(r"^\s*(?:[-*+]|\d+[.)])\s+(.*)")
_ORDERED = re.compile(r"^\s*\d+[.)]\s")
_BR = re.compile(r"&lt;br\s*/?&gt;", re.I)
_CODE_SPAN = re.compile(r"`([^`]+)`")
_BOLD = re.compile(r"\*\*(.+?)\*\*")


def _inline(text: str) -> str:
    """Escape, then allow the inline Markdown articles use: <br>, `code`, **bold**.
    shortcut: no *italic* (clashes with 2*3 maths), add if articles need it."""
    s = _BR.sub("<br>", str(escape(text)))
    s = _CODE_SPAN.sub(r"<code>\1</code>", s)
    return _BOLD.sub(r"<strong>\1</strong>", s)


def current_rewrite(ws: dict, sid: str) -> str | None:
    """The section's rewrite if its latest check passed on exactly this text, else None."""
    st = ws["sections"].get(sid) or {}
    if st.get("fallback"):
        return None
    rw, chk = st.get("rewritten"), st.get("check")
    if rw and chk and chk.get("passed") and chk.get("rev") == hashlib.sha1(rw.encode()).hexdigest()[:8]:
        return rw
    return None


def shows_original(st: dict) -> bool:
    """True when the page shows the section's original text (rewrite:false, or fell back after failed checks)."""
    return bool(st.get("fallback")) or (st.get("rewrite") is False and not st.get("rewritten"))


def _list(lines: list[str]) -> str:
    items: list[str] = []
    for l in lines:
        if m := _ITEM.match(l):
            items.append(m.group(1))
        elif items:
            items[-1] += " " + l.strip()  # continuation line
    tag = "ol" if _ORDERED.match(lines[0]) else "ul"
    return f"<{tag}>" + "".join(f"<li>{_inline(i)}</li>" for i in items) + f"</{tag}>"


def _table(text: str) -> str:
    rows = [[c.strip() for c in l.strip().strip("|").split("|")] for l in text.splitlines()]
    rows = [r for r in rows if not all(re.fullmatch(r":?-{2,}:?", c) for c in r)]  # drop the --- row
    out = ['<div class="table"><table>']
    for i, r in enumerate(rows):
        tag = "th" if i == 0 else "td"
        out.append("<tr>" + "".join(f"<{tag}>{_inline(c)}</{tag}>" for c in r) + "</tr>")
    return "".join(out) + "</table></div>"


FOLD_CODE_LINES = 8  # longer code/log blocks are folded behind "Show N lines" so they don't break the reading flow


def _code(lines: list[str], lang: str) -> str:
    cls = f' class="language-{escape(lang)}"' if lang else ""
    pre = f"<pre><code{cls}>{escape(chr(10).join(lines))}</code></pre>"
    if len(lines) <= FOLD_CODE_LINES:
        return pre
    what = f"{len(lines)} lines of {escape(lang)}" if lang else f"{len(lines)} lines"
    return f'<details class="code"><summary>Show {what}</summary>{pre}</details>'


_P = re.compile(r"<p>(.*?)</p>", re.S)
_TAG = re.compile(r"(<[^>]+>)")


def _lead(body: str) -> str:
    """Wrap the section's first sentence in <span class="lead"> so skimmers get each section's point."""
    if not body.startswith("<p>"):
        return body
    end = body.index("</p>")
    inner = body[3:end]
    m = re.match(r"(.+?[.!?])(?=\s|<br>|$)", inner)
    if not m or any(m.group(1).count(f"<{t}>") != m.group(1).count(f"</{t}>") for t in ("strong", "code")):
        return body  # sentence would cut through a tag
    return f'<p><span class="lead">{m.group(1)}</span>{inner[m.end():]}</p>{body[end + 4:]}'


def _sidenote(ns: list[dict]) -> str:
    """One aside per paragraph: the wide layout pins it at the paragraph's top, so two would overlap."""
    out = []
    for n in ns:
        src = f' <a href="{escape(n["source_url"])}">source</a>' if n["source_url"] else ""
        out.append(f'<span class="sn"><strong>{escape(n["term"])}</strong> {_inline(n["definition"])}{src}</span>')
    return '<aside class="sidenote">' + "".join(out) + "</aside>"


def _place_notes(body: str, notes: list[dict]) -> tuple[str, list[dict]]:
    """Put each definition as a sidenote before the first paragraph that uses its term, and underline the term there.
    Returns the new body and the notes no paragraph mentions (shown in a block after the section)."""
    paras = list(_P.finditer(body))
    inners = [m.group(1) for m in paras]
    at: dict[int, list[dict]] = {}
    left = []
    for n in notes:
        pat = re.compile(r"(?<![\w-])" + re.escape(str(escape(n["term"]))) + r"(?:e?s)?(?![\w-])", re.I)  # plural too
        for k, inner in enumerate(inners):
            parts = _TAG.split(inner)
            hit = next((i for i, x in enumerate(parts) if not x.startswith("<") and pat.search(x)
                        and (i == 0 or parts[i - 1] not in ("<code>", '<span class="term">'))), None)
            if hit is None:
                continue
            parts[hit] = pat.sub(lambda t: f'<span class="term">{t.group(0)}</span>', parts[hit], count=1)
            inners[k] = "".join(parts)
            at.setdefault(k, []).append(n)
            break
        else:
            left.append(n)
    out, pos = [], 0
    for k, m in enumerate(paras):
        out += [body[pos:m.start()], _sidenote(at[k]) if k in at else "", f"<p>{inners[k]}</p>"]
        pos = m.end()
    return "".join(out) + body[pos:], left


def _words(html_text: str) -> int:
    return len(_TAG.sub(" ", html_text).split())


def _html(text: str, br: bool = True) -> Markup:
    """Escape first, then wrap: ``` = code, | = table, > = quote, blank line = new paragraph, - / 1. = list, # = heading."""
    out: list[str] = []
    para: list[str] = []
    items: list[str] = []
    lines = text.splitlines()

    def flush():
        if para:
            out.append("<p>" + ("<br>" if br else " ").join(_inline(l.strip()) for l in para) + "</p>")
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
                out.append(f"<blockquote><p>{_inline(chr(32).join(l.lstrip().lstrip(chr(62)).strip() for l in run))}</p></blockquote>")
        elif not line.strip():
            flush()
        elif (h := re.match(r"^\s*#{1,6}\s+(.*)", line)):
            flush()
            out.append(f"<p><strong>{_inline(h.group(1))}</strong></p>")
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


READ_WPM = 230  # adult silent-reading speed for the "N min read" line


def build_page_data(ws: dict) -> dict:
    sections, fig_no, words = [], 0, 0
    for sid in ws["section_order"]:
        s = ws["sections"][sid]
        rw = current_rewrite(ws, sid)
        visuals = []
        plain_orig = shows_original(s)  # these sections may carry a diagram drawn from the original
        for d in ws["diagrams"].values():
            if d["section"] != sid or (rw is None and not (plain_orig and d.get("rev") == "original")):
                continue
            caption = re.sub(r"^Diagram:\s*", "", d.get("alt", ""))
            if d["kind"] == "svg" and d.get("url"):
                fig_no += 1
                visuals.append({"kind": "svg", "url": d["url"], "alt": d["alt"], "n": fig_no, "caption": caption,
                                "wide": d.get("width", 0) > 1.6 * d.get("height", 0) > 0})
            elif d["kind"] == "table":
                fig_no += 1
                visuals.append({"kind": "table", "columns": d["columns"], "rows": d["rows"], "n": fig_no,
                                "caption": caption, "wide": len(d["columns"]) > 3})
        if rw is not None:
            mode = "simplified_visual" if visuals else "simplified"
            body = _html(rw)
        else:
            mode = "simplified_visual" if visuals else "original"  # simplified_visual is only the template's two-column layout
            body = _html(body_of(s), br=False)  # body_of: h2 already shows the title
        notes = [{"term": g["term"], "definition": g["definition"], "source_url": g["source_url"] if str(g.get("source_url") or "").startswith(("http://", "https://")) else None}
                 for g in ws["glossary"].values() if g["section"] == sid]
        words += _words(str(body))
        body, notes = _place_notes(_lead(str(body)), notes)
        sections.append({"id": sid, "heading": s["title"], "mode": mode, "body_html": Markup(body),
                         "visuals": visuals, "notes": notes, "fallback": bool(s.get("fallback")), "wide": any(v.get("wide") for v in visuals)})
    return {"title": ws["title"], "tldr": ws["tldr"], "sections": sections, "takeaways": ws["takeaways"],
            "minutes": max(1, round(words / READ_WPM)), "figures": fig_no}


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

"""Line-level helpers for article sections. Pure functions: no services, no model."""
import re

_FENCE = re.compile(r"^\s*(```|~~~)")


def protected(lines: list[str]) -> set[int]:
    """1-based line numbers where a section must not start: inside a code fence, or in a table run after its first line."""
    prot, fence, prev_table = set(), None, False
    for n, l in enumerate(lines, 1):
        s = l.strip()
        if fence:
            prot.add(n)  # includes the closing fence line
            if s.startswith(fence):
                fence = None
            continue
        if m := _FENCE.match(l):
            fence, prev_table = m.group(1), False
            continue
        is_table = s.startswith("|")
        if is_table and prev_table:
            prot.add(n)
        prev_table = is_table
    return prot


def first_content(lines: list[str], title_line: int | None) -> int | None:
    """First non-blank line number that is not the title line."""
    return next((n for n, l in enumerate(lines, 1) if l.strip() and n != title_line), None)


def slice_text(lines: list[str], start: int, end: int, title_line: int | None) -> str:
    return "\n".join(l for n, l in enumerate(lines[start - 1:end], start) if n != title_line)


def invariant_ok(lines: list[str], title_line: int | None, sections: list[dict]) -> bool:
    """The title line plus all section texts, in order, reproduce the article's lines exactly."""
    if not sections:
        return False
    first = sections[0]["start"]
    if any(l.strip() and n != title_line for n, l in enumerate(lines[:first - 1], 1)):
        return False  # only blanks and the title may come before the first section
    expected = [l for n, l in enumerate(lines[first - 1:], first) if n != title_line]
    return "\n".join(expected) == "\n".join(s["text"] for s in sections)


def body_of(s: dict) -> str:
    """Section text without a first line that only repeats the section title (the page shows the title as h2)."""
    first, _, rest = s["text"].partition("\n")
    norm = lambda x: re.sub(r"^#+\s*", "", x.strip()).casefold()
    return rest if norm(first) == norm(s["title"]) else s["text"]


def prose_only(text: str) -> str:
    """Text without fenced code, table lines and quote lines (for readability scoring)."""
    out, fence = [], None
    for l in text.split("\n"):
        if fence:
            if l.strip().startswith(fence):
                fence = None
        elif m := _FENCE.match(l):
            fence = m.group(1)
        elif not l.lstrip().startswith(("|", ">")):
            out.append(l)
    return "\n".join(out)


_LIST = re.compile(r"^\s*(?:[-*+]|\d+[.)])\s")


def inside_block(lines: list[str], prot: set[int], n: int) -> bool:
    """True when line n continues a code fence, table, quote or list begun above it (no section may start here)."""
    if n in prot:
        return True
    if n < 2:
        return False
    cur, prev = lines[n - 1], lines[n - 2]
    if cur.lstrip().startswith(">") and prev.lstrip().startswith(">"):
        return True
    return bool(prev.strip()) and (bool(_LIST.match(prev)) or prev[:1] in " \t") and (bool(_LIST.match(cur)) or cur[:1] in " \t")


def _break_rank(lines: list[str], prot: set[int], k: int) -> tuple[int, str] | None:
    """Rank (1 best) of cutting after line k, or None if the cut is not allowed."""
    n = len(lines)
    if k >= n or inside_block(lines, prot, k + 1) or not lines[k].strip():
        return None  # the next chunk must start on a real line outside any block
    nxt, cur = lines[k], lines[k - 1]
    words = nxt.split()
    if nxt.lstrip().startswith("#") or (
            not cur.strip() and len(words) <= 12 and not nxt.rstrip().endswith(".")
            and not nxt.lstrip().startswith(("|", ">", "```", "~~~")) and not _LIST.match(nxt)):
        return 1, "heading"
    if not cur.strip():
        return 2, "blank"
    if cur.rstrip().rstrip("\"')”’*_").endswith((".", "!", "?")):
        return 3, "sentence"
    return None


def smart_chunks(lines: list[str], max_words: int, cut_search: float) -> list[tuple[int, str]]:
    """Chunk end lines as (end_line, break_type). Cuts at the best break in the last `cut_search` share of the
    chunk (heading > blank line > sentence end), never inside a block; if none, extends forward to the next one.
    shortcut: cuts fall between lines, so a single huge line is never split."""
    prot, n, out, lo = protected(lines), len(lines), [], 1
    while lo <= n:
        words, limit = 0, None
        for i in range(lo, n + 1):
            words += len(lines[i - 1].split())
            if words >= max_words:
                limit = i
                break
        if limit is None or sum(len(l.split()) for l in lines[limit:]) == 0:
            out.append((n, "end"))
            break
        floor, w = limit, 0
        while floor > lo and w < cut_search * max_words:  # back to where the last cut_search share starts
            w += len(lines[floor - 1].split())
            floor -= 1
        back = [(r, -k, k, t) for k in range(floor, limit + 1) if (rt := _break_rank(lines, prot, k)) for r, t in [rt]]
        fwd = next(((r, k, t) for k in range(limit + 1, n) if (rt := _break_rank(lines, prot, k)) for r, t in [rt]), None)
        if back:
            _, _, k, t = min(back)  # best rank, then the latest line
        elif fwd:
            _, k, t = fwd
        else:
            out.append((n, "end"))
            break
        out.append((k, t))
        lo = k + 1
    return out

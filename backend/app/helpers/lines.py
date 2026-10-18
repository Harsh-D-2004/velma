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

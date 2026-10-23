"""Style rules the check tool applies to a rewrite: length and reading grade, on prose only (code, tables, quotes out)."""
import math
import re

import textstat

from app.core.settings import settings
from app.helpers.lines import prose_only

_LIST_ITEM = re.compile(r"^\s*(?:[-*+]|\d+[.)])\s+", re.M)  # the marker is not a word


def _sentences(prose: str) -> list[str]:
    return [x for x in re.split(r"(?<=[.!?])\s+|\n+", prose) if x.strip()]


def measure(original: str, rewrite: str) -> dict:
    """Length counts list items; the grade uses running sentences only (a 3-word bullet is not a short sentence).
    grade is None below VELMA_REWRITE_MIN_WORDS_FOR_GRADE words of running text: the formula is noise there."""
    o, r = prose_only(original), prose_only(rewrite)
    lines = r.split("\n")
    items = sum(len(_LIST_ITEM.sub("", l).split()) for l in lines if _LIST_ITEM.match(l))
    sents = _sentences("\n".join(l for l in lines if not _LIST_ITEM.match(l)))
    running = sum(len(x.split()) for x in sents)
    # textstat splits sentences its own way; give it ours, each ended with a full stop
    graded = " ".join(x.strip() if x.strip()[-1] in ".!?" else x.strip() + "." for x in sents)
    ow = len(_LIST_ITEM.sub("", o).split())
    return {"orig_words": ow, "words": running + items,
            "ratio": round((running + items) / ow, 2) if ow else None,
            "grade": round(textstat.flesch_kincaid_grade(graded), 1) if running >= settings.rewrite_min_words_for_grade else None}


def problems(m: dict) -> list[str]:
    """Plain-language failures for a measure() result, written to be pasted into the next rewrite."""
    s, errs = settings, []
    if m["ratio"] is not None and m["ratio"] < s.rewrite_length_min:
        missing = math.ceil(m["orig_words"] * s.rewrite_length_target) - m["words"]
        errs.append(f"too short: {m['words']} words of {m['orig_words']} ({m['ratio']}x, must be {s.rewrite_length_min}x "
                    f"to {s.rewrite_length_max}x); about {missing} words are missing. You summarised: put back the points, "
                    "examples and details you dropped, in plain words")
    elif m["ratio"] is not None and m["ratio"] > s.rewrite_length_max:
        extra = m["words"] - math.floor(m["orig_words"] * s.rewrite_length_target)
        errs.append(f"too long: {m['words']} words of {m['orig_words']} ({m['ratio']}x, must be {s.rewrite_length_min}x "
                    f"to {s.rewrite_length_max}x); cut about {extra} words of padding or repetition, never a point")
    if m["grade"] is not None and m["grade"] > s.rewrite_max_grade:
        errs.append(f"reading grade is {m['grade']}; it must be {s.rewrite_max_grade} or lower: split long sentences "
                    "and use common words")
    return errs

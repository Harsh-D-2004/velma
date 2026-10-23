import math

from app.core.settings import settings
from app.helpers.style import measure, problems

ORIG = "Short text here. It has code.\n```\nx = 1 + 1\n```\n| a | b |\n> quote line here"


def test_prose_only_and_short_text_skips_grade():
    m = measure(ORIG, "Tiny text now. It has code.\n```\nlong code block with many many words\n```")
    assert m["orig_words"] == 6 and m["words"] == 6 and m["ratio"] == 1.0
    assert m["grade"] is None  # under VELMA_REWRITE_MIN_WORDS_FOR_GRADE


def test_list_items_count_for_length_not_for_grade():
    prose = " ".join(["The service read each token and trusted every claim inside it without a check."] * 4)  # 14 words x4
    lst = "\n\n- Database passwords\n- Token secrets\n- Email logins"
    m = measure(prose + lst, prose + lst)
    assert m["words"] == 62 and m["ratio"] == 1.0  # bullet markers are not words
    assert m["grade"] == measure(prose, prose)["grade"]  # the bullets do not change the grade


def test_too_short_says_how_many_words_are_missing():
    orig = " ".join(["word"] * 100) + "."
    errs = problems(measure(orig, " ".join(["word"] * 50) + "."))
    missing = math.ceil(100 * settings.rewrite_length_target) - 50  # read once: never put settings in an assert message
    assert "too short" in errs[0] and f"about {missing} words are missing" in errs[0]


def test_high_grade_flagged():
    hard = " ".join(["Authorization configuration necessitates comprehensive cryptographic verification procedures."] * 6)
    errs = problems(measure(hard, hard))
    assert len(errs) == 1 and errs[0].startswith("reading grade")

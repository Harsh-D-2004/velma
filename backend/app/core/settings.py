from pathlib import Path
from typing import Literal

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="VELMA_", env_file=".env", extra="ignore"
    )

    database_url: str = ""
    database_direct_url: str = ""

    max_article_chars: int = 2_000_000
    max_concurrent_jobs: int = 5
    max_job_seconds: int = 1800
    max_steps: int = 60
    max_html_bytes: int = 1_000_000
    retry_pause_seconds: int = 120

    scratch_dir: str = "./scratch"

    s3_endpoint_url: str | None = None
    archive_bucket: str = ""
    cdn_bucket: str = ""
    cdn_base_url: str = ""

    tavily_api_key: str = ""

    # Outline
    outline_chunk_words: int = 10_000  # max words per outline chunk
    outline_cut_search: float = (
        0.2  # share of a chunk, from its end, searched for the best break
    )
    outline_max_output_tokens: int = 32_768  # outline model output cap

    # Diagrams
    diagram_ratio: float = 0.6  # share of sections given a diagram (0-1)
    diagram_min_score: int = 4  # lowest visual_score (0-10) that may be drawn
    diagram_min_per_article: int = 1
    diagram_max_per_article: int = 12
    diagram_render_tries: int = 3  # render retries inside draw

    # Rewrite
    rewrite_length_min: float = 0.8  # rewrite words / original prose words
    rewrite_length_max: float = 1.1
    rewrite_length_target: float = 0.95
    rewrite_avg_sentence_min: int = 15  # target average words per sentence
    rewrite_avg_sentence_max: int = 20
    rewrite_max_sentence_words: int = 25
    rewrite_max_grade: int = 8  # Flesch-Kincaid ceiling
    rewrite_min_words_for_grade: int = (
        40  # below this only the longest-sentence rule applies
    )
    rewrite_rules: Literal["hard", "soft"] = "hard"
    rewrite_max_rounds: int = (
        2  # rewrite attempts per section before falling back to the original
    )

    # Check: a preset sets the thresholds together, any field below that is not None overrides it
    check_nli_dir: str = str(
        Path(__file__).resolve().parents[2] / "models" / "nli"
    )  # NLI model files (scripts/fetch_nli.py)
    check_strictness: Literal["lenient", "normal", "strict"] = "normal"
    check_entail_min: float | None = None
    check_contra_min: float | None = None
    check_exact_numbers: bool | None = None
    check_exact_names: bool | None = None
    check_min_kept_ratio: float | None = None
    check_top_k: int | None = None
    check_all_pairs_max_sentences: int | None = None
    check_bm25_k1: float | None = None
    check_bm25_b: float | None = None
    check_code_table_facts: Literal["exact", "nli"] | None = None

    # Terms
    explain_require_source: bool = True
    search_results_per_term: int = 3
    search_snippet_chars: int = 300
    explain_max_sentences: int = 2

    # Agent
    token_budget_base: int = 150_000
    token_budget_per_word: int = 30
    token_budget_max: int = 2_000_000
    max_nudges: int = 1  # plain-text replies tolerated before giving up
    max_tool_result_chars: int = 4000

    def check_params(self) -> dict:
        """Check thresholds: the strictness preset with individual VELMA_CHECK_* overrides applied."""
        p = dict(CHECK_PRESETS[self.check_strictness])
        for k in p:
            if (v := getattr(self, "check_" + k)) is not None:
                p[k] = v
        return p


# tuned against tests/test_check.py: the spec defaults pass all 12 cases, so they are unchanged
_NORMAL = {
    "entail_min": 0.60,
    "contra_min": 0.50,
    "exact_numbers": True,
    "exact_names": True,
    "min_kept_ratio": 1.0,
    "top_k": 3,
    "all_pairs_max_sentences": 6,
    "bm25_k1": 1.2,
    "bm25_b": 0.75,
    "code_table_facts": "exact",
}
CHECK_PRESETS = {
    "lenient": _NORMAL
    | dict(
        entail_min=0.45,
        contra_min=0.70,
        exact_numbers=False,
        exact_names=False,
        min_kept_ratio=0.8,
    ),
    "normal": _NORMAL,
    "strict": _NORMAL
    | dict(
        entail_min=0.75,
        contra_min=0.35,
        top_k=5,
        all_pairs_max_sentences=12,
        code_table_facts="nli",
    ),
}


settings = Settings()

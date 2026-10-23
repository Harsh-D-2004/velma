from dataclasses import dataclass

from app.core.settings import settings

BASE_URL = "https://generativelanguage.googleapis.com/v1beta/openai/"
API_KEY_ENV = "VELMA_GEMINI_API_KEY"


@dataclass(frozen=True)
class TaskModel:
    model: str
    max_output_tokens: int
    temperature: float
    reasoning_effort: str        # "low" | "medium" | "high"; Gemini 3 always thinks, this caps it
    timeout_s: int


TASKS = {
    "agent":   TaskModel("gemini-3.5-flash-lite", 4096, 0.2, "medium", 120),  # agent loop: tool choice, planning
    "outline": TaskModel("gemini-3.5-flash-lite", settings.outline_max_output_tokens, 0.2, "medium", 120),  # long input, fact extraction
    "rewrite": TaskModel("gemini-3.5-flash-lite", 8192, 0.3, "low",     90),  # plain-language writing
    "explain": TaskModel("gemini-3.1-flash-lite", 1024, 0.2, "low",     60),  # short definitions
    "draw":    TaskModel("gemini-3.1-flash-lite", 2048, 0.2, "low",     60),  # DOT or chart JSON; render check catches errors
}

# shortcut: conservative guesses, replace with the AI Studio console values.
RATE_LIMITS = {   # key -> [(window_seconds, max_requests, max_tokens)]; None = not set
    "gemini-3.5-flash-lite": [(60, 15, None), (86_400, 1000, None)],
    "gemini-3.1-flash-lite": [(60, 15, None), (86_400, 1000, None)],
    "tavily":                [(2_592_000, 1000, None)],
}

CONNECT_TIMEOUT_S = 10
RETRY_SLEEPS_S = (5, 15)       # 2 retries after the first try
MAX_RETRY_AFTER_S = 60         # honour a server's Retry-After up to this


def token_budget(article_words: int) -> int:
    return min(settings.token_budget_base + settings.token_budget_per_word * article_words, settings.token_budget_max)

import time

from tavily import TavilyClient

from app.core.settings import settings

TIMEOUT_S = 20
RETRY_SLEEPS_S = (1, 3)  # 2 retries


def search(query: str, max_results: int = 5) -> list[dict]:
    """Return [{"title", "url", "content"}]; raises the last error after 2 retries."""
    client = TavilyClient(api_key=settings.tavily_api_key)
    for attempt in range(len(RETRY_SLEEPS_S) + 1):
        try:
            resp = client.search(query, max_results=max_results, timeout=TIMEOUT_S)
            return [{"title": r.get("title", ""), "url": r.get("url", ""), "content": r.get("content", "")}
                    for r in resp.get("results", []) if r.get("url")]
        except Exception:
            if attempt == len(RETRY_SLEEPS_S):
                raise
            time.sleep(RETRY_SLEEPS_S[attempt])

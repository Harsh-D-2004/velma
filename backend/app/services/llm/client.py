import os

import httpx
from openai import OpenAI

from app.services.llm.config import API_KEY_ENV, BASE_URL, CONNECT_TIMEOUT_S


def get(timeout_s: float = 120) -> OpenAI:
    return OpenAI(base_url=BASE_URL, api_key=os.environ.get(API_KEY_ENV, ""), max_retries=0,
                  timeout=httpx.Timeout(timeout_s, connect=CONNECT_TIMEOUT_S))

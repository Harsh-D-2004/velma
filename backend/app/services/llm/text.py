import json
import logging
import re
import time
from dataclasses import dataclass, field

import openai
from app.services.llm import client, limiter
from app.services.llm.config import MAX_RETRY_AFTER_S, RETRY_SLEEPS_S, TASKS

log = logging.getLogger("velma.llm")


class ModelCallsFailed(Exception):
    pass


@dataclass
class ChatResult:
    content: str
    tool_calls: list = field(default_factory=list)  # [{"id", "name", "arguments": str}]
    message: dict = field(default_factory=dict)     # assistant message, ready to append to history
    tokens: int = 0
    finish_reason: str | None = None


def _sleep_for(err: Exception, default: float) -> float:
    resp = getattr(err, "response", None)
    try:
        ra = float(resp.headers.get("retry-after")) if resp is not None else None
    except (TypeError, ValueError):
        ra = None
    return ra if ra is not None and ra <= MAX_RETRY_AFTER_S else default


def _call(tm, messages, tools, json_mode, tool_choice=None):
    kw = {"model": tm.model, "messages": messages, "max_tokens": tm.max_output_tokens,
              "temperature": tm.temperature, "reasoning_effort": tm.reasoning_effort}
    if tools:
        kw["tools"] = tools
        if tool_choice:
            kw["tool_choice"] = tool_choice
    if json_mode:
        kw["response_format"] = {"type": "json_object"}
    return client.get(tm.timeout_s).chat.completions.create(**kw)


def chat(messages: list, *, task: str, ctx, tools: list | None = None, json_mode: bool = False,
         tool_choice: str | None = None) -> ChatResult:
    tm = TASKS[task]
    est = len(json.dumps(messages, default=str)) // 4
    last: Exception | None = None
    for attempt in range(len(RETRY_SLEEPS_S) + 1):
        if not limiter.acquire(tm.model, est):
            raise ModelCallsFailed(f"rate limit for {tm.model} exhausted")
        t0 = time.monotonic()
        try:
            resp = _call(tm, messages, tools, json_mode, tool_choice)
            m = resp.choices[0].message
            msg = m.model_dump(exclude_none=True)
            msg["role"] = "assistant"
            msg.setdefault("content", None)
            calls = [{"id": c.id, "name": c.function.name, "arguments": c.function.arguments or "{}"}
                     for c in (m.tool_calls or [])]
            res = ChatResult(m.content or "", calls, msg, resp.usage.total_tokens if resp.usage else est,
                             resp.choices[0].finish_reason)
            u = resp.usage
            log.info("model task=%s model=%s seconds=%.1f prompt_tokens=%s completion_tokens=%s outcome=ok",
                     task, tm.model, time.monotonic() - t0, getattr(u, "prompt_tokens", 0),
                     getattr(u, "completion_tokens", 0))
            break
        except (openai.OpenAIError, IndexError, AttributeError) as e:
            last = e
            perm = isinstance(e, (openai.BadRequestError, openai.AuthenticationError))
            outcome = "failed" if perm or attempt == len(RETRY_SLEEPS_S) else "retry"
            log.warning("model task=%s model=%s seconds=%.1f prompt_tokens=0 completion_tokens=0 outcome=%s error=%s",
                        task, tm.model, time.monotonic() - t0, outcome, type(e).__name__)
            if perm:
                raise ModelCallsFailed(f"{task}: {type(e).__name__}") from e  # permanent, don't retry
            if attempt < len(RETRY_SLEEPS_S):
                time.sleep(_sleep_for(e, RETRY_SLEEPS_S[attempt]))
    else:
        raise ModelCallsFailed(f"{task}: {type(last).__name__}") from last
    ctx.tokens += res.tokens
    ctx.model_calls += 1
    return res


def _parse(text: str) -> dict:
    text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text.strip())
    out = json.loads(text)
    if not isinstance(out, dict):
        raise ValueError("not an object")
    return out


def chat_json(messages: list, *, task: str, ctx) -> dict:
    try:
        return _parse(chat(messages, task=task, ctx=ctx, json_mode=True).content)
    except ValueError:  # bad JSON: ask again in prose mode
        ask = messages + [{"role": "user", "content": "Reply with only one valid JSON object, no other text."}]
        try:
            return _parse(chat(ask, task=task, ctx=ctx).content)
        except ValueError as e:
            raise ValueError(f"{task}: model reply was not JSON") from e  # tool error, not a job failure

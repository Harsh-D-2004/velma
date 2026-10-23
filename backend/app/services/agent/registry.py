import json
import logging
import signal
import threading
import time
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from app.core import tracing
from app.core.settings import settings
from app.models.agent import Tool
from app.services.llm.text import ModelCallsFailed

log = logging.getLogger("velma.tool")

TOOL_TIMEOUTS = {  # hard cap per tool, seconds
    "outline": 600, "rewrite": 300, "check": 180, "explain_term": 120, "web_search": 70,
    "draw": 300, "finish": 180,
}
DEFAULT_TIMEOUT = 60


class Shutdown(BaseException):
    """Raised by the runner's SIGTERM handler. BaseException so a tool's `except Exception` can't eat it."""


class ToolTimeout(BaseException):
    pass


@dataclass
class ToolContext:
    ctx: Any            # JobContext
    job_id: str
    worker_id: str
    scratch: Path


def tool_cap(name: str, args: dict) -> int:
    if name == "bash":
        t = args.get("timeout", 30)
        return min(t if isinstance(t, (int, float)) else 30, 120) + 10
    return TOOL_TIMEOUTS.get(name, DEFAULT_TIMEOUT)


@contextmanager
def time_limit(seconds: float):
    if threading.current_thread() is not threading.main_thread():
        yield  # shortcut: SIGALRM only works in the main thread; the job process runs tools there
        return

    def _fire(signum, frame):
        raise ToolTimeout(f"tool timed out after {seconds:g}s")

    old = signal.signal(signal.SIGALRM, _fire)
    signal.setitimer(signal.ITIMER_REAL, seconds)
    try:
        yield
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)
        signal.signal(signal.SIGALRM, old)


def _shrink(v, limit):
    """Cut the longest string value (recursively) by the overflow, keeping every key."""
    strs = []

    def walk(o):
        if isinstance(o, dict):
            for k, x in o.items():
                (strs.append((o, k)) if isinstance(x, str) else walk(x))
        elif isinstance(o, list):
            for i, x in enumerate(o):
                (strs.append((o, i)) if isinstance(x, str) else walk(x))

    walk(v)
    if not strs:
        return False
    o, k = max(strs, key=lambda t: len(t[0][t[1]]))
    cur = o[k]
    over = len(json.dumps(v, ensure_ascii=False, default=str)) - limit
    keep = max(len(cur) - over - 20, 0)
    o[k] = cur[:keep] + "…[truncated]"
    return True


def _fit(out) -> str:
    if isinstance(out, str):
        return out if len(out) <= settings.max_tool_result_chars else out[:settings.max_tool_result_chars - 14] + "...[truncated]"
    text = json.dumps(out, ensure_ascii=False, default=str)
    if len(text) <= settings.max_tool_result_chars:
        return text
    out = json.loads(text)
    for _ in range(50):
        if not _shrink(out, settings.max_tool_result_chars):
            break
        text = json.dumps(out, ensure_ascii=False)
        if len(text) <= settings.max_tool_result_chars:
            return text
    return json.dumps({"ok": False, "error": "result too large"})  # many keys/items, no string left to cut


def _err(msg: str) -> str:
    return json.dumps({"ok": False, "error": msg})


class Registry:
    def __init__(self, tools: list[Tool]):
        self.tools = {t.name: t for t in tools}

    def schemas(self) -> list:
        return [{"type": "function", "function": {"name": t.name, "description": t.description,
                                                  "parameters": t.parameters}} for t in self.tools.values()]

    def execute(self, name: str, raw_args: str, tctx: ToolContext) -> str:
        with tracing.span(name, "TOOL", input=raw_args) as sp:
            out = self._execute_logged(name, raw_args, tctx)
            sp.output(out)
            if out.startswith('{"ok": false'):
                sp.error(out)
            return out

    def _execute_logged(self, name: str, raw_args: str, tctx: ToolContext) -> str:
        t0 = time.monotonic()
        try:
            out = self._execute(name, raw_args, tctx)
        except BaseException as e:  # ModelCallsFailed / Shutdown pass through, but are still logged
            log.info("tool name=%s seconds=%.1f ok=false error=%s", name, time.monotonic() - t0,
                     type(e).__name__)
            raise
        err = None
        if out.startswith('{"ok": false'):
            try:
                err = str(json.loads(out).get("error", "failed"))[:200].replace("\n", " ")
            except ValueError:
                err = "failed"
        log.info("tool name=%s seconds=%.1f ok=%s%s", name, time.monotonic() - t0, str(err is None).lower(),
                 f' error="{err}"' if err else "")
        return out

    def _execute(self, name: str, raw_args: str, tctx: ToolContext) -> str:
        tool = self.tools.get(name)
        if tool is None:
            return _err(f"unknown tool '{name}'")
        try:
            args = json.loads(raw_args or "{}")
            if not isinstance(args, dict):
                raise ValueError("arguments must be a JSON object")
        except ValueError as e:
            return _err(f"bad arguments: {e}")
        try:
            with time_limit(tool_cap(name, args)):
                out = tool.fn(args, tctx)
        except (ModelCallsFailed, Shutdown):
            raise
        except ToolTimeout as e:
            return _err(str(e))
        except Exception as e:
            return _err(f"{type(e).__name__}: {e}")
        return _fit(out)

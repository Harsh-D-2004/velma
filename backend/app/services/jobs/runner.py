import ctypes
import logging
import os
import signal
import sys
import time

from app.core import db, logs
from app.services import storage
from app.services.agent import loop
from app.services.agent.tools import sandbox
from app.services.agent.explainer import build_spec
from app.services.agent.registry import Shutdown, ToolContext
from app.services.jobs import context, repository
from app.services.jobs.context import Snapshotter
from app.services.llm.text import ModelCallsFailed

log = logging.getLogger("velma.runner")


def die_with_parent() -> None:
    ctypes.CDLL("libc.so.6", use_errno=True).prctl(1, signal.SIGTERM)  # PR_SET_PDEATHSIG
    if os.getppid() == 1:  # parent already gone
        os._exit(1)


def _on_sigterm(signum, frame):
    raise Shutdown()


def _db(fn, *args):
    conn = db.connect()
    try:
        return fn(conn, *args)
    finally:
        conn.close()


def run_one_job(job_id: str, article_key: str, worker_id: str) -> None:
    die_with_parent()
    signal.signal(signal.SIGTERM, _on_sigterm)
    logs.setup(job_id)
    t0 = time.monotonic()
    ctx = scratch = snap = None
    reason = "crashed"
    try:
        log.info("job_start worker=%s", worker_id)
        ctx = context.load(job_id, storage, article_key)
        snap = Snapshotter(job_id, storage)
        scratch = sandbox.prepare_scratch(ctx)
        tctx = ToolContext(ctx, job_id, worker_id, scratch)
        try:
            outcome = loop.run_agent(build_spec(), ctx, tctx, snap.submit)
            if outcome == "finished":
                reason = "finished"
                snap.flush()
            else:
                reason = outcome
                snap.flush()
                _db(repository.mark_failed, job_id, worker_id,
                    "model stopped without finishing" if outcome == "no_finish" else "step or token limit reached")
            _db(repository.set_archive_key, job_id, snap.key)
        except ModelCallsFailed as e:
            reason = f"model_calls_failed:{e}"[:200]
            snap.flush()
            _db(repository.retry_later_or_fail, job_id, worker_id, "model calls kept failing")
        except Shutdown:
            signal.signal(signal.SIGTERM, signal.SIG_IGN)  # finish the save, don't be interrupted again
            reason = "shutdown"
            snap.flush()
            _db(repository.release, job_id, worker_id)
        if scratch:
            sandbox.remove_scratch(scratch)
        log.info("job_end reason=%s seconds=%.1f steps=%s tokens=%s model_calls=%s tool_calls=%s nudges=%s budget_warned=%s",
                 reason, time.monotonic() - t0, ctx.steps, ctx.tokens, ctx.model_calls, ctx.tool_calls,
                 ctx.nudges, str(ctx.warned).lower())
    except BaseException:  # any other failure: exit 1, the dispatcher treats it as a crash
        log.exception("job_end reason=exception seconds=%.1f", time.monotonic() - t0)
        sys.exit(1)

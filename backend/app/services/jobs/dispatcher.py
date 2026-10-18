import logging
import multiprocessing
import threading
import time
import uuid
from datetime import datetime, timezone

from app.core import db, logs
from app.core.settings import settings
from app.services.jobs import repository

log = logging.getLogger("velma.dispatcher")
STOP_WAIT_S = 25


class Dispatcher:
    def __init__(self):
        self._mp = multiprocessing.get_context("spawn")
        self._procs: dict[str, dict] = {}   # worker_id -> {proc, job_id, started}
        self._wake = threading.Event()
        self._stopping = threading.Event()
        self._thread: threading.Thread | None = None
        self._conn = None

    # --- public API (used by main.py and intake.py) ---
    def start(self) -> None:
        logs.setup()
        self._thread = threading.Thread(target=self._run, name="dispatcher", daemon=True)
        self._thread.start()

    def wake(self) -> None:
        self._wake.set()

    def stop(self) -> None:
        """SIGTERM the children, wait up to 25 s, then SIGKILL. Their rows are released by the children
        (or requeued by recover() on the next start)."""
        self._stopping.set()
        self._wake.set()
        if self._thread:
            self._thread.join(10)
        for w in self._procs.values():
            if w["proc"].is_alive():
                w["proc"].terminate()
        log.info("stop job=- children=%d sigterm sent", len(self._procs))
        deadline = time.monotonic() + STOP_WAIT_S
        for w in self._procs.values():
            w["proc"].join(max(deadline - time.monotonic(), 0))
            if w["proc"].is_alive():
                log.warning("kill job=%s reason=shutdown_timeout", w["job_id"])
                w["proc"].kill()
                w["proc"].join()
        self._close()

    # --- internals ---
    def _c(self):
        if self._conn is None or self._conn.closed:
            self._conn = db.connect()
        return self._conn

    def _close(self) -> None:
        if self._conn is not None:
            self._conn.close()
            self._conn = None

    def recover(self) -> None:
        n = repository.requeue_all_running(self._c())
        f = repository.fail_lost_uploads(self._c(), 600)
        log.info("recover job=- requeued=%d failed_lost_uploads=%d", n, f)

    def collect_exited(self) -> None:
        for wid, w in list(self._procs.items()):
            if w["proc"].is_alive():
                continue
            w["proc"].join()
            code = w["proc"].exitcode
            log.info("exit job=%s exit_code=%s", w["job_id"], code)
            reason = "job timed out" if w.get("killed") else "process crashed"
            # no-op if the child already settled the row
            try:
                status = repository.retry_later_or_fail(self._c(), w["job_id"], wid, reason)
            except Exception:
                log.exception("settle failed job=%s, will retry next pass", w["job_id"])
                self._close()
                continue
            del self._procs[wid]  # only after the DB call worked, so a crashed job never stays running
            if status:
                log.warning("settled job=%s status=%s reason=%r", w["job_id"], status, reason)

    def kill_overdue(self) -> None:
        for w in self._procs.values():
            if not w.get("killed") and time.monotonic() - w["started"] > settings.max_job_seconds:
                w["killed"] = True
                log.warning("kill job=%s reason=over_%ds", w["job_id"], settings.max_job_seconds)
                w["proc"].kill()

    def fill_slots(self) -> None:
        from app.services.jobs import runner  # spawn imports it again in the child; keep parent start light

        while len(self._procs) < settings.max_concurrent_jobs and not self._stopping.is_set():
            wid = uuid.uuid4().hex
            claimed = repository.claim_job(self._c(), wid)
            if not claimed:
                return
            job_id, article_key = claimed
            log.info("claim job=%s worker=%s", job_id, wid)
            p = self._mp.Process(target=runner.run_one_job, args=(job_id, article_key, wid))
            try:
                p.start()
            except Exception:
                log.exception("spawn failed job=%s, releasing", job_id)
                repository.release(self._c(), job_id, wid)
                return
            log.info("spawn job=%s pid=%s", job_id, p.pid)
            self._procs[wid] = {"proc": p, "job_id": job_id, "started": time.monotonic()}

    def wait(self) -> None:
        if self._procs:
            self._wake.wait(5)
            return
        at = repository.next_resume_at(self._c())
        self._close()  # idle: no open connection, so Neon can sleep
        timeout = None
        if at is not None:
            timeout = max((at - datetime.now(timezone.utc)).total_seconds(), 0) + 0.5
        log.info("idle job=- next_resume_in=%s", "none" if timeout is None else f"{timeout:.0f}s")
        self._wake.wait(timeout)

    def _run(self) -> None:
        while not self._stopping.is_set():
            try:
                self.recover()
                break
            except Exception:
                log.exception("recover failed, retrying in 5 s")
                self._close()
                self._stopping.wait(5)
        while not self._stopping.is_set():
            self._wake.clear()
            try:
                self.collect_exited()
                self.kill_overdue()
                self.fill_slots()
                self.wait()
            except Exception:
                log.exception("dispatcher loop error, retrying in 5 s")
                self._close()
                self._stopping.wait(5)


dispatcher = Dispatcher()

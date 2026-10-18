import os
import sqlite3
import time

from app.core.settings import settings
from app.services.llm.config import RATE_LIMITS

MAX_WAIT_S = 120  # shortcut: a longer wait (daily quota) fails the call instead of sleeping, raise if a queue is wanted


def _db() -> sqlite3.Connection:
    os.makedirs(settings.scratch_dir, exist_ok=True)
    db = sqlite3.connect(os.path.join(settings.scratch_dir, "usage.sqlite3"), timeout=30, isolation_level=None)
    db.execute("CREATE TABLE IF NOT EXISTS usage (key TEXT, ts REAL, tokens INT)")
    db.execute("CREATE INDEX IF NOT EXISTS usage_k ON usage (key, ts)")
    return db


def _try(db: sqlite3.Connection, key: str, tokens: int) -> float:
    """Record the call and return 0, or return seconds until it would fit."""
    limits = RATE_LIMITS.get(key, [])
    now = time.time()
    db.execute("BEGIN IMMEDIATE")
    try:
        wait = 0.0
        for window, max_req, max_tok in limits:
            rows = db.execute("SELECT ts, tokens FROM usage WHERE key=? AND ts>? ORDER BY ts",
                              (key, now - window)).fetchall()
            if max_req is not None and len(rows) >= max_req:
                wait = max(wait, rows[len(rows) - max_req][0] + window - now)
            if max_tok is not None and sum(r[1] for r in rows) + tokens > max_tok and rows:
                wait = max(wait, rows[0][0] + window - now)
        if wait <= 0:
            db.execute("INSERT INTO usage VALUES (?,?,?)", (key, now, tokens))
            if limits:
                db.execute("DELETE FROM usage WHERE key=? AND ts<?", (key, now - max(w for w, _, _ in limits)))
        db.execute("COMMIT")
        return max(wait, 0.0)
    except BaseException:
        db.execute("ROLLBACK")
        raise


def acquire(key: str, tokens: int = 0, block: bool = True) -> bool:
    db = _db()
    try:
        while True:
            wait = _try(db, key, tokens)
            if wait == 0:
                return True
            if not block or wait > MAX_WAIT_S:
                return False
            time.sleep(wait + 0.05)
    finally:
        db.close()

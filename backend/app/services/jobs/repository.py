from app.core.settings import settings
from app.models.job import JobOut

_OUT = "job_id::text, status, retries_left, resume_at, html_url, error_message"


def _out(row) -> JobOut:
    return JobOut(job_id=row[0], status=row[1], retries_left=row[2], resume_at=row[3],
                  html_url=row[4], error_message=row[5])


def insert_job(conn) -> JobOut:
    return _out(conn.execute(f"INSERT INTO jobs DEFAULT VALUES RETURNING {_OUT}").fetchone())


def set_article_key(conn, job_id: str, key: str) -> None:
    conn.execute("UPDATE jobs SET article_key=%s WHERE job_id=%s", (key, job_id))


def fail_queued(conn, job_id: str, reason: str) -> None:
    conn.execute("UPDATE jobs SET status='failed', error_message=%s, finished_at=now() "
                 "WHERE job_id=%s AND status='queued'", (reason, job_id))


def get_job(conn, job_id: str) -> JobOut | None:
    row = conn.execute(f"SELECT {_OUT} FROM jobs WHERE job_id=%s", (job_id,)).fetchone()
    return _out(row) if row else None


def claim_job(conn, worker_id: str) -> tuple[str, str] | None:
    return conn.execute(
        """UPDATE jobs SET status='running', worker_id=%(w)s, resume_at=NULL,
                           started_at=coalesce(started_at, now())
           WHERE job_id = (SELECT job_id FROM jobs
                           WHERE article_key IS NOT NULL
                             AND (status='queued' OR (status='paused' AND resume_at <= now()))
                           ORDER BY created_at LIMIT 1 FOR UPDATE SKIP LOCKED)
           RETURNING job_id::text, article_key""", {"w": worker_id}).fetchone()


def mark_done(conn, job_id: str, worker_id: str, html_url: str) -> bool:
    return conn.execute(
        "UPDATE jobs SET status='done', html_url=%s, finished_at=now(), worker_id=NULL "
        "WHERE job_id=%s AND status='running' AND worker_id=%s",
        (html_url, job_id, worker_id)).rowcount > 0


def mark_failed(conn, job_id: str, worker_id: str, reason: str) -> bool:
    return conn.execute(
        "UPDATE jobs SET status='failed', error_message=%s, finished_at=now(), worker_id=NULL "
        "WHERE job_id=%s AND status='running' AND worker_id=%s",
        (reason, job_id, worker_id)).rowcount > 0


def retry_later_or_fail(conn, job_id: str, worker_id: str, reason: str) -> str | None:
    row = conn.execute(
        """UPDATE jobs SET
             status        = CASE WHEN retries_left > 0 THEN 'paused' ELSE 'failed' END,
             resume_at     = CASE WHEN retries_left > 0 THEN now() + make_interval(secs => %(pause)s) END,
             error_message = CASE WHEN retries_left > 0 THEN NULL ELSE 'failed after retry: ' || %(reason)s END,
             finished_at   = CASE WHEN retries_left > 0 THEN NULL ELSE now() END,
             retries_left  = greatest(retries_left - 1, 0),
             worker_id     = NULL
           WHERE job_id=%(j)s AND status='running' AND worker_id=%(w)s
           RETURNING status""",
        {"pause": settings.retry_pause_seconds, "reason": reason, "j": job_id, "w": worker_id}).fetchone()
    return row[0] if row else None


def release(conn, job_id: str, worker_id: str) -> None:
    conn.execute("UPDATE jobs SET status='queued', worker_id=NULL "
                 "WHERE job_id=%s AND status='running' AND worker_id=%s", (job_id, worker_id))


def requeue_all_running(conn) -> int:
    return conn.execute("UPDATE jobs SET status='queued', worker_id=NULL "
                        "WHERE status='running'").rowcount


def fail_lost_uploads(conn, older_than_seconds: int) -> int:
    return conn.execute(
        "UPDATE jobs SET status='failed', error_message='article upload failed', finished_at=now() "
        "WHERE status='queued' AND article_key IS NULL "
        "AND created_at < now() - make_interval(secs => %s)", (older_than_seconds,)).rowcount


def next_resume_at(conn):
    return conn.execute("SELECT min(resume_at) FROM jobs WHERE status='paused'").fetchone()[0]


def set_archive_key(conn, job_id: str, key: str) -> None:
    conn.execute("UPDATE jobs SET archive_key=%s WHERE job_id=%s", (key, job_id))

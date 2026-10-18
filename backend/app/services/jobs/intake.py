import logging
import time

from app.core import db
from app.services import storage
from app.services.jobs import repository
from app.services.jobs.dispatcher import dispatcher

log = logging.getLogger("velma.intake")


def upload_article(job_id: str, text: str) -> None:
    key = f"inputs/{job_id}.txt"
    for attempt in range(3):
        try:
            storage.put_private(key, text.encode())
            with db.pool.connection() as conn:
                repository.set_article_key(conn, job_id, key)
            dispatcher.wake()
            return
        except Exception:
            log.exception("article upload failed (try %d)", attempt + 1)
            time.sleep(1)
    with db.pool.connection() as conn:
        repository.fail_queued(conn, job_id, "article upload failed")

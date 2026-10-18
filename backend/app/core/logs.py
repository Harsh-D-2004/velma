import logging
import sys
from pathlib import Path

from app.core.settings import settings

FORMAT = "%(asctime)s %(levelname)s job=%(job)s %(message)s"


class _Job(logging.Filter):
    def __init__(self, job_id: str):
        super().__init__()
        self.job_id = job_id

    def filter(self, record):
        record.job = getattr(record, "job", self.job_id)
        return True


def setup(job_id: str = "-") -> None:
    """Log the velma.* loggers to stdout and, for a job, to <scratch_dir>/logs/<job_id>.log (kept after the job)."""
    root = logging.getLogger("velma")
    if root.handlers:
        return
    root.setLevel(logging.INFO)
    handlers = [logging.StreamHandler(sys.stdout)]
    if job_id != "-":
        d = Path(settings.scratch_dir).resolve() / "logs"
        d.mkdir(parents=True, exist_ok=True)
        handlers.append(logging.FileHandler(d / f"{job_id}.log"))
    for h in handlers:
        h.setFormatter(logging.Formatter(FORMAT))
        h.addFilter(_Job(job_id))
        root.addHandler(h)

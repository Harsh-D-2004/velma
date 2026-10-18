from datetime import datetime
from enum import StrEnum

from pydantic import BaseModel


class JobStatus(StrEnum):
    queued = "queued"
    running = "running"
    paused = "paused"
    done = "done"
    failed = "failed"


class JobIn(BaseModel):
    article: str


class JobOut(BaseModel):
    job_id: str
    status: JobStatus
    retries_left: int
    resume_at: datetime | None = None
    html_url: str | None = None
    error_message: str | None = None

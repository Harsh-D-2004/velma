from uuid import UUID

from fastapi import BackgroundTasks, HTTPException

from app.core import db
from app.core.settings import settings
from app.models.job import JobOut
from app.services.jobs import intake, repository


def create_job(article: str, background_tasks: BackgroundTasks) -> JobOut:
    if not article.strip():
        raise HTTPException(422, "article is empty")
    if len(article) > settings.max_article_chars:
        raise HTTPException(422, f"article longer than {settings.max_article_chars} characters")
    with db.pool.connection() as conn:
        job = repository.insert_job(conn)
    background_tasks.add_task(intake.upload_article, job.job_id, article)
    return job


def read_job(job_id: str) -> JobOut:
    try:
        UUID(job_id)
    except ValueError:
        raise HTTPException(404, "job not found")
    with db.pool.connection() as conn:
        job = repository.get_job(conn, job_id)
    if job is None:
        raise HTTPException(404, "job not found")
    return job

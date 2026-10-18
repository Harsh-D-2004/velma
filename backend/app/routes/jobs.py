from fastapi import APIRouter, BackgroundTasks

from app.controllers import jobs
from app.models.job import JobIn, JobOut

router = APIRouter(prefix="/v1/jobs")


@router.post("", status_code=202, response_model=JobOut, response_model_exclude_none=True)
def post_job(body: JobIn, background_tasks: BackgroundTasks):
    return jobs.create_job(body.article, background_tasks)


@router.get("/{job_id}", response_model=JobOut, response_model_exclude_none=True)
def get_job(job_id: str):
    return jobs.read_job(job_id)

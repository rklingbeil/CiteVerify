"""Job status polling router."""

from fastapi import APIRouter, Depends, HTTPException

from backend.auth import verify_auth
from backend.jobs import get_job

router = APIRouter()


@router.get("/jobs/{job_id}")
def poll_job(job_id: str, username: str = Depends(verify_auth)):
    """Poll job status and progress."""
    job = get_job(job_id)
    if not job:
        raise HTTPException(404, "Job not found")
    return job.to_dict()

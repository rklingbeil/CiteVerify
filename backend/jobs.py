"""In-memory job manager with ThreadPoolExecutor."""

import logging
import os
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from threading import Lock
from typing import Optional

from backend.config import JOB_TTL_HOURS
from backend.pipeline import VerificationReport, run_verification

logger = logging.getLogger(__name__)

_executor = ThreadPoolExecutor(max_workers=2)
_lock = Lock()
_jobs: dict[str, "JobState"] = {}
_reports: dict[str, VerificationReport] = {}


@dataclass
class JobState:
    id: str
    filename: str
    file_path: str = ""
    status: str = "pending"  # pending | running | completed | failed
    progress: int = 0
    progress_message: str = ""
    report_id: Optional[str] = None
    error: Optional[str] = None
    created_at: float = field(default_factory=time.time)

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "filename": self.filename,
            "status": self.status,
            "progress": self.progress,
            "progress_message": self.progress_message,
            "report_id": self.report_id,
            "error": self.error,
        }


def submit_job(filename: str, file_path: str) -> str:
    """Submit a verification job and return the job ID."""
    job_id = str(uuid.uuid4())
    job = JobState(id=job_id, filename=filename, file_path=file_path)

    with _lock:
        _jobs[job_id] = job

    def _run() -> None:
        with _lock:
            job.status = "running"
        try:
            def _progress(step: int, total: int, message: str) -> None:
                job.progress = min(int(step / total * 100), 99)
                job.progress_message = message

            report = run_verification(file_path, filename, progress_callback=_progress)

            with _lock:
                _reports[report.id] = report
                job.report_id = report.id
                job.progress = 100
                job.progress_message = "Complete"
                job.status = "completed"

        except Exception as e:
            logger.exception(f"Job {job_id} failed: {e}")
            with _lock:
                job.error = str(e)
                job.status = "failed"

        finally:
            # Clean up uploaded temp file
            try:
                if file_path and os.path.exists(file_path):
                    os.unlink(file_path)
            except OSError:
                logger.warning(f"Failed to clean up temp file: {file_path}")

    _executor.submit(_run)
    return job_id


def get_job(job_id: str) -> Optional[JobState]:
    """Get current job state."""
    with _lock:
        return _jobs.get(job_id)


def get_report(report_id: str) -> Optional[VerificationReport]:
    """Get a completed verification report."""
    with _lock:
        return _reports.get(report_id)


def shutdown_executor() -> None:
    """Shut down the thread pool executor (call on app shutdown)."""
    _executor.shutdown(wait=False)
    logger.info("Job executor shut down")


def purge_old_jobs() -> None:
    """Remove jobs and reports older than JOB_TTL_HOURS."""
    cutoff = time.time() - (JOB_TTL_HOURS * 3600)
    with _lock:
        expired_jobs = [jid for jid, j in _jobs.items() if j.created_at < cutoff]
        for jid in expired_jobs:
            job = _jobs.pop(jid, None)
            if job and job.report_id:
                _reports.pop(job.report_id, None)
            # Clean up any leftover temp file
            if job and job.file_path:
                try:
                    os.unlink(job.file_path)
                except OSError:
                    pass
    if expired_jobs:
        logger.info(f"Purged {len(expired_jobs)} expired jobs")

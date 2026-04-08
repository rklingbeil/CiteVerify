"""Tests for the in-memory job manager."""

import time
from unittest.mock import patch

from backend.jobs import JobState, _jobs, _lock, _reports, get_job, get_report, purge_old_jobs, submit_job


def _clear_state():
    with _lock:
        _jobs.clear()
        _reports.clear()


class TestJobState:
    def test_to_dict(self):
        job = JobState(id="j1", filename="test.pdf", status="running", progress=50)
        d = job.to_dict()
        assert d["id"] == "j1"
        assert d["status"] == "running"
        assert d["progress"] == 50
        assert d["report_id"] is None
        assert d["error"] is None


class TestSubmitJob:
    @patch("backend.jobs.run_verification")
    def test_creates_job_and_returns_id(self, mock_run):
        _clear_state()
        from backend.pipeline import VerificationReport
        mock_run.return_value = VerificationReport(
            id="r1", filename="test.pdf", document_text="text",
            total_citations=0, verified=0, warnings=0, errors=0, unverifiable=0,
            created_at="2024-01-01",
        )

        job_id = submit_job("test.pdf", "/tmp/test.pdf")
        assert isinstance(job_id, str)
        assert len(job_id) == 36  # UUID

        # Wait for background job
        time.sleep(0.5)

        job = get_job(job_id)
        assert job is not None
        assert job.status == "completed"
        assert job.report_id == "r1"

        report = get_report("r1")
        assert report is not None

    @patch("backend.jobs.run_verification")
    def test_failed_job(self, mock_run):
        _clear_state()
        mock_run.side_effect = RuntimeError("boom")

        job_id = submit_job("bad.pdf", "/tmp/bad.pdf")
        time.sleep(0.5)

        job = get_job(job_id)
        assert job is not None
        assert job.status == "failed"
        assert "boom" in (job.error or "")


class TestGetJob:
    def test_returns_none_for_unknown(self):
        _clear_state()
        assert get_job("nonexistent") is None


class TestPurgeOldJobs:
    def test_purges_expired(self):
        _clear_state()
        old_job = JobState(id="old", filename="old.pdf", created_at=time.time() - 100_000)
        with _lock:
            _jobs["old"] = old_job
        purge_old_jobs()
        assert get_job("old") is None

    def test_keeps_recent(self):
        _clear_state()
        new_job = JobState(id="new", filename="new.pdf", created_at=time.time())
        with _lock:
            _jobs["new"] = new_job
        purge_old_jobs()
        assert get_job("new") is not None

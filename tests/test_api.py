"""Tests for API endpoints."""

import io
from unittest.mock import patch

import pytest


class TestHealth:
    def test_health(self, client):
        resp = client.get("/api/health")
        assert resp.status_code == 200
        assert resp.json() == {"status": "ok"}


class TestUpload:
    def test_upload_pdf(self, client):
        with patch("backend.routers.upload.submit_job", return_value="job-123"):
            resp = client.post(
                "/api/upload",
                files={"file": ("test.pdf", io.BytesIO(b"fake pdf content"), "application/pdf")},
            )
        assert resp.status_code == 200
        data = resp.json()
        assert data["job_id"] == "job-123"
        assert data["filename"] == "test.pdf"

    def test_upload_docx(self, client):
        with patch("backend.routers.upload.submit_job", return_value="job-456"):
            resp = client.post(
                "/api/upload",
                files={"file": ("brief.docx", io.BytesIO(b"fake docx"), "application/vnd.openxmlformats")},
            )
        assert resp.status_code == 200
        assert resp.json()["job_id"] == "job-456"

    def test_upload_rejects_unsupported_type(self, client):
        resp = client.post(
            "/api/upload",
            files={"file": ("notes.txt", io.BytesIO(b"text"), "text/plain")},
        )
        assert resp.status_code == 400
        assert "Unsupported" in resp.json()["detail"]

    def test_upload_rejects_large_file(self, client):
        big = b"x" * (51 * 1024 * 1024)  # 51 MB
        resp = client.post(
            "/api/upload",
            files={"file": ("huge.pdf", io.BytesIO(big), "application/pdf")},
        )
        assert resp.status_code == 413
        assert "too large" in resp.json()["detail"]

    def test_upload_rejects_no_filename(self, client):
        # FastAPI may return 400 or 422 depending on how the empty filename is handled
        resp = client.post(
            "/api/upload",
            files={"file": ("", io.BytesIO(b"data"), "application/pdf")},
        )
        assert resp.status_code in (400, 422)


class TestJobs:
    def test_poll_unknown_job(self, client):
        resp = client.get("/api/jobs/nonexistent-id")
        assert resp.status_code == 404

    def test_poll_existing_job(self, client):
        from backend.jobs import JobState, _jobs, _lock
        job = JobState(id="j1", filename="test.pdf", status="running", progress=50, progress_message="Looking up...")
        with _lock:
            _jobs["j1"] = job

        resp = client.get("/api/jobs/j1")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "running"
        assert data["progress"] == 50

        # cleanup
        with _lock:
            _jobs.pop("j1", None)


class TestReports:
    def test_fetch_unknown_report(self, client):
        resp = client.get("/api/reports/nonexistent-id")
        assert resp.status_code == 404

    def test_fetch_existing_report(self, client):
        from backend.jobs import _lock, _reports
        from backend.pipeline import VerificationReport

        report = VerificationReport(
            id="r1", filename="test.pdf", document_text="text",
            total_citations=2, verified=1, warnings=1, errors=0, unverifiable=0,
            citations=[], created_at="2024-01-01",
        )
        with _lock:
            _reports["r1"] = report

        resp = client.get("/api/reports/r1")
        assert resp.status_code == 200
        data = resp.json()
        assert data["total_citations"] == 2
        assert data["verified"] == 1

        # cleanup
        with _lock:
            _reports.pop("r1", None)

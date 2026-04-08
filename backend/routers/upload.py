"""Upload router — file upload and job creation."""

import os
import uuid

from fastapi import APIRouter, HTTPException, UploadFile

from backend.config import MAX_UPLOAD_SIZE_MB, UPLOAD_DIR
from backend.jobs import submit_job

router = APIRouter()


@router.post("/upload")
async def upload_document(file: UploadFile):
    """Upload a legal document for citation verification."""
    if not file.filename:
        raise HTTPException(400, "No filename provided")

    ext = os.path.splitext(file.filename)[1].lower()
    if ext not in (".pdf", ".docx"):
        raise HTTPException(400, f"Unsupported file type: {ext}. Upload PDF or DOCX.")

    # Read file with size check
    content = await file.read()
    max_bytes = MAX_UPLOAD_SIZE_MB * 1024 * 1024
    if len(content) > max_bytes:
        raise HTTPException(400, f"File too large. Maximum: {MAX_UPLOAD_SIZE_MB} MB")

    # Save to temp directory
    os.makedirs(UPLOAD_DIR, exist_ok=True)
    safe_name = f"{uuid.uuid4()}{ext}"
    file_path = os.path.join(UPLOAD_DIR, safe_name)
    with open(file_path, "wb") as f:
        f.write(content)

    # Submit verification job
    job_id = submit_job(file.filename, file_path)

    return {"job_id": job_id, "status": "pending", "filename": file.filename}

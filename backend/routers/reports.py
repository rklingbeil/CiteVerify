"""Report retrieval router."""

import re

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import Response

from backend.auth import verify_auth
from backend.jobs import get_report
from backend.pdf_export import generate_pdf

router = APIRouter()


@router.get("/reports/{report_id}")
def fetch_report(report_id: str, username: str = Depends(verify_auth)):
    """Get a completed verification report."""
    report = get_report(report_id)
    if not report:
        raise HTTPException(404, "Report not found")
    return report.to_dict()


@router.get("/reports/{report_id}/pdf")
def export_pdf(report_id: str, username: str = Depends(verify_auth)):
    """Download the verification report as a PDF."""
    report = get_report(report_id)
    if not report:
        raise HTTPException(404, "Report not found")

    pdf_bytes = generate_pdf(report)
    # Sanitize filename to prevent header injection (strip newlines, quotes, non-ASCII)
    base_name = report.filename.rsplit(".", 1)[0]
    safe_name = re.sub(r'[^\w\-.]', '_', base_name)[:100] + "_citeverify.pdf"

    return Response(
        content=pdf_bytes,
        media_type="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="{safe_name}"'},
    )

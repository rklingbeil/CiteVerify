"""Report retrieval router."""

import re

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import Response

from backend.auth import verify_auth
from backend.excel_export import generate_excel
from backend.jobs import get_report
from backend.pdf_export import generate_pdf

router = APIRouter()


def _safe_filename(filename: str, ext: str) -> str:
    """Sanitize filename for Content-Disposition header."""
    base_name = filename.rsplit(".", 1)[0]
    return re.sub(r'[^\w\-.]', '_', base_name)[:100] + f"_citeverify.{ext}"


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
    safe_name = _safe_filename(report.filename, "pdf")

    return Response(
        content=pdf_bytes,
        media_type="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="{safe_name}"'},
    )


@router.get("/reports/{report_id}/excel")
def export_excel(report_id: str, username: str = Depends(verify_auth)):
    """Download the verification report as an Excel spreadsheet."""
    report = get_report(report_id)
    if not report:
        raise HTTPException(404, "Report not found")

    excel_bytes = generate_excel(report)
    safe_name = _safe_filename(report.filename, "xlsx")

    return Response(
        content=excel_bytes,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f'attachment; filename="{safe_name}"'},
    )

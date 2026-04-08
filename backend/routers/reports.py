"""Report retrieval router."""

from fastapi import APIRouter, HTTPException
from fastapi.responses import Response

from backend.jobs import get_report
from backend.pdf_export import generate_pdf

router = APIRouter()


@router.get("/reports/{report_id}")
def fetch_report(report_id: str):
    """Get a completed verification report."""
    report = get_report(report_id)
    if not report:
        raise HTTPException(404, "Report not found")
    return report.to_dict()


@router.get("/reports/{report_id}/pdf")
def export_pdf(report_id: str):
    """Download the verification report as a PDF."""
    report = get_report(report_id)
    if not report:
        raise HTTPException(404, "Report not found")

    pdf_bytes = generate_pdf(report)
    safe_name = report.filename.rsplit(".", 1)[0] + "_citeverify.pdf"

    return Response(
        content=pdf_bytes,
        media_type="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="{safe_name}"'},
    )

"""Report retrieval router."""

from fastapi import APIRouter, HTTPException

from backend.jobs import get_report

router = APIRouter()


@router.get("/reports/{report_id}")
def fetch_report(report_id: str):
    """Get a completed verification report."""
    report = get_report(report_id)
    if not report:
        raise HTTPException(404, "Report not found")
    return report.to_dict()

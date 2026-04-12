"""PDF report generation using ReportLab."""

import io
from datetime import datetime, timezone

from reportlab.lib import colors
from reportlab.lib.pagesizes import letter
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import inch
from reportlab.platypus import (
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)

from backend.pipeline import CitationReport, VerificationReport

# ─── Status Colors ───────────────────────────────────────────────────────

STATUS_COLORS = {
    "verified": colors.HexColor("#16a34a"),
    "warning": colors.HexColor("#ca8a04"),
    "error": colors.HexColor("#dc2626"),
    "unverifiable": colors.HexColor("#6b7280"),
}

STATUS_BG = {
    "verified": colors.HexColor("#f0fdf4"),
    "warning": colors.HexColor("#fefce8"),
    "error": colors.HexColor("#fef2f2"),
    "unverifiable": colors.HexColor("#f9fafb"),
}


def _build_styles():
    """Create paragraph styles for the PDF."""
    base = getSampleStyleSheet()
    styles = {
        "title": ParagraphStyle(
            "Title", parent=base["Title"],
            fontSize=20, spaceAfter=4, textColor=colors.HexColor("#1f2937"),
        ),
        "subtitle": ParagraphStyle(
            "Subtitle", parent=base["Normal"],
            fontSize=11, textColor=colors.HexColor("#6b7280"), spaceAfter=16,
        ),
        "heading": ParagraphStyle(
            "Heading", parent=base["Heading2"],
            fontSize=13, spaceBefore=16, spaceAfter=6,
            textColor=colors.HexColor("#1f2937"),
        ),
        "label": ParagraphStyle(
            "Label", parent=base["Normal"],
            fontSize=8, textColor=colors.HexColor("#6b7280"),
            spaceAfter=2,
        ),
        "body": ParagraphStyle(
            "Body", parent=base["Normal"],
            fontSize=9, leading=13, textColor=colors.HexColor("#374151"),
        ),
        "quote": ParagraphStyle(
            "Quote", parent=base["Normal"],
            fontSize=9, leading=13, leftIndent=16,
            textColor=colors.HexColor("#4b5563"), fontName="Helvetica-Oblique",
        ),
        "status": ParagraphStyle(
            "Status", parent=base["Normal"],
            fontSize=10, fontName="Helvetica-Bold",
        ),
        "small": ParagraphStyle(
            "Small", parent=base["Normal"],
            fontSize=8, textColor=colors.HexColor("#9ca3af"),
        ),
    }
    return styles


def _status_text(status: str, styles: dict) -> Paragraph:
    """Create a colored status paragraph."""
    color = STATUS_COLORS.get(status, colors.gray)
    style = ParagraphStyle("s", parent=styles["status"], textColor=color)
    return Paragraph(status.upper(), style)


def _summary_table(report: VerificationReport, styles: dict) -> Table:
    """Build the summary statistics table."""
    data = [[
        Paragraph(f"<b>{report.total_citations}</b> Total", styles["body"]),
        Paragraph(f"<b>{report.verified}</b> Verified", styles["body"]),
        Paragraph(f"<b>{report.warnings}</b> Warnings", styles["body"]),
        Paragraph(f"<b>{report.errors}</b> Errors", styles["body"]),
        Paragraph(f"<b>{report.unverifiable}</b> Unverifiable", styles["body"]),
    ]]
    t = Table(data, colWidths=[1.1 * inch] * 5)
    t.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (0, 0), colors.HexColor("#f3f4f6")),
        ("BACKGROUND", (1, 0), (1, 0), STATUS_BG["verified"]),
        ("BACKGROUND", (2, 0), (2, 0), STATUS_BG["warning"]),
        ("BACKGROUND", (3, 0), (3, 0), STATUS_BG["error"]),
        ("BACKGROUND", (4, 0), (4, 0), STATUS_BG["unverifiable"]),
        ("BOX", (0, 0), (-1, -1), 0.5, colors.HexColor("#e5e7eb")),
        ("INNERGRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#e5e7eb")),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("TOPPADDING", (0, 0), (-1, -1), 6),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
        ("LEFTPADDING", (0, 0), (-1, -1), 8),
    ]))
    return t


def _citation_section(idx: int, cr: CitationReport, styles: dict) -> list:
    """Build flowable elements for a single citation."""
    elements: list = []
    status = cr.verification.status
    bg = STATUS_BG.get(status, colors.white)

    # Header row: number + case name + status
    case_label = cr.extraction.case_name or cr.extraction.citation_text
    header_data = [[
        Paragraph(f"<b>#{idx + 1}</b>", styles["body"]),
        Paragraph(f"<b>{_esc(case_label)}</b>", styles["body"]),
        _status_text(status, styles),
    ]]
    ht = Table(header_data, colWidths=[0.4 * inch, 4.0 * inch, 1.2 * inch])
    ht.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, -1), bg),
        ("BOX", (0, 0), (-1, -1), 0.5, colors.HexColor("#d1d5db")),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("TOPPADDING", (0, 0), (-1, -1), 6),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
        ("LEFTPADDING", (0, 0), (-1, -1), 8),
    ]))
    elements.append(ht)
    elements.append(Spacer(1, 4))

    # Citation text
    elements.append(Paragraph(f"<b>Citation:</b> {_esc(cr.extraction.citation_text)}", styles["body"]))

    # Source info
    if cr.lookup.found:
        source_parts = []
        if cr.lookup.court:
            source_parts.append(f"Court: {_esc(cr.lookup.court)}")
        if cr.lookup.date_filed:
            source_parts.append(f"Filed: {_esc(cr.lookup.date_filed)}")
        source_parts.append(f"Source: {cr.lookup.source}")
        elements.append(Paragraph(" | ".join(source_parts), styles["small"]))
    else:
        elements.append(Paragraph("Source not found in CourtListener or GovInfo", styles["small"]))

    # Quote verification
    if cr.extraction.quoted_text:
        elements.append(Spacer(1, 4))
        accuracy = cr.verification.quote_accuracy or "—"
        elements.append(Paragraph(f"<b>Quote accuracy:</b> {accuracy}", styles["body"]))
        elements.append(Paragraph(f"\"{_esc(cr.extraction.quoted_text)}\"", styles["quote"]))
        if cr.verification.quote_diff:
            elements.append(Paragraph(f"<i>Diff: {_esc(cr.verification.quote_diff)}</i>", styles["small"]))

    # Characterization verification
    if cr.extraction.characterization:
        elements.append(Spacer(1, 4))
        accuracy = cr.verification.characterization_accuracy or "—"
        elements.append(Paragraph(f"<b>Characterization accuracy:</b> {accuracy}", styles["body"]))
        elements.append(Paragraph(_esc(cr.extraction.characterization), styles["quote"]))
        if cr.verification.characterization_explanation:
            elements.append(Paragraph(
                f"<i>{_esc(cr.verification.characterization_explanation)}</i>", styles["small"],
            ))

    # Confidence + reasoning
    conf = cr.verification.confidence
    if conf > 0:
        elements.append(Paragraph(f"Confidence: {conf:.0%}", styles["small"]))
    if cr.verification.reasoning:
        # Truncate very long reasoning for PDF readability
        reasoning = cr.verification.reasoning[:500]
        if len(cr.verification.reasoning) > 500:
            reasoning += "..."
        elements.append(Paragraph(f"<i>{_esc(reasoning)}</i>", styles["small"]))

    elements.append(Spacer(1, 12))
    return elements


def _esc(text: str) -> str:
    """Escape XML special characters for ReportLab paragraphs."""
    return (
        text.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
    )


def generate_pdf(report: VerificationReport) -> bytes:
    """Generate a PDF report and return the bytes."""
    buf = io.BytesIO()
    doc = SimpleDocTemplate(
        buf,
        pagesize=letter,
        topMargin=0.6 * inch,
        bottomMargin=0.6 * inch,
        leftMargin=0.75 * inch,
        rightMargin=0.75 * inch,
    )

    styles = _build_styles()
    elements: list = []

    # Title
    elements.append(Paragraph("CiteVerify — Citation Verification Report", styles["title"]))
    elements.append(Paragraph(
        f"Document: {_esc(report.filename)} | "
        f"Generated: {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}",
        styles["subtitle"],
    ))

    # Summary
    elements.append(_summary_table(report, styles))
    elements.append(Spacer(1, 16))

    # Extraction warnings (scanned pages, etc.)
    if report.extraction_warnings:
        elements.append(Paragraph("Document Warnings", styles["heading"]))
        for w in report.extraction_warnings:
            elements.append(Paragraph(f"• {_esc(w)}", styles["small"]))
        elements.append(Spacer(1, 8))

    # Citations
    if report.citations:
        elements.append(Paragraph("Citation Details", styles["heading"]))
        for i, cr in enumerate(report.citations):
            elements.extend(_citation_section(i, cr, styles))
    else:
        elements.append(Paragraph("No citations found in the document.", styles["body"]))

    # Footer
    elements.append(Spacer(1, 24))
    elements.append(Paragraph(
        "Generated by CiteVerify — AI-powered legal citation verification",
        styles["small"],
    ))

    doc.build(elements)
    return buf.getvalue()

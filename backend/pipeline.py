"""Verification pipeline — orchestrates extraction, lookup, and verification."""

import logging
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Callable, Optional

from backend.citation_extractor import ExtractedCitation, extract_citations
from backend.extractor import extract_document
from backend.source_lookup import LookupResult, lookup_citation
from backend.verifier import VerificationResult, make_unverifiable_result, verify_citation

logger = logging.getLogger(__name__)


@dataclass
class CitationReport:
    """Full report for a single citation."""
    extraction: ExtractedCitation
    lookup: LookupResult
    verification: VerificationResult


@dataclass
class VerificationReport:
    """Complete verification report for a document."""
    id: str
    filename: str
    document_text: str
    total_citations: int
    verified: int
    warnings: int
    errors: int
    unverifiable: int
    citations: list[CitationReport] = field(default_factory=list)
    created_at: str = ""

    def to_dict(self) -> dict:
        """Serialize for JSON response."""
        return {
            "id": self.id,
            "filename": self.filename,
            "document_text": self.document_text,
            "total_citations": self.total_citations,
            "verified": self.verified,
            "warnings": self.warnings,
            "errors": self.errors,
            "unverifiable": self.unverifiable,
            "citations": [
                {
                    "extraction": {
                        "citation_text": c.extraction.citation_text,
                        "case_name": c.extraction.case_name,
                        "full_reference": c.extraction.full_reference,
                        "quoted_text": c.extraction.quoted_text,
                        "characterization": c.extraction.characterization,
                        "context": c.extraction.context,
                        "position_start": c.extraction.position_start,
                        "position_end": c.extraction.position_end,
                    },
                    "lookup": {
                        "found": c.lookup.found,
                        "status": c.lookup.status,
                        "case_name": c.lookup.case_name,
                        "court": c.lookup.court,
                        "date_filed": c.lookup.date_filed,
                        "url": c.lookup.url,
                        "source": c.lookup.source,
                        "has_opinion_text": c.lookup.opinion_text is not None,
                    },
                    "verification": {
                        "status": c.verification.status,
                        "citation_exists": c.verification.citation_exists,
                        "citation_format_correct": c.verification.citation_format_correct,
                        "quote_accuracy": c.verification.quote_accuracy,
                        "quote_diff": c.verification.quote_diff,
                        "actual_quote": c.verification.actual_quote,
                        "characterization_accuracy": c.verification.characterization_accuracy,
                        "characterization_explanation": c.verification.characterization_explanation,
                        "confidence": c.verification.confidence,
                    },
                }
                for c in self.citations
            ],
            "created_at": self.created_at,
        }


ProgressCallback = Callable[[int, int, str], None]


def run_verification(
    file_path: str,
    filename: str,
    progress_callback: Optional[ProgressCallback] = None,
) -> VerificationReport:
    """Run the full verification pipeline on a document."""
    report_id = str(uuid.uuid4())

    def progress(step: int, total: int, message: str) -> None:
        if progress_callback:
            progress_callback(step, total, message)

    # Step 1: Extract text
    progress(5, 100, "Extracting text from document...")
    extraction = extract_document(file_path)
    logger.info(f"Extracted {len(extraction.text)} chars from {filename}")

    # Step 2: Extract citations via AI
    progress(10, 100, "Identifying citations, quotes, and characterizations...")
    citations = extract_citations(extraction.text)
    logger.info(f"Found {len(citations)} citations in {filename}")

    if not citations:
        return VerificationReport(
            id=report_id,
            filename=filename,
            document_text=extraction.text,
            total_citations=0,
            verified=0,
            warnings=0,
            errors=0,
            unverifiable=0,
            created_at=datetime.now(timezone.utc).isoformat(),
        )

    # Step 3: Look up each citation
    citation_reports: list[CitationReport] = []
    lookup_progress_start = 20
    lookup_progress_end = 70
    lookup_range = lookup_progress_end - lookup_progress_start

    for i, citation in enumerate(citations):
        pct = lookup_progress_start + int((i / len(citations)) * lookup_range)
        progress(pct, 100, f"Looking up: {citation.case_name or citation.citation_text}...")

        lookup_result = lookup_citation(citation.citation_text)
        citation_reports.append(CitationReport(
            extraction=citation,
            lookup=lookup_result,
            verification=make_unverifiable_result(),  # placeholder
        ))

    # Step 4: Verify citations that have source text
    verify_progress_start = 70
    verify_progress_end = 95
    verify_range = verify_progress_end - verify_progress_start
    verifiable = [cr for cr in citation_reports if cr.lookup.opinion_text]

    for i, cr in enumerate(verifiable):
        pct = verify_progress_start + int((i / max(len(verifiable), 1)) * verify_range)
        progress(pct, 100, f"Verifying: {cr.extraction.case_name or cr.extraction.citation_text}...")

        # Only verify if there's a quote or characterization to check
        if cr.extraction.quoted_text or cr.extraction.characterization:
            cr.verification = verify_citation(cr.extraction, cr.lookup.opinion_text)
        else:
            cr.verification = VerificationResult(
                status="verified",
                citation_exists=True,
                citation_format_correct=True,
                quote_accuracy=None,
                quote_diff=None,
                actual_quote=None,
                characterization_accuracy=None,
                characterization_explanation="No quote or characterization to verify",
                confidence=1.0,
            )

    # Step 5: Assemble report
    progress(98, 100, "Assembling report...")

    verified_count = sum(1 for cr in citation_reports if cr.verification.status == "verified")
    warning_count = sum(1 for cr in citation_reports if cr.verification.status == "warning")
    error_count = sum(1 for cr in citation_reports if cr.verification.status == "error")
    unverifiable_count = sum(1 for cr in citation_reports if cr.verification.status == "unverifiable")

    report = VerificationReport(
        id=report_id,
        filename=filename,
        document_text=extraction.text,
        total_citations=len(citation_reports),
        verified=verified_count,
        warnings=warning_count,
        errors=error_count,
        unverifiable=unverifiable_count,
        citations=citation_reports,
        created_at=datetime.now(timezone.utc).isoformat(),
    )

    progress(100, 100, "Verification complete")
    logger.info(
        f"Report {report_id}: {len(citation_reports)} citations — "
        f"{verified_count} verified, {warning_count} warnings, "
        f"{error_count} errors, {unverifiable_count} unverifiable"
    )

    return report

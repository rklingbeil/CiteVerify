"""Tests for PDF export."""

from backend.citation_extractor import ExtractedCitation
from backend.pdf_export import generate_pdf
from backend.pipeline import CitationReport, VerificationReport
from backend.source_lookup import LookupResult
from backend.verifier import VerificationResult


def _make_report(**overrides) -> VerificationReport:
    defaults = {
        "id": "r1",
        "filename": "test_brief.pdf",
        "document_text": "Some legal document text.",
        "total_citations": 0,
        "verified": 0,
        "warnings": 0,
        "errors": 0,
        "unverifiable": 0,
        "citations": [],
        "created_at": "2024-01-01T00:00:00",
    }
    defaults.update(overrides)
    return VerificationReport(**defaults)


def _make_citation_report(status="verified", quote=None, characterization=None):
    return CitationReport(
        extraction=ExtractedCitation(
            citation_text="325 Or App 648",
            case_name="Smith v. Jones",
            full_reference="Smith v. Jones, 325 Or App 648 (2023)",
            quoted_text=quote,
            characterization=characterization,
            context="In Smith v. Jones, the court held...",
            position_start=100,
            position_end=150,
        ),
        lookup=LookupResult(
            found=True, status="found", case_name="Smith v. Jones",
            court="Oregon Court of Appeals", date_filed="2023-01-15",
            url="https://www.courtlistener.com/opinion/12345/",
            source="courtlistener", opinion_text="Full opinion text...",
        ),
        verification=VerificationResult(
            status=status, citation_exists=True, citation_format_correct=True,
            quote_accuracy="exact" if quote else None,
            quote_diff=None,
            actual_quote=quote,
            characterization_accuracy="accurate" if characterization else None,
            characterization_explanation="Matches the holding" if characterization else None,
            confidence=0.95,
        ),
    )


class TestGeneratePdf:
    def test_empty_report(self):
        report = _make_report()
        pdf = generate_pdf(report)
        assert isinstance(pdf, bytes)
        assert len(pdf) > 100
        assert pdf[:5] == b"%PDF-"

    def test_report_with_citations(self):
        report = _make_report(
            total_citations=3,
            verified=1,
            warnings=1,
            errors=1,
            citations=[
                _make_citation_report("verified", quote="The court held that..."),
                _make_citation_report("warning", characterization="establishing negligence"),
                _make_citation_report("error", quote="Misquoted text", characterization="wrong holding"),
            ],
        )
        pdf = generate_pdf(report)
        assert pdf[:5] == b"%PDF-"
        assert len(pdf) > 500

    def test_unverifiable_citation(self):
        cr = _make_citation_report("unverifiable")
        cr.lookup = LookupResult(found=False, status="not_found")
        cr.verification = VerificationResult(
            status="unverifiable", citation_exists=False, citation_format_correct=True,
            quote_accuracy=None, quote_diff=None, actual_quote=None,
            characterization_accuracy=None, characterization_explanation="Source not available",
            confidence=0.0,
        )
        report = _make_report(total_citations=1, unverifiable=1, citations=[cr])
        pdf = generate_pdf(report)
        assert pdf[:5] == b"%PDF-"

    def test_special_characters_escaped(self):
        """XML special chars in citation text don't break PDF generation."""
        cr = _make_citation_report("verified")
        cr.extraction.case_name = "A < B & C > D"
        cr.extraction.quoted_text = 'He said "use <brackets>"'
        report = _make_report(total_citations=1, verified=1, citations=[cr])
        pdf = generate_pdf(report)
        assert pdf[:5] == b"%PDF-"


class TestPdfEndpoint:
    def test_download_pdf(self, client):
        from backend.jobs import _lock, _reports

        report = _make_report(total_citations=1, verified=1, citations=[
            _make_citation_report("verified", quote="a quote"),
        ])
        with _lock:
            _reports["r1"] = report

        resp = client.get("/api/reports/r1/pdf")
        assert resp.status_code == 200
        assert resp.headers["content-type"] == "application/pdf"
        assert "citeverify.pdf" in resp.headers["content-disposition"]
        assert resp.content[:5] == b"%PDF-"

        with _lock:
            _reports.pop("r1", None)

    def test_pdf_not_found(self, client):
        resp = client.get("/api/reports/nonexistent/pdf")
        assert resp.status_code == 404

"""Tests for the verification pipeline orchestrator."""

from unittest.mock import MagicMock, patch

from backend.citation_extractor import ExtractedCitation
from backend.extractor import ExtractionResult
from backend.pipeline import VerificationReport, run_verification
from backend.source_lookup import LookupResult
from backend.verifier import VerificationResult


def _make_citation(**overrides) -> ExtractedCitation:
    defaults = {
        "citation_text": "325 Or App 648",
        "case_name": "Smith v. Jones",
        "full_reference": "ref",
        "quoted_text": "a quote",
        "characterization": None,
        "context": "ctx",
        "position_start": 0,
        "position_end": 50,
    }
    defaults.update(overrides)
    return ExtractedCitation(**defaults)


class TestRunVerification:
    @patch("backend.pipeline.verify_citation")
    @patch("backend.pipeline.lookup_citation")
    @patch("backend.pipeline.extract_citations")
    @patch("backend.pipeline.extract_document")
    def test_full_pipeline(self, mock_extract_doc, mock_extract_cit, mock_lookup, mock_verify):
        mock_extract_doc.return_value = ExtractionResult(text="doc text", page_count=1)
        mock_extract_cit.return_value = [_make_citation()]
        mock_lookup.return_value = LookupResult(
            found=True, status="found", case_name="Smith v. Jones",
            opinion_text="opinion text", source="courtlistener",
        )
        mock_verify.return_value = VerificationResult(
            status="verified", citation_exists=True, citation_format_correct=True,
            quote_accuracy="exact", quote_diff=None, actual_quote="a quote",
            characterization_accuracy=None, characterization_explanation=None,
            confidence=0.95,
        )

        report = run_verification("/tmp/test.pdf", "test.pdf")

        assert isinstance(report, VerificationReport)
        assert report.total_citations == 1
        assert report.verified == 1
        assert report.warnings == 0
        assert report.errors == 0
        assert report.filename == "test.pdf"
        assert len(report.citations) == 1

    @patch("backend.pipeline.extract_citations")
    @patch("backend.pipeline.extract_document")
    def test_no_citations_found(self, mock_extract_doc, mock_extract_cit):
        mock_extract_doc.return_value = ExtractionResult(text="no citations here")
        mock_extract_cit.return_value = []

        report = run_verification("/tmp/test.pdf", "test.pdf")
        assert report.total_citations == 0
        assert report.citations == []

    @patch("backend.pipeline.verify_citation")
    @patch("backend.pipeline.lookup_citation")
    @patch("backend.pipeline.extract_citations")
    @patch("backend.pipeline.extract_document")
    def test_unverifiable_when_no_opinion_text(self, mock_extract_doc, mock_extract_cit, mock_lookup, mock_verify):
        mock_extract_doc.return_value = ExtractionResult(text="doc text")
        mock_extract_cit.return_value = [_make_citation()]
        mock_lookup.return_value = LookupResult(
            found=True, status="found", opinion_text=None, source="courtlistener",
        )

        report = run_verification("/tmp/test.pdf", "test.pdf")
        assert report.unverifiable == 1
        mock_verify.assert_not_called()

    @patch("backend.pipeline.verify_citation")
    @patch("backend.pipeline.lookup_citation")
    @patch("backend.pipeline.extract_citations")
    @patch("backend.pipeline.extract_document")
    def test_no_quote_or_characterization_auto_verified(
        self, mock_extract_doc, mock_extract_cit, mock_lookup, mock_verify
    ):
        """Citations with opinion text but no quote/characterization are auto-verified."""
        mock_extract_doc.return_value = ExtractionResult(text="doc text")
        mock_extract_cit.return_value = [_make_citation(quoted_text=None, characterization=None)]
        mock_lookup.return_value = LookupResult(
            found=True, status="found", opinion_text="opinion", source="courtlistener",
        )

        report = run_verification("/tmp/test.pdf", "test.pdf")
        assert report.verified == 1
        mock_verify.assert_not_called()

    @patch("backend.pipeline.verify_citation")
    @patch("backend.pipeline.lookup_citation")
    @patch("backend.pipeline.extract_citations")
    @patch("backend.pipeline.extract_document")
    def test_progress_callback(self, mock_extract_doc, mock_extract_cit, mock_lookup, mock_verify):
        mock_extract_doc.return_value = ExtractionResult(text="doc text")
        mock_extract_cit.return_value = [_make_citation()]
        mock_lookup.return_value = LookupResult(
            found=True, status="found", opinion_text="opinion", source="courtlistener",
        )
        mock_verify.return_value = VerificationResult(
            status="verified", citation_exists=True, citation_format_correct=True,
            quote_accuracy="exact", quote_diff=None, actual_quote="a quote",
            characterization_accuracy=None, characterization_explanation=None,
            confidence=0.95,
        )

        progress_calls: list[tuple[int, int, str]] = []

        def callback(step: int, total: int, msg: str) -> None:
            progress_calls.append((step, total, msg))

        run_verification("/tmp/test.pdf", "test.pdf", progress_callback=callback)
        assert len(progress_calls) >= 3  # at least: extract text, extract citations, complete

    def test_report_to_dict(self):
        """VerificationReport.to_dict() produces valid serializable dict."""
        report = VerificationReport(
            id="test-id",
            filename="test.pdf",
            document_text="text",
            total_citations=0,
            verified=0,
            warnings=0,
            errors=0,
            unverifiable=0,
            citations=[],
            created_at="2024-01-01T00:00:00",
        )
        d = report.to_dict()
        assert d["id"] == "test-id"
        assert d["citations"] == []
        assert isinstance(d, dict)

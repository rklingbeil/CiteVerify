"""Tests for quote and characterization verification."""

from unittest.mock import patch

from backend.citation_extractor import ExtractedCitation
from backend.verifier import VerificationResult, make_unverifiable_result, verify_citation


def _make_citation(**overrides) -> ExtractedCitation:
    defaults = {
        "citation_text": "325 Or App 648",
        "case_name": "Smith v. Jones",
        "full_reference": "Smith v. Jones, 325 Or App 648 (2023)",
        "quoted_text": "The court held that negligence requires...",
        "characterization": "establishing the standard for negligence",
        "context": "In Smith v. Jones, the court...",
        "position_start": 100,
        "position_end": 150,
    }
    defaults.update(overrides)
    return ExtractedCitation(**defaults)


class TestVerifyCitation:
    @patch("backend.verifier.call_ai_json")
    def test_verified_result(self, mock_ai):
        mock_ai.return_value = {
            "citation_format_correct": True,
            "quote_accuracy": "exact",
            "quote_diff": None,
            "actual_quote": "The court held that negligence requires...",
            "characterization_accuracy": "accurate",
            "characterization_explanation": "Matches the holding",
            "confidence": 0.95,
            "overall_status": "verified",
        }

        result = verify_citation(_make_citation(), "Full opinion text here...")
        assert isinstance(result, VerificationResult)
        assert result.status == "verified"
        assert result.quote_accuracy == "exact"
        # 0.95 initial + 0.05 review confirmation boost, clamped to 1.0
        assert result.confidence == 1.0

    @patch("backend.verifier.call_ai_json")
    def test_warning_result(self, mock_ai):
        mock_ai.return_value = {
            "citation_format_correct": True,
            "quote_accuracy": "close",
            "quote_diff": "Minor punctuation difference",
            "actual_quote": "The court held that negligence requires",
            "characterization_accuracy": "accurate",
            "characterization_explanation": "Supported",
            "confidence": 0.8,
            "overall_status": "warning",
        }

        result = verify_citation(_make_citation(), "opinion text")
        assert result.status == "warning"
        assert result.quote_accuracy == "close"

    @patch("backend.verifier.call_ai_json")
    def test_error_result(self, mock_ai):
        mock_ai.return_value = {
            "citation_format_correct": True,
            "quote_accuracy": "inaccurate",
            "quote_diff": "Quote does not match source",
            "actual_quote": "Completely different text",
            "characterization_accuracy": "misleading",
            "characterization_explanation": "Misrepresents the holding",
            "confidence": 0.9,
            "overall_status": "error",
        }

        result = verify_citation(_make_citation(), "opinion text")
        assert result.status == "error"
        assert result.quote_accuracy == "inaccurate"
        assert result.characterization_accuracy == "misleading"

    @patch("backend.verifier.call_ai_json")
    def test_no_quote_no_characterization(self, mock_ai):
        """Citation with no quote or characterization still works."""
        mock_ai.return_value = {
            "citation_format_correct": True,
            "quote_accuracy": None,
            "quote_diff": None,
            "actual_quote": None,
            "characterization_accuracy": None,
            "characterization_explanation": None,
            "confidence": 1.0,
            "overall_status": "verified",
        }

        citation = _make_citation(quoted_text=None, characterization=None)
        result = verify_citation(citation, "opinion text")
        assert result.status == "verified"

    @patch("backend.verifier.call_ai_json")
    def test_handles_invalid_ai_response(self, mock_ai):
        mock_ai.return_value = "not a dict"
        result = verify_citation(_make_citation(), "opinion text")
        assert result.status == "error"
        assert result.confidence == 0.0

    @patch("backend.verifier.call_ai_json")
    def test_truncates_long_opinion(self, mock_ai):
        mock_ai.return_value = {
            "citation_format_correct": True,
            "quote_accuracy": "exact",
            "quote_diff": None,
            "actual_quote": "match",
            "characterization_accuracy": None,
            "characterization_explanation": None,
            "confidence": 0.9,
            "overall_status": "verified",
        }

        long_opinion = "x" * 200_000  # Must exceed 150K to trigger truncation
        verify_citation(_make_citation(), long_opinion)

        # Check that the opinion was truncated in the prompt
        call_args = mock_ai.call_args
        prompt_text = call_args[1]["messages"][0]["content"] if "messages" in call_args[1] else call_args[0][0][0]["content"]
        assert "truncated" in prompt_text


class TestMakeUnverifiable:
    def test_default_reason(self):
        result = make_unverifiable_result()
        assert result.status == "unverifiable"
        assert result.citation_exists is False
        assert result.confidence == 0.0

    def test_custom_reason(self):
        result = make_unverifiable_result("Court not supported")
        assert result.characterization_explanation == "Court not supported"

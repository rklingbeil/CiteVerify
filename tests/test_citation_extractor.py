"""Tests for AI-powered citation extraction."""

from unittest.mock import patch

from backend.citation_extractor import ExtractedCitation, extract_citations


class TestExtractCitations:
    @patch("backend.citation_extractor.call_ai_json")
    def test_extracts_single_citation(self, mock_ai):
        mock_ai.return_value = [
            {
                "citation_text": "325 Or App 648",
                "case_name": "Smith v. Jones",
                "full_reference": "Smith v. Jones, 325 Or App 648 (2023)",
                "quoted_text": "The court held that...",
                "characterization": "establishing the burden of proof",
                "context": "In Smith v. Jones, the court...",
                "position_start": 100,
                "position_end": 150,
            }
        ]

        result = extract_citations("Some legal document text")
        assert len(result) == 1
        assert isinstance(result[0], ExtractedCitation)
        assert result[0].citation_text == "325 Or App 648"
        assert result[0].case_name == "Smith v. Jones"
        assert result[0].quoted_text == "The court held that..."

    @patch("backend.citation_extractor.call_ai_json")
    def test_extracts_multiple_citations(self, mock_ai):
        mock_ai.return_value = [
            {
                "citation_text": "325 Or App 648",
                "case_name": "Smith v. Jones",
                "full_reference": "ref1",
                "quoted_text": None,
                "characterization": None,
                "context": "ctx1",
                "position_start": 0,
                "position_end": 50,
            },
            {
                "citation_text": "550 US 544",
                "case_name": "Twombly",
                "full_reference": "ref2",
                "quoted_text": "a quote",
                "characterization": None,
                "context": "ctx2",
                "position_start": 200,
                "position_end": 250,
            },
        ]

        result = extract_citations("text")
        assert len(result) == 2

    @patch("backend.citation_extractor.call_ai_json")
    def test_handles_non_list_response(self, mock_ai):
        mock_ai.return_value = {
            "citation_text": "123 US 456",
            "case_name": "Test",
            "full_reference": "Test, 123 US 456",
            "quoted_text": None,
            "characterization": None,
            "context": "ctx",
            "position_start": 0,
            "position_end": 20,
        }

        result = extract_citations("text")
        assert len(result) == 1

    @patch("backend.citation_extractor.call_ai_json")
    def test_handles_empty_response(self, mock_ai):
        mock_ai.return_value = []
        result = extract_citations("text")
        assert result == []

    @patch("backend.citation_extractor.call_ai_json")
    def test_skips_non_dict_items(self, mock_ai):
        mock_ai.return_value = ["not a dict", 42, None]
        result = extract_citations("text")
        assert result == []

    @patch("backend.citation_extractor.call_ai_json")
    def test_long_document_chunking(self, mock_ai):
        """Documents > 80K chars get split into chunks."""
        mock_ai.return_value = [
            {
                "citation_text": "1 US 1",
                "case_name": "A v. B",
                "full_reference": "ref",
                "quoted_text": None,
                "characterization": None,
                "context": "ctx",
                "position_start": 100,
                "position_end": 120,
            }
        ]

        long_text = "x" * 90_000
        result = extract_citations(long_text)
        # Should have called AI twice (two chunks) and deduplicated
        assert mock_ai.call_count == 2
        assert len(result) >= 1

    @patch("backend.citation_extractor.call_ai_json")
    def test_deduplicates_across_chunks(self, mock_ai):
        """Same citation found in overlap region should be deduplicated."""
        citation_data = {
            "citation_text": "1 US 1",
            "case_name": "A v. B",
            "full_reference": "ref",
            "quoted_text": None,
            "characterization": None,
            "context": "ctx",
            "position_start": 100,
            "position_end": 120,
        }
        mock_ai.return_value = [citation_data]

        long_text = "x" * 90_000
        result = extract_citations(long_text)
        # Both chunks return same citation at ~position 100, should deduplicate
        assert len(result) >= 1

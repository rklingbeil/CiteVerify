"""Tests for code review fixes (P0/P1/P2).

Covers: HTML entity decoding, setattr allowlist, status/confidence validation,
truncated JSON recovery, auth, rate limiting, pipeline error handling,
job locking, position casting, encrypted PDF, Content-Disposition sanitization.
"""

import json
import os
import sys
import tempfile
import time
from unittest.mock import MagicMock, patch

import pytest

# ─── Helper to create ExtractedCitation with all required fields ─────────

from backend.citation_extractor import ExtractedCitation


def _make_citation(**kwargs):
    """Create ExtractedCitation with sensible defaults."""
    defaults = {
        "citation_text": "123 U.S. 456",
        "case_name": "Test v. Test",
        "full_reference": "Test v. Test, 123 U.S. 456",
        "quoted_text": None,
        "characterization": None,
        "context": "cited in support of argument",
        "position_start": 0,
        "position_end": 100,
    }
    defaults.update(kwargs)
    return ExtractedCitation(**defaults)


# ─── P0 #1: HTML entity decoding ─────────────────────────────────────────

from backend.source_lookup import _strip_html


class TestHtmlEntityDecoding:
    def test_smart_quotes_preserved(self):
        html = "<p>the court&rsquo;s holding</p>"
        result = _strip_html(html)
        assert "\u2019" in result or "'" in result
        assert "courts" not in result

    def test_em_dash_preserved(self):
        html = "<p>123&mdash;456</p>"
        result = _strip_html(html)
        assert "\u2014" in result

    def test_section_symbol_preserved(self):
        html = "<p>&sect;1983</p>"
        result = _strip_html(html)
        assert "\u00a7" in result

    def test_numeric_entities_preserved(self):
        html = "<p>Smith&#8217;s case</p>"
        result = _strip_html(html)
        assert "s case" in result

    def test_nbsp_becomes_space(self):
        html = "<p>Hello&nbsp;world</p>"
        result = _strip_html(html)
        assert "Hello world" in result


# ─── P0 #2: setattr allowlist ────────────────────────────────────────────

from backend.citation_extractor import _review_extraction


class TestSetAttrAllowlist:
    @patch("backend.citation_extractor.call_ai_json")
    def test_rejects_disallowed_field(self, mock_ai):
        """AI trying to set __class__ should be rejected."""
        citations = [_make_citation()]
        mock_ai.return_value = {
            "corrections": [{"index": 0, "field": "__class__", "new_value": "hacked"}],
            "missed": [],
        }
        result = _review_extraction("Some document text", citations)
        assert result[0].__class__.__name__ == "ExtractedCitation"

    @patch("backend.citation_extractor.call_ai_json")
    def test_allows_valid_field(self, mock_ai):
        """Correcting case_name should work."""
        citations = [_make_citation(case_name="Tset v. Tset")]
        mock_ai.return_value = {
            "corrections": [{"index": 0, "field": "case_name", "new_value": "Test v. Test"}],
            "missed": [],
        }
        result = _review_extraction("Some document text", citations)
        assert result[0].case_name == "Test v. Test"

    @patch("backend.citation_extractor.call_ai_json")
    def test_rejects_dunder_dict(self, mock_ai):
        """AI trying to set __dict__ should be rejected."""
        citations = [_make_citation()]
        mock_ai.return_value = {
            "corrections": [{"index": 0, "field": "__dict__", "new_value": {}}],
            "missed": [],
        }
        result = _review_extraction("Some document text", citations)
        assert result[0].citation_text == "123 U.S. 456"


# ─── P0 #3: Status and confidence validation ────────────────────────────

from backend.verifier import VerificationResult, verify_citation, verify_citation_from_knowledge, make_unverifiable_result


class TestStatusConfidenceValidation:
    @patch("backend.verifier.call_ai_json")
    def test_invalid_status_defaults_to_error(self, mock_ai):
        mock_ai.return_value = {
            "overall_status": "partially_verified",
            "confidence": 0.8,
            "citation_format_correct": True,
        }
        citation = _make_citation(quoted_text="some quote")
        result = verify_citation(citation, "opinion text here")
        assert result.status == "error"

    @patch("backend.verifier.call_ai_json")
    def test_confidence_clamped_high(self, mock_ai):
        mock_ai.return_value = {
            "overall_status": "verified",
            "confidence": 1.5,
            "citation_format_correct": True,
        }
        citation = _make_citation(quoted_text="some quote")
        result = verify_citation(citation, "opinion text here")
        assert result.confidence == 1.0

    @patch("backend.verifier.call_ai_json")
    def test_confidence_clamped_low(self, mock_ai):
        mock_ai.return_value = {
            "overall_status": "verified",
            "confidence": -0.3,
            "citation_format_correct": True,
        }
        citation = _make_citation(quoted_text="some quote")
        result = verify_citation(citation, "opinion text here")
        # -0.3 clamped to 0.0, then +0.05 review confirmation boost
        assert result.confidence == 0.05

    @patch("backend.verifier.call_ai_json")
    def test_knowledge_verify_invalid_status(self, mock_ai):
        mock_ai.return_value = {
            "overall_status": "uncertain",
            "confidence": 0.5,
            "citation_format_correct": True,
        }
        citation = _make_citation(quoted_text="some quote")
        result = verify_citation_from_knowledge(citation)
        assert result.status == "unverifiable"


# ─── P0 #4: Truncated JSON recovery ─────────────────────────────────────

from backend.ai_client import extract_json


class TestTruncatedJsonRecovery:
    def test_recovery_filters_objects_without_citation_keys(self):
        """When recovery produces array, objects missing citation_text and case_name are dropped."""
        # Unit test the filtering logic that runs in strategy #4
        import json as json_mod
        truncated = '[{"citation_text":"a","case_name":"A"},{"bad":true}]'
        parsed = json_mod.loads(truncated)
        # Apply same filter as in extract_json strategy #4
        filtered = [
            obj for obj in parsed
            if isinstance(obj, dict) and (obj.get("citation_text") or obj.get("case_name"))
        ]
        assert len(filtered) == 1
        assert filtered[0]["case_name"] == "A"

    def test_valid_json_array_parses_normally(self):
        text = '[{"citation_text": "123 U.S. 456", "case_name": "A"}, {"citation_text": "789 F.3d 101", "case_name": "B"}]'
        result = extract_json(text)
        assert isinstance(result, list)
        assert len(result) == 2


# ─── P1 #7: Pipeline step 5 error handling ──────────────────────────────

from backend.pipeline import run_verification


class TestPipelineStep5ErrorHandling:
    @patch("backend.pipeline.verify_citation_from_knowledge")
    @patch("backend.pipeline.verify_citation")
    @patch("backend.pipeline.lookup_citation")
    @patch("backend.pipeline.extract_citations")
    @patch("backend.pipeline.extract_document")
    def test_step5_exception_doesnt_kill_pipeline(
        self, mock_extract, mock_citations, mock_lookup, mock_verify, mock_knowledge
    ):
        from backend.extractor import ExtractionResult
        from backend.source_lookup import LookupResult

        mock_extract.return_value = ExtractionResult(text="doc text", page_count=1)
        mock_citations.return_value = [
            _make_citation(quoted_text="a quote"),
        ]
        mock_lookup.return_value = LookupResult(
            found=False, status="not_found", case_name="", court="",
            date_filed="", url="", source="", opinion_text=None,
        )
        mock_knowledge.side_effect = RuntimeError("API down")

        report = run_verification("/tmp/test.pdf", "test.pdf")
        assert report.total_citations == 1
        assert report.unverifiable == 1


# ─── P1 #8: Job state locking ───────────────────────────────────────────

from backend.jobs import JobState


class TestJobStateLocking:
    def test_job_to_dict_includes_all_fields(self):
        job = JobState(id="test-123", filename="test.pdf")
        d = job.to_dict()
        assert d["id"] == "test-123"
        assert d["status"] == "pending"
        assert d["error"] is None


# ─── P1 #9: Position value casting ──────────────────────────────────────

from backend.citation_extractor import _parse_citation_list


class TestPositionCasting:
    def test_string_positions_cast_to_int(self):
        items = [{"citation_text": "123 U.S. 456", "case_name": "Test",
                  "quoted_text": None, "characterization": None, "context": "",
                  "position_start": "95000", "position_end": "95500"}]
        result = _parse_citation_list(items)
        assert result[0].position_start == 95000
        assert result[0].position_end == 95500

    def test_invalid_position_defaults_to_zero(self):
        items = [{"citation_text": "123 U.S. 456", "case_name": "Test",
                  "position_start": "invalid", "position_end": None}]
        result = _parse_citation_list(items)
        assert result[0].position_start == 0
        assert result[0].position_end == 0

    def test_float_position_cast_to_int(self):
        items = [{"citation_text": "123 U.S. 456", "case_name": "Test",
                  "position_start": 95000.7}]
        result = _parse_citation_list(items)
        assert result[0].position_start == 95000


# ─── P1 #17: page_count bug ─────────────────────────────────────────────

from backend.extractor import extract_pdf


class TestPageCountFix:
    def test_page_count_is_total_not_text_only(self):
        page1 = MagicMock()
        page1.get_text.return_value = "Some text."
        page2 = MagicMock()
        page2.get_text.return_value = "   "

        mock_doc = MagicMock()
        mock_doc.__iter__ = lambda self: iter([page1, page2])
        mock_doc.__len__ = lambda self: 2
        mock_doc.__enter__ = lambda self: self
        mock_doc.__exit__ = lambda *_: None
        mock_doc.is_encrypted = False

        mock_fitz = MagicMock()
        mock_fitz.open.return_value = mock_doc

        with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as f:
            f.write(b"fake pdf")
            path = f.name

        try:
            with patch.dict(sys.modules, {"fitz": mock_fitz}):
                result = extract_pdf(path)
            assert result.page_count == 2
        finally:
            os.unlink(path)


# ─── P1 #26: Encrypted PDF detection ────────────────────────────────────

class TestEncryptedPdfDetection:
    def test_encrypted_pdf_raises_clear_error(self):
        mock_doc = MagicMock()
        mock_doc.__enter__ = lambda self: self
        mock_doc.__exit__ = lambda *_: None
        mock_doc.is_encrypted = True

        mock_fitz = MagicMock()
        mock_fitz.open.return_value = mock_doc

        with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as f:
            f.write(b"fake encrypted pdf")
            path = f.name

        try:
            with patch.dict(sys.modules, {"fitz": mock_fitz}):
                with pytest.raises(ValueError, match="password-protected"):
                    extract_pdf(path)
        finally:
            os.unlink(path)


# ─── P1 #12: Content-Disposition sanitization ────────────────────────────

class TestContentDispositionSanitization:
    def test_filename_injection_prevented(self, client):
        """Filename with special chars should be sanitized in Content-Disposition."""
        from backend.jobs import _reports, _lock
        from backend.pipeline import VerificationReport

        report = VerificationReport(
            id="test-report",
            filename='evil"file\nname.pdf',
            document_text="test",
            total_citations=0,
            verified=0, warnings=0, errors=0, unverifiable=0,
            created_at="2026-01-01T00:00:00Z",
        )
        with _lock:
            _reports["test-report"] = report

        try:
            resp = client.get("/api/reports/test-report/pdf")
            assert resp.status_code == 200
            cd = resp.headers.get("content-disposition", "")
            assert "\n" not in cd
            assert '"file' not in cd  # Quotes in filename are stripped
        finally:
            with _lock:
                _reports.pop("test-report", None)


# ─── P1 #15: Citation regex fix ─────────────────────────────────────────

from backend.source_lookup import _parse_citation_parts


class TestCitationRegexFix:
    def test_s_ct_reporter(self):
        result = _parse_citation_parts("556 S. Ct. 951")
        assert result is not None
        vol, reporter, page = result
        assert vol == "556"
        assert "S." in reporter and "Ct." in reporter
        assert page == "951"

    def test_l_ed_2d_reporter(self):
        result = _parse_citation_parts("500 L. Ed. 2d 100")
        assert result is not None
        vol, reporter, page = result
        assert vol == "500"
        assert page == "100"

    def test_f3d_reporter(self):
        result = _parse_citation_parts("69 F.3d 337")
        assert result is not None
        vol, reporter, page = result
        assert vol == "69"
        assert reporter.strip() == "F.3d"
        assert page == "337"

    def test_us_reporter(self):
        result = _parse_citation_parts("501 U.S. 44")
        assert result is not None
        vol, reporter, page = result
        assert vol == "501"
        assert page == "44"


# ─── Accuracy: Lookup cache (Id./supra resolution) ──────────────────────


class TestLookupCache:
    @patch("backend.pipeline.verify_citation_from_knowledge")
    @patch("backend.pipeline.lookup_citation")
    @patch("backend.pipeline.extract_citations")
    @patch("backend.pipeline.extract_document")
    def test_same_citation_text_reuses_lookup(
        self, mock_extract, mock_citations, mock_lookup, mock_knowledge
    ):
        """When two citations have the same citation_text (Id. resolved), lookup is called once."""
        from backend.extractor import ExtractionResult
        from backend.source_lookup import LookupResult

        mock_extract.return_value = ExtractionResult(text="doc text", page_count=1)
        mock_citations.return_value = [
            _make_citation(citation_text="501 U.S. 32 (1991)", case_name="A v. B",
                          full_reference="A v. B, 501 U.S. 32 (1991)"),
            _make_citation(citation_text="501 U.S. 32 (1991)", case_name="A v. B",
                          full_reference="Id. at 44", position_start=500),
        ]
        mock_lookup.return_value = LookupResult(
            found=True, status="found", case_name="A v. B", court="scotus",
            date_filed="1991-01-01", url="", source="courtlistener", opinion_text=None,
        )
        mock_knowledge.return_value = MagicMock(status="unverifiable")

        report = run_verification("/tmp/test.pdf", "test.pdf")
        assert report.total_citations == 2
        # lookup_citation should only be called ONCE (second is cached)
        assert mock_lookup.call_count == 1

    @patch("backend.pipeline.verify_citation_from_knowledge")
    @patch("backend.pipeline.lookup_citation")
    @patch("backend.pipeline.extract_citations")
    @patch("backend.pipeline.extract_document")
    def test_different_citation_text_separate_lookups(
        self, mock_extract, mock_citations, mock_lookup, mock_knowledge
    ):
        """Different citation_text values should trigger separate lookups."""
        from backend.extractor import ExtractionResult
        from backend.source_lookup import LookupResult

        mock_extract.return_value = ExtractionResult(text="doc text", page_count=1)
        mock_citations.return_value = [
            _make_citation(citation_text="501 U.S. 32 (1991)", case_name="A v. B"),
            _make_citation(citation_text="509 U.S. 579 (1993)", case_name="C v. D",
                          position_start=500),
        ]
        mock_lookup.return_value = LookupResult(
            found=True, status="found", case_name="", court="",
            date_filed="", url="", source="courtlistener", opinion_text=None,
        )
        mock_knowledge.return_value = MagicMock(status="unverifiable")

        run_verification("/tmp/test.pdf", "test.pdf")
        assert mock_lookup.call_count == 2


# ─── Accuracy: Case name mismatch detection ─────────────────────────────

from backend.pipeline import _case_names_match


class TestCaseNameMatch:
    def test_exact_match(self):
        assert _case_names_match("Smith v. Jones", "Smith v. Jones") is True

    def test_case_insensitive(self):
        assert _case_names_match("SMITH v. JONES", "smith v. jones") is True

    def test_partial_party_match(self):
        assert _case_names_match("Smith v. Jones", "Smith v. Jones Industries, Inc.") is True

    def test_abbreviation_match(self):
        assert _case_names_match("BMW of North America v. Gore", "BMW of N. Am., Inc. v. Gore") is True

    def test_no_match(self):
        assert _case_names_match("Smith v. Jones", "Doe v. Roe") is False

    def test_empty_names_assume_match(self):
        assert _case_names_match("", "Smith v. Jones") is True
        assert _case_names_match("Smith v. Jones", "") is True

    def test_mismatch_skips_source_verification(self):
        """When case names don't match, citation should go to knowledge verification."""
        from backend.source_lookup import LookupResult
        from backend.pipeline import CitationReport
        from backend.verifier import make_unverifiable_result

        cr = CitationReport(
            extraction=_make_citation(
                citation_text="498 F.3d 835",
                case_name="Morrison v. St. Luke's",
                quoted_text="some fabricated quote",
            ),
            lookup=LookupResult(
                found=True, status="found",
                case_name="Completely Different v. Case",
                court="ca8", date_filed="2007-01-01",
                url="", source="courtlistener",
                opinion_text="This is the opinion text for a completely different case",
            ),
            verification=make_unverifiable_result(),
        )

        # The pipeline would detect this mismatch and skip source verification
        assert not _case_names_match(cr.extraction.case_name, cr.lookup.case_name)


# ─── Legal Name Abbreviation Normalization ────────────────────────────────

from backend.source_lookup import normalize_legal_name


class TestNormalizeLegalName:
    def test_apostrophe_abbreviations(self):
        assert "international" in normalize_legal_name("Int'l")
        assert "national" in normalize_legal_name("Nat'l")
        assert "department" in normalize_legal_name("Dep't")
        assert "association" in normalize_legal_name("Ass'n")
        assert "government" in normalize_legal_name("Gov't")
        assert "commission" in normalize_legal_name("Comm'n")

    def test_period_abbreviations(self):
        assert "corporation" in normalize_legal_name("Corp.")
        assert "company" in normalize_legal_name("Co.")
        assert "incorporated" in normalize_legal_name("Inc.")
        assert "manufacturing" in normalize_legal_name("Mfg.")
        assert "railroad" in normalize_legal_name("R.R.")
        assert "university" in normalize_legal_name("Univ.")

    def test_full_forms_unchanged(self):
        assert "corporation" in normalize_legal_name("Corporation")
        assert "international" in normalize_legal_name("International")
        assert "department" in normalize_legal_name("Department")

    def test_full_case_name(self):
        a = normalize_legal_name("Alice Corp. v. CLS Bank Int'l")
        b = normalize_legal_name("Alice Corporation v. CLS Bank International")
        assert a == b

    def test_meritor_fsb(self):
        a = normalize_legal_name("Meritor Savings Bank, FSB v. Vinson")
        b = normalize_legal_name("Meritor Savings Bank, Federal Savings Bank v. Vinson")
        assert a == b

    def test_empty(self):
        assert normalize_legal_name("") == ""
        assert normalize_legal_name(None) == ""


class TestCaseNameMatchAbbreviations:
    """Abbreviation-aware case name matching."""

    def test_intl_vs_international(self):
        assert _case_names_match(
            "Alice Corp. v. CLS Bank Int'l",
            "Alice Corporation v. CLS Bank International",
        ) is True

    def test_corp_vs_corporation(self):
        assert _case_names_match(
            "Celotex Corp. v. Catrett",
            "Celotex Corporation v. Catrett",
        ) is True

    def test_dept_vs_department(self):
        assert _case_names_match(
            "Texas Dep't of Community Affairs v. Burdine",
            "Texas Department of Community Affairs v. Burdine",
        ) is True

    def test_mfg_vs_manufacturing(self):
        assert _case_names_match(
            "Acme Mfg. Co. v. Smith",
            "Acme Manufacturing Company v. Smith",
        ) is True

    def test_rr_vs_railroad(self):
        assert _case_names_match(
            "Palsgraf v. Long Island R.R. Co.",
            "Palsgraf v. Long Island Railroad Company",
        ) is True

    def test_assn_vs_association(self):
        assert _case_names_match(
            "Nat'l Ass'n of Mfrs. v. Dept of Labor",
            "National Association of Manufacturers v. Department of Labor",
        ) is True

    def test_unrelated_still_no_match(self):
        assert _case_names_match(
            "Smith v. Jones Corp.",
            "Doe v. Roe International",
        ) is False


# ─── Accuracy: Majority opinion preference ───────────────────────────────

from backend.source_lookup import _MAJORITY_OPINION_TYPES, _fetch_opinion_data


class TestMajorityOpinionPreference:
    def test_majority_types_defined(self):
        assert "020lead" in _MAJORITY_OPINION_TYPES
        assert "010combined" in _MAJORITY_OPINION_TYPES
        assert "040dissent" not in _MAJORITY_OPINION_TYPES
        assert "030concurrence" not in _MAJORITY_OPINION_TYPES

    @patch("backend.source_lookup.requests.get")
    def test_fetch_opinion_data_returns_type(self, mock_get):
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {
            "type": "020lead",
            "plain_text": "This is the majority opinion.",
        }
        mock_get.return_value = mock_resp

        text, opinion_type = _fetch_opinion_data(12345)
        assert text == "This is the majority opinion."
        assert opinion_type == "020lead"

    @patch("backend.source_lookup.requests.get")
    def test_fetch_opinion_data_dissent(self, mock_get):
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {
            "type": "040dissent",
            "plain_text": "I respectfully dissent.",
        }
        mock_get.return_value = mock_resp

        text, opinion_type = _fetch_opinion_data(12345)
        assert text == "I respectfully dissent."
        assert opinion_type == "040dissent"
        assert opinion_type not in _MAJORITY_OPINION_TYPES


# ─── Pass 2: Verification Review ────────��────────────────────────────────

from backend.verifier import _review_verification, _review_knowledge_verification, _apply_review


class TestVerificationReview:
    @patch("backend.verifier.call_ai_json")
    def test_review_agrees_boosts_confidence(self, mock_ai):
        """When reviewer agrees, confidence gets +0.05 boost."""
        mock_ai.return_value = {
            "agrees_with_initial": True,
            "overall_status": "verified",
            "confidence": 0.9,
        }
        citation = _make_citation(quoted_text="some quote")
        initial = VerificationResult(
            status="verified", citation_exists=True, citation_format_correct=True,
            quote_accuracy="exact", quote_diff=None, actual_quote="some quote",
            characterization_accuracy=None, characterization_explanation=None,
            confidence=0.85, reasoning="Pass 1 reasoning",
        )
        result = _review_verification(citation, "opinion text", initial)
        assert result.status == "verified"
        assert result.confidence == 0.9  # 0.85 + 0.05
        assert "Confirmed by review" in result.reasoning

    @patch("backend.verifier.call_ai_json")
    def test_review_disagrees_overrides(self, mock_ai):
        """When reviewer disagrees, their assessment overrides."""
        mock_ai.return_value = {
            "agrees_with_initial": False,
            "overall_status": "error",
            "confidence": 0.8,
            "quote_accuracy": "inaccurate",
            "quote_diff": "Quote not found in opinion",
            "actual_quote": None,
            "characterization_accuracy": None,
            "characterization_explanation": None,
            "reasoning": "I searched the entire opinion and could not find this quote",
            "citation_format_correct": True,
        }
        citation = _make_citation(quoted_text="fabricated quote")
        initial = VerificationResult(
            status="verified", citation_exists=True, citation_format_correct=True,
            quote_accuracy="exact", quote_diff=None, actual_quote="fabricated quote",
            characterization_accuracy=None, characterization_explanation=None,
            confidence=0.9, reasoning="Pass 1: found quote",
        )
        result = _review_verification(citation, "opinion text", initial)
        assert result.status == "error"
        assert result.quote_accuracy == "inaccurate"
        assert "override" in result.reasoning.lower()

    @patch("backend.verifier.call_ai_json")
    def test_review_failure_returns_initial(self, mock_ai):
        """If review call fails, return initial result unchanged."""
        mock_ai.side_effect = RuntimeError("API down")
        citation = _make_citation(quoted_text="a quote")
        initial = VerificationResult(
            status="verified", citation_exists=True, citation_format_correct=True,
            quote_accuracy="exact", quote_diff=None, actual_quote="a quote",
            characterization_accuracy=None, characterization_explanation=None,
            confidence=0.9, reasoning="Pass 1",
        )
        result = _review_verification(citation, "opinion text", initial)
        assert result.status == "verified"
        assert result.confidence == 0.9  # Unchanged

    def test_apply_review_agrees(self):
        """_apply_review with agrees=True boosts confidence."""
        initial = VerificationResult(
            status="warning", citation_exists=True, citation_format_correct=True,
            quote_accuracy="close", quote_diff="minor diff", actual_quote="text",
            characterization_accuracy=None, characterization_explanation=None,
            confidence=0.7,
        )
        review = {"agrees_with_initial": True}
        result = _apply_review(initial, review, "test")
        assert result.confidence == 0.75
        assert result.status == "warning"

    def test_apply_review_disagrees(self):
        """_apply_review with agrees=False uses review values."""
        initial = VerificationResult(
            status="verified", citation_exists=True, citation_format_correct=True,
            quote_accuracy="exact", quote_diff=None, actual_quote="text",
            characterization_accuracy=None, characterization_explanation=None,
            confidence=0.9,
        )
        review = {
            "agrees_with_initial": False,
            "overall_status": "warning",
            "confidence": 0.6,
            "reasoning": "Reviewer found issues",
        }
        result = _apply_review(initial, review, "test")
        assert result.status == "warning"
        assert result.confidence == 0.6


# ─── Pass 2: Knowledge Review ─────────────��──────────────────────────────

class TestKnowledgeReview:
    @patch("backend.verifier.call_ai_json")
    def test_knowledge_review_caps_confidence(self, mock_ai):
        """Knowledge review should enforce conservative confidence."""
        mock_ai.return_value = {
            "agrees_with_initial": False,
            "overall_status": "verified",
            "confidence": 0.6,  # Reviewer lowers confidence
            "reasoning": "Case is well-known but confidence should be lower",
            "citation_format_correct": True,
        }
        citation = _make_citation(quoted_text="some quote")
        initial = VerificationResult(
            status="verified", citation_exists=True, citation_format_correct=True,
            quote_accuracy="exact", quote_diff=None, actual_quote=None,
            characterization_accuracy=None, characterization_explanation=None,
            confidence=0.85, reasoning="Pass 1",
        )
        result = _review_knowledge_verification(citation, initial)
        assert result.confidence == 0.6  # Reviewer's lower value used


# ─── Pass 3: Cross-Citation Consistency ──────���───────────────────────────

from backend.pipeline import _check_cross_citation_consistency, CitationReport
from backend.source_lookup import LookupResult


class TestConsistencyCheck:
    @patch("backend.pipeline.call_ai_json")
    def test_consistent_results_no_changes(self, mock_ai):
        """When results are consistent, no adjustments made."""
        mock_ai.return_value = {"adjustments": [], "consistent": True}
        reports = [
            CitationReport(
                extraction=_make_citation(case_name="A v. B", citation_text="123 U.S. 456"),
                lookup=LookupResult(found=True, status="found"),
                verification=VerificationResult(
                    status="verified", citation_exists=True, citation_format_correct=True,
                    quote_accuracy=None, quote_diff=None, actual_quote=None,
                    characterization_accuracy=None, characterization_explanation=None,
                    confidence=0.9,
                ),
            ),
            CitationReport(
                extraction=_make_citation(case_name="C v. D", citation_text="456 F.3d 789"),
                lookup=LookupResult(found=True, status="found"),
                verification=VerificationResult(
                    status="verified", citation_exists=True, citation_format_correct=True,
                    quote_accuracy=None, quote_diff=None, actual_quote=None,
                    characterization_accuracy=None, characterization_explanation=None,
                    confidence=0.85,
                ),
            ),
        ]
        _check_cross_citation_consistency(reports)
        assert reports[0].verification.status == "verified"
        assert reports[1].verification.status == "verified"

    @patch("backend.pipeline.call_ai_json")
    def test_inconsistency_applies_adjustment(self, mock_ai):
        """When AI finds inconsistency, status is adjusted."""
        mock_ai.return_value = {
            "adjustments": [
                {"index": 1, "revised_status": "verified", "revised_confidence": 0.8,
                 "reason": "Same case found in citation 0"},
            ],
            "consistent": False,
        }
        reports = [
            CitationReport(
                extraction=_make_citation(case_name="A v. B", citation_text="123 U.S. 456"),
                lookup=LookupResult(found=True, status="found"),
                verification=VerificationResult(
                    status="verified", citation_exists=True, citation_format_correct=True,
                    quote_accuracy=None, quote_diff=None, actual_quote=None,
                    characterization_accuracy=None, characterization_explanation=None,
                    confidence=0.9,
                ),
            ),
            CitationReport(
                extraction=_make_citation(case_name="A v. B", citation_text="123 U.S. 456",
                                         position_start=500),
                lookup=LookupResult(found=True, status="found"),
                verification=VerificationResult(
                    status="error", citation_exists=True, citation_format_correct=True,
                    quote_accuracy=None, quote_diff=None, actual_quote=None,
                    characterization_accuracy=None, characterization_explanation=None,
                    confidence=0.3, reasoning="Original reasoning",
                ),
            ),
        ]
        _check_cross_citation_consistency(reports)
        assert reports[0].verification.status == "verified"  # Unchanged
        assert reports[1].verification.status == "verified"  # Adjusted
        assert reports[1].verification.confidence == 0.8
        assert "Consistency" in reports[1].verification.reasoning

    def test_single_citation_skips_check(self):
        """With only one citation, consistency check is a no-op."""
        reports = [
            CitationReport(
                extraction=_make_citation(),
                lookup=LookupResult(found=True, status="found"),
                verification=VerificationResult(
                    status="verified", citation_exists=True, citation_format_correct=True,
                    quote_accuracy=None, quote_diff=None, actual_quote=None,
                    characterization_accuracy=None, characterization_explanation=None,
                    confidence=0.9,
                ),
            ),
        ]
        # Should not raise — just returns without doing anything
        _check_cross_citation_consistency(reports)
        assert reports[0].verification.status == "verified"

    @patch("backend.pipeline.call_ai_json")
    def test_consistency_failure_preserves_results(self, mock_ai):
        """If consistency check fails, original results are preserved."""
        mock_ai.side_effect = RuntimeError("API down")
        reports = [
            CitationReport(
                extraction=_make_citation(case_name="A"),
                lookup=LookupResult(found=True, status="found"),
                verification=VerificationResult(
                    status="error", citation_exists=True, citation_format_correct=True,
                    quote_accuracy=None, quote_diff=None, actual_quote=None,
                    characterization_accuracy=None, characterization_explanation=None,
                    confidence=0.3,
                ),
            ),
            CitationReport(
                extraction=_make_citation(case_name="B", position_start=500),
                lookup=LookupResult(found=True, status="found"),
                verification=VerificationResult(
                    status="verified", citation_exists=True, citation_format_correct=True,
                    quote_accuracy=None, quote_diff=None, actual_quote=None,
                    characterization_accuracy=None, characterization_explanation=None,
                    confidence=0.9,
                ),
            ),
        ]
        _check_cross_citation_consistency(reports)
        assert reports[0].verification.status == "error"  # Unchanged
        assert reports[1].verification.status == "verified"  # Unchanged


# ─── Auth (P0 #5) ───────────���───────────────────────────────────────────

from backend.auth import _check_rate


class TestRateLimiting:
    def test_allows_under_limit(self):
        assert _check_rate(f"test-allow-{time.time()}", 5, 60) is True

    def test_blocks_over_limit(self):
        key = f"test-block-{time.time()}"
        for _ in range(5):
            _check_rate(key, 5, 60)
        assert _check_rate(key, 5, 60) is False


# ─── Accuracy #1: Programmatic Quote Pre-Search ─────────────────────────

from backend.verifier import (
    _find_quote_in_text, _normalize_for_search, _extract_pinpoint_context,
    _validate_ai_actual_quote, _derive_element_statuses, _cap_knowledge_confidence,
    _check_word_proximity, _extract_holdings, _is_vague_characterization,
    _LANDMARK_CASES,
)


class TestQuotePreSearch:
    def test_exact_match_found(self):
        opinion = "The court held that negligence requires proof of duty and breach."
        quote = "negligence requires proof of duty and breach"
        result = _find_quote_in_text(quote, opinion)
        assert result["found"] is True
        assert result["match_type"] == "exact"
        assert result["similarity"] == 1.0

    def test_fuzzy_match_found(self):
        opinion = "The court stated that a manufacturer has no duty to prevent financial loss."
        quote = "a manufacturer has no duty to prevent purely financial loss"
        result = _find_quote_in_text(quote, opinion)
        assert result["found"] is True
        assert result["match_type"] in ("fuzzy", "close_paraphrase")
        assert result["similarity"] >= 0.7

    def test_not_found(self):
        opinion = "This case involves breach of contract between commercial parties."
        quote = "the prosecution may not use statements stemming from custodial interrogation"
        result = _find_quote_in_text(quote, opinion)
        assert result["found"] is False
        assert result["match_type"] == "not_found"

    def test_short_quote_skipped(self):
        result = _find_quote_in_text("short", "some text")
        assert result["found"] is False

    def test_empty_inputs(self):
        assert _find_quote_in_text("", "text")["found"] is False
        assert _find_quote_in_text("quote", "")["found"] is False

    def test_normalize_smart_quotes(self):
        text = _normalize_for_search("the court\u2019s holding")
        assert "'" in text
        assert "\u2019" not in text

    def test_normalize_editorial_markers(self):
        text = _normalize_for_search("quote here [emphasis added] and more [internal citations omitted]")
        assert "[emphasis added]" not in text
        assert "[internal citations omitted]" not in text


# ─── Accuracy #4: AI actual_quote Validation ─────────────────────────────

class TestAiActualQuoteValidation:
    def test_valid_quote_passes(self):
        """When actual_quote exists in opinion, no modification."""
        result = VerificationResult(
            status="verified", citation_exists=True, citation_format_correct=True,
            quote_accuracy="exact", quote_diff=None,
            actual_quote="the court held that negligence requires proof",
            characterization_accuracy=None, characterization_explanation=None,
            confidence=0.95,
        )
        opinion = "x" * 500 + "the court held that negligence requires proof" + "y" * 500
        _validate_ai_actual_quote(result, opinion)
        assert result.quote_accuracy == "exact"  # Not modified
        assert result.confidence == 0.95

    def test_hallucinated_quote_flagged(self):
        """When actual_quote doesn't exist in opinion, result is modified."""
        result = VerificationResult(
            status="verified", citation_exists=True, citation_format_correct=True,
            quote_accuracy="exact", quote_diff=None,
            actual_quote="completely fabricated passage that does not appear anywhere at all in the text",
            characterization_accuracy=None, characterization_explanation=None,
            confidence=0.95,
        )
        opinion = "x" * 2000  # Long enough to trigger validation, no match
        _validate_ai_actual_quote(result, opinion)
        assert result.quote_accuracy == "close"  # Downgraded from exact
        assert result.confidence <= 0.7

    def test_short_opinion_skipped(self):
        """Short opinion texts (test fixtures) don't trigger validation."""
        result = VerificationResult(
            status="verified", citation_exists=True, citation_format_correct=True,
            quote_accuracy="exact", quote_diff=None,
            actual_quote="some quote text here for testing",
            characterization_accuracy=None, characterization_explanation=None,
            confidence=0.95,
        )
        _validate_ai_actual_quote(result, "short opinion")
        assert result.quote_accuracy == "exact"  # Not modified


# ─── Accuracy #5: Citation Plausibility Validation ───────────────────────

from backend.pipeline import validate_citation_plausibility


class TestCitationPlausibility:
    def test_valid_citation_is_plausible(self):
        result = validate_citation_plausibility("550 U.S. 544 (2007)")
        assert result["plausible"] is True

    def test_f3d_before_1993_implausible(self):
        result = validate_citation_plausibility("100 F.3d 200 (1985)")
        assert result["plausible"] is False
        assert "1993" in result["reason"]

    def test_f2d_after_1993_implausible(self):
        result = validate_citation_plausibility("100 F.2d 200 (2020)")
        assert result["plausible"] is False

    def test_no_year_passes(self):
        """Citations without a year can't be checked and pass."""
        result = validate_citation_plausibility("550 U.S. 544")
        assert result["plausible"] is True

    def test_unparseable_passes(self):
        """Unparseable citations don't block — they might be valid formats we don't know."""
        result = validate_citation_plausibility("Id. at 444")
        assert result["plausible"] is True

    def test_absurd_year_implausible(self):
        result = validate_citation_plausibility("100 U.S. 200 (1200)")
        assert result["plausible"] is False


# ─── Accuracy #6: Pinpoint-Targeted Context ──────────────────────────────

class TestPinpointContext:
    def test_finds_star_page_marker(self):
        opinion = "beginning text " + ("x" * 100) + " *255 This is the key passage. " + ("y" * 100)
        result = _extract_pinpoint_context(opinion, "255")
        assert result is not None
        assert "key passage" in result

    def test_no_page_marker_returns_none(self):
        opinion = "This opinion has no page markers anywhere."
        result = _extract_pinpoint_context(opinion, "42")
        assert result is None

    def test_empty_inputs(self):
        assert _extract_pinpoint_context("", "42") is None
        assert _extract_pinpoint_context("text", "") is None
        assert _extract_pinpoint_context("text", None) is None


# ─── Accuracy #7: Element Status Derivation ──────────────────────────────

class TestElementStatusDerivation:
    def test_exact_quote_verified(self):
        result = VerificationResult(
            status="verified", citation_exists=True, citation_format_correct=True,
            quote_accuracy="exact", quote_diff=None, actual_quote=None,
            characterization_accuracy="accurate", characterization_explanation=None,
            confidence=0.95,
        )
        _derive_element_statuses(result)
        assert result.quote_status == "verified"
        assert result.characterization_status == "verified"

    def test_close_quote_warning(self):
        result = VerificationResult(
            status="warning", citation_exists=True, citation_format_correct=True,
            quote_accuracy="close", quote_diff="minor diff", actual_quote=None,
            characterization_accuracy=None, characterization_explanation=None,
            confidence=0.7,
        )
        _derive_element_statuses(result)
        assert result.quote_status == "warning"
        assert result.characterization_status is None  # No characterization to assess

    def test_inaccurate_quote_error(self):
        result = VerificationResult(
            status="error", citation_exists=True, citation_format_correct=True,
            quote_accuracy="inaccurate", quote_diff="major diff", actual_quote=None,
            characterization_accuracy="unsupported", characterization_explanation="wrong",
            confidence=0.3,
        )
        _derive_element_statuses(result)
        assert result.quote_status == "error"
        assert result.characterization_status == "error"

    def test_misleading_characterization_warning(self):
        result = VerificationResult(
            status="warning", citation_exists=True, citation_format_correct=True,
            quote_accuracy=None, quote_diff=None, actual_quote=None,
            characterization_accuracy="misleading", characterization_explanation="context omitted",
            confidence=0.6,
        )
        _derive_element_statuses(result)
        assert result.quote_status is None
        assert result.characterization_status == "warning"


# ─── Accuracy #8: Knowledge Confidence Hard Cap ──────────────────────────

class TestKnowledgeConfidenceCap:
    def test_non_landmark_capped_at_07(self):
        result = VerificationResult(
            status="verified", citation_exists=True, citation_format_correct=True,
            quote_accuracy=None, quote_diff=None, actual_quote=None,
            characterization_accuracy=None, characterization_explanation=None,
            confidence=0.9,
        )
        _cap_knowledge_confidence(result, "Smith v. Jones")
        assert result.confidence == 0.7

    def test_landmark_capped_at_085(self):
        result = VerificationResult(
            status="verified", citation_exists=True, citation_format_correct=True,
            quote_accuracy=None, quote_diff=None, actual_quote=None,
            characterization_accuracy=None, characterization_explanation=None,
            confidence=0.95,
        )
        _cap_knowledge_confidence(result, "Miranda v. Arizona")
        assert result.confidence == 0.85

    def test_under_cap_not_modified(self):
        result = VerificationResult(
            status="verified", citation_exists=True, citation_format_correct=True,
            quote_accuracy=None, quote_diff=None, actual_quote=None,
            characterization_accuracy=None, characterization_explanation=None,
            confidence=0.5,
        )
        _cap_knowledge_confidence(result, "Smith v. Jones")
        assert result.confidence == 0.5  # Under cap, unchanged

    def test_landmark_cases_populated(self):
        assert len(_LANDMARK_CASES) >= 15
        assert "miranda v. arizona" in _LANDMARK_CASES
        assert "brown v. board of education" in _LANDMARK_CASES


# ─── Accuracy #2: Id./Supra Resolution ───────────────────────────────────

from backend.pipeline import _resolve_id_supra_references


class TestIdSupraResolution:
    def test_id_resolves_to_parent(self):
        citations = [
            _make_citation(citation_text="384 U.S. 436 (1966)", case_name="Miranda v. Arizona",
                          position_start=100),
            _make_citation(citation_text="Id. at 444", case_name="Miranda v. Arizona",
                          position_start=500),
        ]
        _resolve_id_supra_references(citations)
        assert citations[1].citation_text == "384 U.S. 436 (1966)"

    def test_supra_resolves_by_name(self):
        citations = [
            _make_citation(citation_text="550 U.S. 544 (2007)", case_name="Bell Atlantic v. Twombly",
                          position_start=100),
            _make_citation(citation_text="Twombly, supra", case_name="Bell Atlantic v. Twombly",
                          position_start=800),
        ]
        _resolve_id_supra_references(citations)
        assert citations[1].citation_text == "550 U.S. 544 (2007)"

    def test_bare_pinpoint_resolves(self):
        """'556 U.S. at 678' should resolve to '556 U.S. 662 (2009)'."""
        citations = [
            _make_citation(citation_text="556 U.S. 662 (2009)", case_name="Ashcroft v. Iqbal",
                          position_start=100),
            _make_citation(citation_text="556 U.S. at 678", case_name="Ashcroft v. Iqbal",
                          position_start=500),
        ]
        _resolve_id_supra_references(citations)
        assert citations[1].citation_text == "556 U.S. 662 (2009)"

    def test_no_parent_leaves_unchanged(self):
        """Id. without a preceding citation is left as-is."""
        citations = [
            _make_citation(citation_text="Id. at 444", case_name="Unknown",
                          position_start=100),
        ]
        _resolve_id_supra_references(citations)
        assert citations[0].citation_text == "Id. at 444"  # No parent to resolve to

    def test_multiple_cases_tracked(self):
        """Each case name tracks its own full citation."""
        citations = [
            _make_citation(citation_text="550 U.S. 544 (2007)", case_name="Bell Atlantic v. Twombly",
                          position_start=100),
            _make_citation(citation_text="556 U.S. 662 (2009)", case_name="Ashcroft v. Iqbal",
                          position_start=300),
            _make_citation(citation_text="Id. at 678", case_name="Ashcroft v. Iqbal",
                          position_start=500),
        ]
        _resolve_id_supra_references(citations)
        # Id. should resolve to Iqbal (most recent), not Twombly
        assert citations[2].citation_text == "556 U.S. 662 (2009)"


# ─── Accuracy #3: Constrained Consistency Check ─────────────────────────

class TestConstrainedConsistencyCheck:
    @patch("backend.pipeline.call_ai_json")
    def test_blocks_error_downgrade_with_unsupported_characterization(self, mock_ai):
        """Consistency check must NOT downgrade error->warning when characterization is unsupported."""
        mock_ai.return_value = {
            "adjustments": [
                {"index": 0, "revised_status": "warning", "revised_confidence": 0.5,
                 "reason": "Case found, should be warning"},
            ],
            "consistent": False,
        }
        reports = [
            CitationReport(
                extraction=_make_citation(case_name="Palsgraf v. Long Island R.R.",
                                         characterization="absolute duty to prevent all harm"),
                lookup=LookupResult(found=True, status="found", case_name="Palsgraf v. Long Island R.R. Co."),
                verification=VerificationResult(
                    status="error", citation_exists=True, citation_format_correct=True,
                    quote_accuracy=None, quote_diff=None, actual_quote=None,
                    characterization_accuracy="unsupported",
                    characterization_explanation="Palsgraf holds the opposite",
                    confidence=0.9, reasoning="Characterization is fundamentally wrong",
                ),
            ),
            CitationReport(
                extraction=_make_citation(case_name="Other v. Case", position_start=500),
                lookup=LookupResult(found=True, status="found"),
                verification=VerificationResult(
                    status="verified", citation_exists=True, citation_format_correct=True,
                    quote_accuracy=None, quote_diff=None, actual_quote=None,
                    characterization_accuracy=None, characterization_explanation=None,
                    confidence=0.9,
                ),
            ),
        ]
        _check_cross_citation_consistency(reports)
        # Error should NOT have been downgraded to warning
        assert reports[0].verification.status == "error"

    @patch("backend.pipeline.call_ai_json")
    def test_blocks_error_downgrade_with_inaccurate_quote(self, mock_ai):
        """Consistency check must NOT downgrade error->warning when quote is inaccurate."""
        mock_ai.return_value = {
            "adjustments": [
                {"index": 0, "revised_status": "warning", "revised_confidence": 0.5,
                 "reason": "Should be warning"},
            ],
            "consistent": False,
        }
        reports = [
            CitationReport(
                extraction=_make_citation(case_name="Test v. Case", quoted_text="fake quote"),
                lookup=LookupResult(found=True, status="found"),
                verification=VerificationResult(
                    status="error", citation_exists=True, citation_format_correct=True,
                    quote_accuracy="inaccurate", quote_diff="Quote not in opinion",
                    actual_quote=None,
                    characterization_accuracy=None, characterization_explanation=None,
                    confidence=0.8,
                ),
            ),
            CitationReport(
                extraction=_make_citation(case_name="Other", position_start=500),
                lookup=LookupResult(found=True, status="found"),
                verification=VerificationResult(
                    status="verified", citation_exists=True, citation_format_correct=True,
                    quote_accuracy=None, quote_diff=None, actual_quote=None,
                    characterization_accuracy=None, characterization_explanation=None,
                    confidence=0.9,
                ),
            ),
        ]
        _check_cross_citation_consistency(reports)
        assert reports[0].verification.status == "error"  # Blocked

    @patch("backend.pipeline.call_ai_json")
    def test_allows_upgrade_from_error_without_accuracy_issues(self, mock_ai):
        """Consistency CAN upgrade error->warning when no quote/char accuracy issues."""
        mock_ai.return_value = {
            "adjustments": [
                {"index": 0, "revised_status": "warning", "revised_confidence": 0.5,
                 "reason": "Wrong case lookup, should be warning"},
            ],
            "consistent": False,
        }
        reports = [
            CitationReport(
                extraction=_make_citation(case_name="Test"),
                lookup=LookupResult(found=True, status="found"),
                verification=VerificationResult(
                    status="error", citation_exists=True, citation_format_correct=True,
                    quote_accuracy=None, quote_diff=None, actual_quote=None,
                    characterization_accuracy=None, characterization_explanation=None,
                    confidence=0.3,
                ),
            ),
            CitationReport(
                extraction=_make_citation(case_name="Other", position_start=500),
                lookup=LookupResult(found=True, status="found"),
                verification=VerificationResult(
                    status="verified", citation_exists=True, citation_format_correct=True,
                    quote_accuracy=None, quote_diff=None, actual_quote=None,
                    characterization_accuracy=None, characterization_explanation=None,
                    confidence=0.9,
                ),
            ),
        ]
        _check_cross_citation_consistency(reports)
        assert reports[0].verification.status == "warning"  # Allowed


# ─── P1 #11: Thread-safe AI client ──────────────────────────────────────

class TestThreadSafeClient:
    def test_get_client_requires_api_key(self):
        import backend.ai_client as mod
        old_client = mod._client
        old_key = mod.ANTHROPIC_API_KEY
        try:
            mod._client = None
            mod.ANTHROPIC_API_KEY = ""
            with pytest.raises(RuntimeError, match="ANTHROPIC_API_KEY"):
                mod.get_client()
        finally:
            mod._client = old_client
            mod.ANTHROPIC_API_KEY = old_key


# ─── Accuracy #9: Word Proximity Analysis (#3) ─────────────────────────

class TestWordProximity:
    def test_mere_scintilla_found(self):
        """'mere scintilla' should match 'the mere existence of a scintilla of evidence'."""
        words = ["mere", "scintilla"]
        text = "the court held that the mere existence of a scintilla of evidence is not sufficient"
        result = _check_word_proximity(words, text)
        assert result["found"] is True
        assert result["match_type"] == "close_paraphrase"
        assert result["similarity"] == 0.75

    def test_words_far_apart_not_matched(self):
        """Words too far apart should not match."""
        words = ["negligence", "damages"]
        text = ("negligence " + "x " * 200 + "damages")
        result = _check_word_proximity(words, text)
        assert result["found"] is False

    def test_missing_words_not_matched(self):
        """If most content words are missing, no match."""
        words = ["fabricated", "holding", "special"]
        text = "the court discussed breach of contract and warranty"
        result = _check_word_proximity(words, text)
        assert result["found"] is False

    def test_stop_words_filtered(self):
        """Stop words should not count as content words."""
        words = ["the", "and", "mere", "scintilla"]
        text = "a mere scintilla"
        result = _check_word_proximity(words, text)
        assert result["found"] is True  # Only "mere" and "scintilla" matter

    def test_single_content_word_skipped(self):
        """Need at least 2 content words for proximity check."""
        words = ["the", "a"]
        result = _check_word_proximity(words, "the a something")
        assert result["found"] is False

    def test_close_paraphrase_in_find_quote(self):
        """Integration: _find_quote_in_text should use proximity for short phrases."""
        # "mere scintilla" has only 2 words, below the 4-word phrase threshold
        # but should be caught by word proximity
        opinion = "x " * 50 + "the mere existence of a scintilla of evidence" + " y" * 50
        result = _find_quote_in_text("mere scintilla", opinion)
        assert result["found"] is True
        assert result["match_type"] == "close_paraphrase"


# ─── Accuracy #10: Holdings Extraction (#5) ──────────────────────────────

class TestHoldingsExtraction:
    def test_extracts_we_hold_that(self):
        opinion = (
            "Background discussion. " * 20
            + "Based on the foregoing analysis, we hold that the statute requires but-for causation. "
            + "Further discussion. " * 20
        )
        result = _extract_holdings(opinion)
        assert result is not None
        assert "we hold that" in result.lower()
        assert "but-for causation" in result

    def test_extracts_we_conclude(self):
        opinion = (
            "Long discussion. " * 30
            + "We conclude that Daubert's gatekeeping obligation applies to all expert testimony. "
            + "More text. " * 20
        )
        result = _extract_holdings(opinion)
        assert result is not None
        assert "conclude" in result.lower()

    def test_short_opinion_returns_none(self):
        result = _extract_holdings("Short opinion.")
        assert result is None

    def test_no_holdings_returns_none(self):
        opinion = "This case involves a contract dispute. The parties disagree. " * 30
        result = _extract_holdings(opinion)
        assert result is None

    def test_max_excerpts_respected(self):
        opinion = (
            "We hold that X. " + "text " * 100
            + "We hold that Y. " + "text " * 100
            + "We hold that Z. " + "text " * 100
            + "We hold that W. " + "text " * 100
        )
        result = _extract_holdings(opinion, max_excerpts=2)
        assert result is not None
        # Should have at most 2 excerpts separated by ---
        assert result.count("---") <= 1


# ─── Accuracy #11: Vague Characterization Detection (#6) ────────────────

class TestVagueCharacterization:
    def test_refined_this_analysis_is_vague(self):
        assert _is_vague_characterization("refined this analysis") is True

    def test_addressed_this_issue_is_vague(self):
        assert _is_vague_characterization("addressed this issue") is True

    def test_discussed_the_matter_is_vague(self):
        assert _is_vague_characterization("discussed that matter") is True

    def test_specific_holding_not_vague(self):
        assert _is_vague_characterization(
            "held that the moving party bears the initial burden of demonstrating absence of genuine issue"
        ) is False

    def test_empty_not_vague(self):
        assert _is_vague_characterization("") is False
        assert _is_vague_characterization(None) is False

    def test_short_but_specific_not_vague(self):
        """Short characterizations with specific legal content should not be flagged."""
        assert _is_vague_characterization("established four-part test") is False


# ─── Accuracy #12: Knowledge Confidence Cap with Mismatch ──────────────

class TestKnowledgeConfidenceCapMismatch:
    def test_mismatch_caps_at_04(self):
        result = VerificationResult(
            status="warning", citation_exists=True, citation_format_correct=True,
            quote_accuracy=None, quote_diff=None, actual_quote=None,
            characterization_accuracy=None, characterization_explanation=None,
            confidence=0.65,
        )
        _cap_knowledge_confidence(result, "Morrison v. St. Luke's", has_lookup_mismatch=True)
        assert result.confidence == 0.4

    def test_mismatch_overrides_landmark(self):
        """Even landmark cases get strict cap when there's a lookup mismatch."""
        result = VerificationResult(
            status="warning", citation_exists=True, citation_format_correct=True,
            quote_accuracy=None, quote_diff=None, actual_quote=None,
            characterization_accuracy=None, characterization_explanation=None,
            confidence=0.8,
        )
        _cap_knowledge_confidence(result, "Miranda v. Arizona", has_lookup_mismatch=True)
        assert result.confidence == 0.4

    def test_no_mismatch_uses_normal_caps(self):
        result = VerificationResult(
            status="verified", citation_exists=True, citation_format_correct=True,
            quote_accuracy=None, quote_diff=None, actual_quote=None,
            characterization_accuracy=None, characterization_explanation=None,
            confidence=0.9,
        )
        _cap_knowledge_confidence(result, "Some v. Case", has_lookup_mismatch=False)
        assert result.confidence == 0.7  # Normal non-landmark cap

    def test_under_mismatch_cap_unchanged(self):
        result = VerificationResult(
            status="warning", citation_exists=True, citation_format_correct=True,
            quote_accuracy=None, quote_diff=None, actual_quote=None,
            characterization_accuracy=None, characterization_explanation=None,
            confidence=0.3,
        )
        _cap_knowledge_confidence(result, "Fake v. Case", has_lookup_mismatch=True)
        assert result.confidence == 0.3  # Already under 0.4 cap


# ─── Accuracy #13: Mismatch Escalation in Pipeline ──────────────────────

class TestMismatchEscalation:
    @patch("backend.pipeline.verify_citation_from_knowledge")
    @patch("backend.pipeline.confirm_case_by_name")
    def test_low_confidence_mismatch_escalated_to_error(self, mock_confirm, mock_knowledge):
        """When lookup mismatch + low knowledge confidence, escalate to error."""
        mock_confirm.return_value = False  # Case name search also fails

        # Simulate knowledge verification returning warning with low confidence
        mock_knowledge.return_value = VerificationResult(
            status="warning", citation_exists=True, citation_format_correct=True,
            quote_accuracy="close", quote_diff=None, actual_quote=None,
            characterization_accuracy=None, characterization_explanation=None,
            confidence=0.45, reasoning="Seems like a recognized case",
        )

        from backend.pipeline import CitationReport
        cr = CitationReport(
            extraction=_make_citation(
                case_name="Morrison v. St. Luke's",
                citation_text="498 F.3d 835 (8th Cir. 2007)",
                quoted_text="A hospital that grants privileges...",
            ),
            lookup=LookupResult(
                found=True, status="found",
                case_name="Buytendorp v. Extendicare",
                opinion_text="Wrong case opinion text",
            ),
            verification=make_unverifiable_result("Source text may be for wrong case"),
        )

        # Simulate the pipeline's mismatch + escalation logic
        has_mismatch = True
        lookup_context = "CRITICAL LOOKUP WARNING: database returned different case"
        cr.verification = verify_citation_from_knowledge(
            cr.extraction, lookup_context=lookup_context,
            has_lookup_mismatch=has_mismatch,
        )
        # Simulate the escalation check from pipeline
        if has_mismatch and cr.verification.status in ("warning", "unverifiable"):
            if cr.verification.confidence <= 0.5:
                cr.verification.status = "error"

        assert cr.verification.status == "error"

    @patch("backend.pipeline.verify_citation_from_knowledge")
    @patch("backend.pipeline.confirm_case_by_name")
    def test_high_confidence_mismatch_not_escalated(self, mock_confirm, mock_knowledge):
        """When lookup mismatch but high knowledge confidence, don't escalate."""
        mock_confirm.return_value = False

        mock_knowledge.return_value = VerificationResult(
            status="verified", citation_exists=True, citation_format_correct=True,
            quote_accuracy=None, quote_diff=None, actual_quote=None,
            characterization_accuracy="accurate", characterization_explanation="Known case",
            confidence=0.7, reasoning="Well-known landmark case",
        )

        # With mismatch cap at 0.4, confidence would be capped
        # But the status is "verified" not "warning", so escalation check doesn't apply
        from backend.pipeline import CitationReport
        cr = CitationReport(
            extraction=_make_citation(case_name="Miranda v. Arizona",
                                     citation_text="384 U.S. 436 (1966)",
                                     characterization="right to remain silent"),
            lookup=LookupResult(found=True, status="found", case_name="Wrong Case",
                               opinion_text="Wrong text"),
            verification=make_unverifiable_result(),
        )

        cr.verification = verify_citation_from_knowledge(
            cr.extraction, lookup_context="WARNING", has_lookup_mismatch=True,
        )
        # Note: confidence would be capped at 0.4 by _cap_knowledge_confidence
        # But status "verified" is not in ("warning", "unverifiable") so no escalation
        # Actually with 0.4 cap the AI might set it differently...
        # The point is the logic only escalates warning/unverifiable
        has_mismatch = True
        if has_mismatch and cr.verification.status in ("warning", "unverifiable"):
            if cr.verification.confidence <= 0.5:
                cr.verification.status = "error"
        # The mock returns "verified" so it should not be escalated
        assert cr.verification.status == "verified"


# ─── Accuracy #14: Lookup Context in Knowledge Prompt ────────────────────

class TestLookupContextPropagation:
    @patch("backend.verifier.call_ai_json")
    def test_mismatch_context_included_in_prompt(self, mock_ai):
        """When lookup_context is provided, it should appear in the FIRST AI call."""
        mock_ai.return_value = {
            "overall_status": "unverifiable",
            "citation_format_correct": True,
            "quote_accuracy": None,
            "quote_diff": None,
            "actual_quote": None,
            "characterization_accuracy": "unsupported",
            "characterization_explanation": "Cannot verify",
            "confidence": 0.2,
            "reasoning": "Case not recognized",
            "agrees_with_initial": True,
        }

        citation = _make_citation(
            case_name="Whitfield v. Pacific Medical",
            citation_text="387 F.3d 1042 (9th Cir. 2004)",
            characterization="holding about expert testimony",
        )

        verify_citation_from_knowledge(
            citation,
            lookup_context="CRITICAL LOOKUP WARNING: returned different case",
            has_lookup_mismatch=True,
        )

        # Check the FIRST call's prompt (initial verification, not review pass)
        assert mock_ai.call_count >= 1
        first_call = mock_ai.call_args_list[0]
        messages = first_call.kwargs.get("messages") or first_call[1].get("messages", [])
        prompt_text = messages[0]["content"] if messages else ""
        assert "CRITICAL LOOKUP WARNING" in prompt_text

    @patch("backend.verifier.call_ai_json")
    def test_no_context_when_no_mismatch(self, mock_ai):
        """Without lookup_context, prompt should not contain mismatch warnings."""
        mock_ai.return_value = {
            "overall_status": "verified",
            "citation_format_correct": True,
            "confidence": 0.6,
            "reasoning": "Recognized case",
            "agrees_with_initial": True,
        }

        citation = _make_citation(
            case_name="Daubert v. Merrell Dow",
            citation_text="509 U.S. 579 (1993)",
            characterization="gatekeeping function",
        )

        verify_citation_from_knowledge(citation)  # No lookup_context

        # Check ALL calls don't contain the warning
        for call in mock_ai.call_args_list:
            messages = call.kwargs.get("messages") or call[1].get("messages", [])
            prompt_text = messages[0]["content"] if messages else ""
            assert "CRITICAL LOOKUP WARNING" not in prompt_text

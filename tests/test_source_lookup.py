"""Tests for case law source lookup."""

from unittest.mock import MagicMock, patch

from backend.source_lookup import (
    LookupResult,
    _extract_id_from_url,
    _parse_citation_parts,
    _clean_citation_for_search,
    _strip_html,
    lookup_citation,
    lookup_citation_courtlistener,
    lookup_citation_govinfo,
)


class TestHelpers:
    def test_extract_id_from_url_opinions(self):
        assert _extract_id_from_url("https://www.courtlistener.com/api/rest/v4/opinions/12345/") == 12345

    def test_extract_id_from_url_clusters(self):
        assert _extract_id_from_url("/api/rest/v4/clusters/67890/") == 67890

    def test_extract_id_from_url_nested(self):
        # Should get the LAST numeric segment
        assert _extract_id_from_url("/api/rest/v4/clusters/99/opinions/12345/") == 12345

    def test_extract_id_from_url_bare_int(self):
        assert _extract_id_from_url("42") == 42

    def test_extract_id_from_url_none(self):
        assert _extract_id_from_url("") is None
        assert _extract_id_from_url("no-digits") is None

    def test_strip_html(self):
        html = "<p>Hello &amp; <b>world</b>&nbsp;!</p>"
        assert _strip_html(html) == "Hello & world !"

    def test_strip_html_removes_script(self):
        html = "<p>text</p><script>alert(1)</script><p>more</p>"
        assert "alert" not in _strip_html(html)

    def test_strip_html_removes_style(self):
        html = "<style>.foo { color: red; }</style><p>content</p>"
        result = _strip_html(html)
        assert "color" not in result
        assert "content" in result

    def test_clean_citation_for_search(self):
        assert _clean_citation_for_search("501 U.S. 32 (1991)") == "501 U.S. 32"
        assert _clean_citation_for_search("464 F.3d at 958") == "464 F.3d"
        assert _clean_citation_for_search("501 U.S. 32, 44") == "501 U.S. 32"

    def test_parse_citation_parts(self):
        parts = _parse_citation_parts("501 U.S. 32 (1991)")
        assert parts == ("501", "U.S.", "32")

    def test_parse_citation_parts_f3d(self):
        parts = _parse_citation_parts("464 F.3d 951")
        assert parts == ("464", "F.3d", "951")

    def test_parse_citation_parts_unparseable(self):
        assert _parse_citation_parts("Smith v. Jones") is None


class TestCitationLookup:
    @patch("backend.source_lookup._cl_citation_lookup")
    def test_citation_lookup_api_used_first(self, mock_lookup):
        """Citation Lookup API is tried before search API."""
        mock_lookup.return_value = LookupResult(
            found=True, status="found", case_name="Test Case", source="courtlistener"
        )
        result = lookup_citation_courtlistener("501 U.S. 32")
        assert result.found is True
        assert result.case_name == "Test Case"
        mock_lookup.assert_called_once()

    @patch("backend.source_lookup._cl_v4_search")
    @patch("backend.source_lookup._cl_citation_lookup")
    def test_falls_back_to_search(self, mock_lookup, mock_search):
        """Falls back to search API when citation lookup returns None."""
        mock_lookup.return_value = None
        mock_search.return_value = LookupResult(
            found=True, status="found", case_name="Search Result", source="courtlistener"
        )
        result = lookup_citation_courtlistener("501 U.S. 32")
        assert result.found is True
        mock_search.assert_called()

    @patch("backend.source_lookup._cl_v4_search")
    @patch("backend.source_lookup._cl_citation_lookup")
    def test_not_found(self, mock_lookup, mock_search):
        mock_lookup.return_value = None
        mock_search.return_value = None
        result = lookup_citation_courtlistener("999 Fake 999")
        assert result.found is False
        assert result.status == "not_found"


class TestGovInfo:
    @patch("backend.source_lookup._throttle_gi")
    @patch("backend.source_lookup.requests.post")
    def test_found(self, mock_post, _mock_throttle):
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {
            "results": [
                {
                    "title": "United States v. Doe",
                    "governmentAuthor": ["S.D.N.Y."],
                    "dateIssued": "2020-05-01",
                    "download": {"pdfLink": "https://govinfo.gov/pdf"},
                    "resultLink": "https://govinfo.gov/result",
                }
            ]
        }
        mock_post.return_value = mock_resp

        result = lookup_citation_govinfo("550 US 544")
        assert result.found is True
        assert result.source == "govinfo"

    @patch("backend.source_lookup._throttle_gi")
    @patch("backend.source_lookup.requests.post")
    def test_not_found(self, mock_post, _mock_throttle):
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {"results": []}
        mock_post.return_value = mock_resp

        result = lookup_citation_govinfo("fake")
        assert result.found is False


class TestUnifiedLookup:
    @patch("backend.source_lookup.lookup_citation_govinfo")
    @patch("backend.source_lookup.lookup_citation_courtlistener")
    def test_uses_courtlistener_first(self, mock_cl, mock_gi):
        mock_cl.return_value = LookupResult(found=True, status="found", source="courtlistener")
        result = lookup_citation("test")
        assert result.source == "courtlistener"
        mock_gi.assert_not_called()

    @patch("backend.source_lookup.lookup_citation_govinfo")
    @patch("backend.source_lookup.lookup_citation_courtlistener")
    def test_falls_back_to_govinfo(self, mock_cl, mock_gi):
        mock_cl.return_value = LookupResult(found=False, status="not_found")
        mock_gi.return_value = LookupResult(found=True, status="found", source="govinfo")
        result = lookup_citation("test")
        assert result.source == "govinfo"

    def test_empty_citation_returns_not_found(self):
        result = lookup_citation("")
        assert result.found is False
        assert result.status == "not_found"

    def test_whitespace_citation_returns_not_found(self):
        result = lookup_citation("   ")
        assert result.found is False

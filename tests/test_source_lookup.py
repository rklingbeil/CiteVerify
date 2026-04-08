"""Tests for case law source lookup."""

from unittest.mock import MagicMock, patch

from backend.source_lookup import (
    LookupResult,
    _extract_cluster_id,
    _extract_opinion_id,
    _strip_html,
    lookup_citation,
    lookup_citation_courtlistener,
    lookup_citation_govinfo,
)


class TestHelpers:
    def test_extract_cluster_id_from_cluster_url(self):
        assert _extract_cluster_id("/api/rest/v4/clusters/12345/") == 12345

    def test_extract_cluster_id_from_opinion_url(self):
        assert _extract_cluster_id("/opinion/67890/some-case/") == 67890

    def test_extract_cluster_id_none(self):
        assert _extract_cluster_id("no match") is None

    def test_extract_opinion_id_from_url(self):
        assert _extract_opinion_id("/api/rest/v4/opinions/111/") == 111

    def test_extract_opinion_id_bare_int(self):
        assert _extract_opinion_id("42") == 42

    def test_extract_opinion_id_none(self):
        assert _extract_opinion_id("nope") is None

    def test_strip_html(self):
        html = "<p>Hello &amp; <b>world</b>&nbsp;!</p>"
        assert _strip_html(html) == "Hello & world !"


class TestCourtListener:
    @patch("backend.source_lookup._throttle_cl")
    @patch("backend.source_lookup.requests.post")
    def test_found(self, mock_post, _mock_throttle):
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = [
            {
                "case_name": "Smith v. Jones",
                "court": "orctapp",
                "date_filed": "2023-01-15",
                "absolute_url": "/opinion/12345/smith-v-jones/",
                "cluster_url": "/api/rest/v4/clusters/12345/",
            }
        ]
        mock_post.return_value = mock_resp

        with patch("backend.source_lookup._fetch_opinion_text", return_value="opinion text"):
            result = lookup_citation_courtlistener("325 Or App 648")

        assert result.found is True
        assert result.case_name == "Smith v. Jones"
        assert result.source == "courtlistener"
        assert result.opinion_text == "opinion text"

    @patch("backend.source_lookup._throttle_cl")
    @patch("backend.source_lookup.requests.post")
    def test_not_found(self, mock_post, _mock_throttle):
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = []
        mock_post.return_value = mock_resp

        result = lookup_citation_courtlistener("999 Fake 999")
        assert result.found is False
        assert result.status == "not_found"

    @patch("backend.source_lookup._throttle_cl")
    @patch("backend.source_lookup.requests.post")
    def test_api_error(self, mock_post, _mock_throttle):
        mock_resp = MagicMock()
        mock_resp.status_code = 500
        mock_post.return_value = mock_resp

        result = lookup_citation_courtlistener("325 Or App 648")
        assert result.found is False
        assert result.status == "error"

    @patch("backend.source_lookup._throttle_cl")
    @patch("backend.source_lookup.requests.post")
    def test_request_exception(self, mock_post, _mock_throttle):
        import requests
        mock_post.side_effect = requests.RequestException("timeout")

        result = lookup_citation_courtlistener("325 Or App 648")
        assert result.found is False
        assert result.status == "error"


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
                    "court": "S.D.N.Y.",
                    "dateIssued": "2020-05-01",
                    "detailsLink": "https://govinfo.gov/...",
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

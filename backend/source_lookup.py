"""Case law source lookup — CourtListener and GovInfo APIs."""

import logging
import re
import threading
import time
from dataclasses import dataclass
from typing import Optional

import requests

from backend.config import (
    COURTLISTENER_API_TOKEN,
    COURTLISTENER_BASE,
    CL_MIN_INTERVAL,
    GOVINFO_API_KEY,
    GOVINFO_BASE,
    GI_MIN_INTERVAL,
)

logger = logging.getLogger(__name__)


@dataclass
class LookupResult:
    found: bool
    status: str  # "found", "not_found", "error"
    case_name: str = ""
    court: str = ""
    date_filed: str = ""
    cluster_id: int | None = None
    opinion_id: int | None = None
    opinion_text: str | None = None
    url: str = ""
    source: str = ""  # "courtlistener" or "govinfo"


# ─── Rate Limiting ────────────────────────────────────────────────────────

_cl_lock = threading.Lock()
_cl_last_request = 0.0
_gi_lock = threading.Lock()
_gi_last_request = 0.0


def _throttle_cl() -> None:
    """Enforce CourtListener rate limit."""
    global _cl_last_request
    with _cl_lock:
        elapsed = time.monotonic() - _cl_last_request
        if elapsed < CL_MIN_INTERVAL:
            time.sleep(CL_MIN_INTERVAL - elapsed)
        _cl_last_request = time.monotonic()


def _throttle_gi() -> None:
    """Enforce GovInfo rate limit."""
    global _gi_last_request
    with _gi_lock:
        elapsed = time.monotonic() - _gi_last_request
        if elapsed < GI_MIN_INTERVAL:
            time.sleep(GI_MIN_INTERVAL - elapsed)
        _gi_last_request = time.monotonic()


# ─── CourtListener ────────────────────────────────────────────────────────

def _cl_headers() -> dict:
    headers = {"Accept": "application/json"}
    if COURTLISTENER_API_TOKEN:
        headers["Authorization"] = f"Token {COURTLISTENER_API_TOKEN}"
    return headers


def _parse_citation_for_search(citation_text: str) -> str:
    """Normalize a citation string for CourtListener search.

    E.g. "325 Or App 648" → "325 Or. App. 648"
         "547 U.S. 813 (2006)" → "547 U.S. 813"
    """
    # Strip year parenthetical
    text = re.sub(r"\s*\(\d{4}\)\s*$", "", citation_text.strip())
    return text


def lookup_citation_courtlistener(citation_text: str) -> LookupResult:
    """Look up a citation via CourtListener's search API."""
    _throttle_cl()
    search_query = _parse_citation_for_search(citation_text)

    try:
        # Use the v3 search endpoint which supports citation queries
        resp = requests.get(
            f"{COURTLISTENER_BASE}/search/",
            params={
                "q": f'"{search_query}"',
                "type": "o",  # opinions
                "format": "json",
            },
            headers=_cl_headers(),
            timeout=30,
        )
        if resp.status_code != 200:
            logger.warning(f"CourtListener search returned {resp.status_code}")
            return LookupResult(found=False, status="error")

        data = resp.json()
        results = data.get("results", [])
        if not results:
            # Try without exact-match quotes for broader search
            _throttle_cl()
            resp = requests.get(
                f"{COURTLISTENER_BASE}/search/",
                params={
                    "q": search_query,
                    "type": "o",
                    "format": "json",
                },
                headers=_cl_headers(),
                timeout=30,
            )
            if resp.status_code == 200:
                data = resp.json()
                results = data.get("results", [])

        if not results:
            return LookupResult(found=False, status="not_found")

        first = results[0]
        cluster_id = first.get("cluster_id") or _extract_id_from_url(first.get("cluster", ""))
        absolute_url = first.get("absolute_url", "")

        result = LookupResult(
            found=True,
            status="found",
            case_name=first.get("caseName", "") or first.get("case_name", ""),
            court=first.get("court", "") or first.get("court_id", ""),
            date_filed=first.get("dateFiled", "") or first.get("date_filed", ""),
            cluster_id=cluster_id,
            url=f"https://www.courtlistener.com{absolute_url}" if absolute_url else "",
            source="courtlistener",
        )

        # Try to fetch the full opinion text for verification
        opinion_id = first.get("id") or _extract_id_from_url(first.get("absolute_url", ""))
        if opinion_id:
            opinion_text = _fetch_opinion_text_v4(opinion_id)
            if opinion_text:
                result.opinion_text = opinion_text
            elif cluster_id:
                # Fallback: try via cluster
                opinion_text = _fetch_opinion_via_cluster(cluster_id)
                if opinion_text:
                    result.opinion_text = opinion_text

        return result

    except requests.RequestException as e:
        logger.warning(f"CourtListener lookup failed: {e}")
        return LookupResult(found=False, status="error")


def _extract_id_from_url(url: str) -> int | None:
    """Extract numeric ID from a CourtListener URL or API URL."""
    if not url:
        return None
    match = re.search(r"/(\d+)/", url)
    if match:
        return int(match.group(1))
    try:
        return int(url)
    except (ValueError, TypeError):
        return None


def _fetch_opinion_text_v4(opinion_id: int) -> str | None:
    """Fetch opinion text using the v4 API."""
    _throttle_cl()
    try:
        resp = requests.get(
            f"https://www.courtlistener.com/api/rest/v4/opinions/{opinion_id}/",
            headers=_cl_headers(),
            timeout=30,
        )
        if resp.status_code != 200:
            return None

        data = resp.json()
        text = data.get("plain_text", "")
        if not text:
            html = data.get("html_with_citations", "") or data.get("html", "")
            if html:
                text = _strip_html(html)
        return text if text and text.strip() else None

    except requests.RequestException as e:
        logger.warning(f"Opinion fetch failed for opinion {opinion_id}: {e}")
        return None


def _fetch_opinion_via_cluster(cluster_id: int) -> str | None:
    """Fetch opinion text by first getting cluster, then its opinions."""
    _throttle_cl()
    try:
        resp = requests.get(
            f"https://www.courtlistener.com/api/rest/v4/clusters/{cluster_id}/",
            headers=_cl_headers(),
            timeout=30,
        )
        if resp.status_code != 200:
            return None

        cluster_data = resp.json()
        sub_opinions = cluster_data.get("sub_opinions", [])
        if not sub_opinions:
            return None

        # Each sub_opinion is a URL string
        opinion_url = sub_opinions[0]
        if isinstance(opinion_url, str) and opinion_url.startswith("http"):
            _throttle_cl()
            resp = requests.get(opinion_url, headers=_cl_headers(), timeout=30)
            if resp.status_code != 200:
                return None
            data = resp.json()
        else:
            oid = _extract_id_from_url(str(opinion_url))
            if not oid:
                return None
            return _fetch_opinion_text_v4(oid)

        text = data.get("plain_text", "")
        if not text:
            html = data.get("html_with_citations", "") or data.get("html", "")
            if html:
                text = _strip_html(html)
        return text if text and text.strip() else None

    except requests.RequestException as e:
        logger.warning(f"Cluster opinion fetch failed for cluster {cluster_id}: {e}")
        return None


def _strip_html(html: str) -> str:
    """Simple HTML tag stripping."""
    text = re.sub(r"<[^>]+>", "", html)
    text = re.sub(r"&nbsp;", " ", text)
    text = re.sub(r"&amp;", "&", text)
    text = re.sub(r"&lt;", "<", text)
    text = re.sub(r"&gt;", ">", text)
    text = re.sub(r"&#\d+;", "", text)
    return text.strip()


# ─── GovInfo ──────────────────────────────────────────────────────────────

def lookup_citation_govinfo(citation_text: str) -> LookupResult:
    """Search GovInfo for a citation as fallback."""
    _throttle_gi()
    try:
        resp = requests.get(
            f"{GOVINFO_BASE}/search",
            params={
                "query": citation_text,
                "pageSize": 3,
                "collection": "USCOURTS",
                "api_key": GOVINFO_API_KEY,
            },
            timeout=15,
        )
        if resp.status_code != 200:
            return LookupResult(found=False, status="not_found")

        data = resp.json()
        results = data.get("results", [])
        if not results:
            return LookupResult(found=False, status="not_found")

        first = results[0]
        return LookupResult(
            found=True,
            status="found",
            case_name=first.get("title", ""),
            court=first.get("court", ""),
            date_filed=first.get("dateIssued", ""),
            url=first.get("detailsLink", ""),
            source="govinfo",
            opinion_text=None,
        )

    except requests.RequestException as e:
        logger.warning(f"GovInfo lookup failed: {e}")
        return LookupResult(found=False, status="error")


# ─── Unified Lookup ───────────────────────────────────────────────────────

def lookup_citation(citation_text: str) -> LookupResult:
    """Look up a citation, trying CourtListener first then GovInfo."""
    result = lookup_citation_courtlistener(citation_text)
    if result.found:
        return result

    # Fallback to GovInfo
    return lookup_citation_govinfo(citation_text)

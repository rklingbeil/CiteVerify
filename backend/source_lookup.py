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
    CL_MIN_INTERVAL,
    GOVINFO_API_KEY,
    GOVINFO_BASE,
    GI_MIN_INTERVAL,
)

logger = logging.getLogger(__name__)

CL_V3 = "https://www.courtlistener.com/api/rest/v3"
CL_V4 = "https://www.courtlistener.com/api/rest/v4"


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
    global _cl_last_request
    with _cl_lock:
        elapsed = time.monotonic() - _cl_last_request
        if elapsed < CL_MIN_INTERVAL:
            time.sleep(CL_MIN_INTERVAL - elapsed)
        _cl_last_request = time.monotonic()


def _throttle_gi() -> None:
    global _gi_last_request
    with _gi_lock:
        elapsed = time.monotonic() - _gi_last_request
        if elapsed < GI_MIN_INTERVAL:
            time.sleep(GI_MIN_INTERVAL - elapsed)
        _gi_last_request = time.monotonic()


# ─── Citation Parsing ─────────────────────────────────────────────────────

# Pattern: volume reporter page, e.g. "501 U.S. 32" or "464 F.3d 951"
_CITE_RE = re.compile(
    r"(\d+)\s+"                          # volume
    r"((?:U\.S\.|S\.\s*Ct\.|L\.\s*Ed\.\s*2d|F\.\d[a-z]*|F\.\s*Supp\.\s*\d*[a-z]*"
    r"|So\.\s*\d*[a-z]*|N\.E\.\s*\d*[a-z]*|N\.W\.\s*\d*[a-z]*|S\.E\.\s*\d*[a-z]*"
    r"|S\.W\.\s*\d*[a-z]*|P\.\d*[a-z]*|A\.\d*[a-z]*|Cal\.\s*Rptr\.\s*\d*[a-z]*"
    r"|Or\.?\s*App\.?|Or\.?|Wn\.\s*\d*[a-z]*|Wash\.\s*\d*[a-z]*"
    r"|[A-Z][a-z]*\.(?:\s*[A-Z][a-z]*\.)*)\s*)"  # reporter
    r"(\d+)"                              # starting page
)


def _parse_base_citation(citation_text: str) -> tuple[str, str, str] | None:
    """Extract (volume, reporter, page) from a citation string.

    Returns None if it's not a recognizable reporter citation.
    """
    # Strip parenthetical year and "at" pinpoints
    text = citation_text.strip()
    text = re.sub(r"\s*\(.*?\)\s*$", "", text)
    text = re.sub(r",?\s+at\s+\d+.*$", "", text)
    text = re.sub(r",\s*\d+\s*$", "", text)  # trailing pinpoint like ", 603"

    match = _CITE_RE.search(text)
    if match:
        return match.group(1), match.group(2).strip(), match.group(3)
    return None


def _make_citation_query(citation_text: str) -> str:
    """Build the best search query from a citation string."""
    parsed = _parse_base_citation(citation_text)
    if parsed:
        vol, reporter, page = parsed
        return f"{vol} {reporter} {page}"
    # Fallback: clean up the raw text
    text = re.sub(r"\s*\(.*?\)\s*$", "", citation_text.strip())
    text = re.sub(r",?\s+at\s+\d+.*$", "", text)
    return text


# ─── CourtListener ────────────────────────────────────────────────────────

def _cl_headers() -> dict:
    headers = {"Accept": "application/json"}
    if COURTLISTENER_API_TOKEN:
        headers["Authorization"] = f"Token {COURTLISTENER_API_TOKEN}"
    return headers


def lookup_citation_courtlistener(
    citation_text: str,
    case_name: str = "",
) -> LookupResult:
    """Look up a citation via CourtListener search API.

    Tries three strategies:
    1. Search with `citation` parameter (best for standard reporter cites)
    2. Free-text search with the citation string
    3. Case name search as last resort
    """
    cite_query = _make_citation_query(citation_text)

    # Strategy 1: Use the citation parameter
    result = _cl_search(citation=cite_query)
    if result:
        return result

    # Strategy 2: Free-text search with citation
    result = _cl_search(q=f'"{cite_query}"')
    if result:
        return result

    # Strategy 3: Search by case name
    if case_name:
        # Clean case name for search
        clean_name = re.sub(r"\s+v\.?\s+", " v. ", case_name)
        result = _cl_search(q=f'"{clean_name}"')
        if result:
            return result

    return LookupResult(found=False, status="not_found")


def _cl_search(
    q: str = "",
    citation: str = "",
) -> LookupResult | None:
    """Execute a CourtListener search and return first match, or None."""
    _throttle_cl()
    try:
        params: dict[str, str] = {"type": "o", "format": "json"}
        if citation:
            params["citation"] = citation
        if q:
            params["q"] = q

        resp = requests.get(
            f"{CL_V3}/search/",
            params=params,
            headers=_cl_headers(),
            timeout=30,
        )

        if resp.status_code != 200:
            logger.warning(f"CourtListener search returned {resp.status_code} for params={params}")
            return None

        data = resp.json()
        results = data.get("results", [])
        if not results:
            return None

        first = results[0]
        cluster_id = first.get("cluster_id")
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

        # Fetch full opinion text for verification
        opinion_id = first.get("id")
        if opinion_id:
            text = _fetch_opinion_text(opinion_id)
            if text:
                result.opinion_text = text
                return result

        # Fallback: get opinion via cluster
        if cluster_id:
            text = _fetch_opinion_via_cluster(cluster_id)
            if text:
                result.opinion_text = text

        return result

    except requests.RequestException as e:
        logger.warning(f"CourtListener search failed: {e}")
        return None


def _fetch_opinion_text(opinion_id: int) -> str | None:
    """Fetch opinion text from v4 API."""
    _throttle_cl()
    try:
        resp = requests.get(
            f"{CL_V4}/opinions/{opinion_id}/",
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
        logger.warning(f"Opinion fetch failed for {opinion_id}: {e}")
        return None


def _fetch_opinion_via_cluster(cluster_id: int) -> str | None:
    """Fetch opinion text through cluster → sub_opinions."""
    _throttle_cl()
    try:
        resp = requests.get(
            f"{CL_V4}/clusters/{cluster_id}/",
            headers=_cl_headers(),
            timeout=30,
        )
        if resp.status_code != 200:
            return None

        sub_opinions = resp.json().get("sub_opinions", [])
        if not sub_opinions:
            return None

        # Each sub_opinion is a URL
        opinion_url = sub_opinions[0]
        if isinstance(opinion_url, str) and opinion_url.startswith("http"):
            _throttle_cl()
            resp = requests.get(opinion_url, headers=_cl_headers(), timeout=30)
            if resp.status_code != 200:
                return None
            data = resp.json()
            text = data.get("plain_text", "")
            if not text:
                html = data.get("html_with_citations", "") or data.get("html", "")
                if html:
                    text = _strip_html(html)
            return text if text and text.strip() else None
        else:
            oid = _extract_id_from_url(str(opinion_url))
            if oid:
                return _fetch_opinion_text(oid)
            return None

    except requests.RequestException as e:
        logger.warning(f"Cluster fetch failed for {cluster_id}: {e}")
        return None


def _extract_id_from_url(url: str) -> int | None:
    if not url:
        return None
    match = re.search(r"/(\d+)/", url)
    if match:
        return int(match.group(1))
    try:
        return int(url)
    except (ValueError, TypeError):
        return None


def _strip_html(html: str) -> str:
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

def lookup_citation(citation_text: str, case_name: str = "") -> LookupResult:
    """Look up a citation, trying CourtListener first then GovInfo."""
    result = lookup_citation_courtlistener(citation_text, case_name=case_name)
    if result.found:
        return result

    return lookup_citation_govinfo(citation_text)

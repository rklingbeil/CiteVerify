"""Case law source lookup — CourtListener V4 API and GovInfo."""

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

def _clean_citation_for_search(citation_text: str) -> str:
    """Clean a citation string for use in CourtListener search.

    Strips 'at' pinpoints, trailing pinpoints, and year parentheticals.
    """
    text = citation_text.strip()
    # Remove "at XXX" pinpoints
    text = re.sub(r",?\s+at\s+\d+[\–\-–]?\d*", "", text)
    # Remove trailing pinpoint ", 603" or ", 603-605"
    text = re.sub(r",\s*\d+[\–\-–]?\d*\s*$", "", text)
    # Remove year parenthetical at end
    text = re.sub(r"\s*\([^)]*\d{4}[^)]*\)\s*$", "", text)
    return text.strip()


# ─── CourtListener V4 ────────────────────────────────────────────────────

def _cl_headers() -> dict:
    headers = {"Accept": "application/json"}
    if COURTLISTENER_API_TOKEN:
        headers["Authorization"] = f"Token {COURTLISTENER_API_TOKEN}"
    return headers


def lookup_citation_courtlistener(
    citation_text: str,
    case_name: str = "",
) -> LookupResult:
    """Look up a citation via CourtListener V4 search API.

    Tries multiple search strategies:
    1. Quoted citation text search (e.g. "501 U.S. 32")
    2. Combined case name + citation search
    3. Case name search alone
    4. Unquoted citation search (broader)
    """
    clean_cite = _clean_citation_for_search(citation_text)

    # Strategy 1: Quoted citation text
    result = _cl_v4_search(q=f'"{clean_cite}"')
    if result and result.found:
        return result

    # Strategy 2: Combined case name + citation
    if case_name:
        clean_name = case_name.strip()
        # Try with both
        result = _cl_v4_search(q=f'"{clean_name}" "{clean_cite}"')
        if result and result.found:
            return result

    # Strategy 3: Case name alone (quoted)
    if case_name:
        # Use short form for search — just the party names
        clean_name = re.sub(r"\s+v\.?\s+", " v. ", case_name.strip())
        result = _cl_v4_search(q=f'"{clean_name}"')
        if result and result.found:
            return result

    # Strategy 4: Unquoted citation (broadest)
    result = _cl_v4_search(q=clean_cite)
    if result and result.found:
        return result

    return LookupResult(found=False, status="not_found")


def _cl_v4_search(q: str) -> LookupResult | None:
    """Execute a CourtListener V4 opinion search and return first match."""
    _throttle_cl()
    try:
        resp = requests.get(
            f"{CL_V4}/search/",
            params={"type": "o", "q": q},
            headers=_cl_headers(),
            timeout=30,
        )

        if resp.status_code != 200:
            logger.warning(f"CourtListener V4 search returned {resp.status_code} for q={q!r}")
            return None

        data = resp.json()
        results = data.get("results", [])
        if not results:
            return None

        first = results[0]
        cluster_id = first.get("cluster_id")
        absolute_url = first.get("absolute_url", "")
        citations = first.get("citation", [])

        result = LookupResult(
            found=True,
            status="found",
            case_name=first.get("caseName", "") or first.get("caseNameFull", ""),
            court=first.get("court", "") or first.get("court_id", ""),
            date_filed=first.get("dateFiled", ""),
            cluster_id=cluster_id,
            url=f"https://www.courtlistener.com{absolute_url}" if absolute_url else "",
            source="courtlistener",
        )

        # Get opinion text from the opinions embedded in search results
        opinions = first.get("opinions", [])
        if opinions:
            # Try each opinion for text
            for op in opinions:
                opinion_id = op.get("id")
                if opinion_id:
                    text = _fetch_opinion_text(opinion_id)
                    if text:
                        result.opinion_text = text
                        result.opinion_id = opinion_id
                        break

        # Fallback: try via cluster sub_opinions
        if not result.opinion_text and cluster_id:
            text, oid = _fetch_opinion_via_cluster(cluster_id)
            if text:
                result.opinion_text = text
                result.opinion_id = oid

        return result

    except requests.RequestException as e:
        logger.warning(f"CourtListener V4 search failed: {e}")
        return None


def _fetch_opinion_text(opinion_id: int) -> str | None:
    """Fetch full opinion text from V4 opinions endpoint."""
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
        # Prefer plain_text, then html_with_citations, then html
        text = data.get("plain_text", "")
        if not text or not text.strip():
            html = data.get("html_with_citations", "") or data.get("html", "")
            if html:
                text = _strip_html(html)
        return text if text and text.strip() else None

    except requests.RequestException as e:
        logger.warning(f"Opinion fetch failed for {opinion_id}: {e}")
        return None


def _fetch_opinion_via_cluster(cluster_id: int) -> tuple[str | None, int | None]:
    """Fetch opinion text through cluster → sub_opinions. Returns (text, opinion_id)."""
    _throttle_cl()
    try:
        resp = requests.get(
            f"{CL_V4}/clusters/{cluster_id}/",
            headers=_cl_headers(),
            timeout=30,
        )
        if resp.status_code != 200:
            return None, None

        sub_opinions = resp.json().get("sub_opinions", [])
        if not sub_opinions:
            return None, None

        # Try each sub_opinion URL
        for opinion_url in sub_opinions:
            if isinstance(opinion_url, str) and opinion_url.startswith("http"):
                _throttle_cl()
                resp2 = requests.get(opinion_url, headers=_cl_headers(), timeout=30)
                if resp2.status_code != 200:
                    continue
                data = resp2.json()
                oid = data.get("id")
                text = data.get("plain_text", "")
                if not text or not text.strip():
                    html = data.get("html_with_citations", "") or data.get("html", "")
                    if html:
                        text = _strip_html(html)
                if text and text.strip():
                    return text, oid
            else:
                oid = _extract_id_from_url(str(opinion_url))
                if oid:
                    text = _fetch_opinion_text(oid)
                    if text:
                        return text, oid

        return None, None

    except requests.RequestException as e:
        logger.warning(f"Cluster fetch failed for {cluster_id}: {e}")
        return None, None


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
    """Strip HTML tags and decode common entities."""
    text = re.sub(r"<[^>]+>", "", html)
    text = re.sub(r"&nbsp;", " ", text)
    text = re.sub(r"&amp;", "&", text)
    text = re.sub(r"&lt;", "<", text)
    text = re.sub(r"&gt;", ">", text)
    text = re.sub(r"&[a-z]+;", "", text)
    text = re.sub(r"&#\d+;", "", text)
    # Collapse excessive whitespace
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


# ─── GovInfo ──────────────────────────────────────────────────────────────

def lookup_citation_govinfo(citation_text: str, case_name: str = "") -> LookupResult:
    """Search GovInfo for a citation as fallback (POST endpoint).

    GovInfo is a document search engine, not a citation resolver — it finds
    documents that *mention* a citation rather than the case itself.
    Still useful as a secondary confirmation of existence.
    """
    _throttle_gi()
    # Build a focused query — use case name if available
    query = f'"{case_name}"' if case_name else citation_text

    try:
        resp = requests.post(
            f"{GOVINFO_BASE}/search",
            params={"api_key": GOVINFO_API_KEY},
            json={
                "query": query,
                "pageSize": 5,
                "offsetMark": "*",
            },
            timeout=15,
        )
        if resp.status_code != 200:
            return LookupResult(found=False, status="not_found")

        data = resp.json()
        results = data.get("results", [])
        if not results:
            return LookupResult(found=False, status="not_found")

        # Try to find a result whose title matches the case name
        best = results[0]
        if case_name:
            name_lower = case_name.lower()
            for r in results:
                title = r.get("title", "").lower()
                # Check if the case name parties appear in the title
                parties = re.split(r"\s+v\.?\s+", name_lower)
                if any(p.strip() in title for p in parties if len(p.strip()) > 3):
                    best = r
                    break

        # Build a link to the PDF if available
        pdf_link = best.get("download", {}).get("pdfLink", "")
        result_link = best.get("resultLink", "")

        return LookupResult(
            found=True,
            status="found",
            case_name=best.get("title", ""),
            court=", ".join(best.get("governmentAuthor", [])),
            date_filed=best.get("dateIssued", ""),
            url=pdf_link or result_link,
            source="govinfo",
            opinion_text=None,  # GovInfo doesn't provide raw opinion text via API
        )

    except requests.RequestException as e:
        logger.warning(f"GovInfo lookup failed: {e}")
        return LookupResult(found=False, status="error")


# ─── Unified Lookup ───────────────────────────────────────────────────────

def lookup_citation(citation_text: str, case_name: str = "") -> LookupResult:
    """Look up a citation, trying CourtListener V4 first then GovInfo."""
    result = lookup_citation_courtlistener(citation_text, case_name=case_name)
    if result.found:
        return result

    return lookup_citation_govinfo(citation_text)

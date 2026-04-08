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


def lookup_citation_courtlistener(citation_text: str) -> LookupResult:
    """Look up a citation via CourtListener's citation-lookup API."""
    _throttle_cl()
    try:
        resp = requests.post(
            f"{COURTLISTENER_BASE}/citation-lookup/",
            json={"text": citation_text},
            headers=_cl_headers(),
            timeout=30,
        )
        if resp.status_code != 200:
            logger.warning(f"CourtListener citation-lookup returned {resp.status_code}")
            return LookupResult(found=False, status="error")

        data = resp.json()
        if not data:
            return LookupResult(found=False, status="not_found")

        # The response is a list of matched citations
        # Each entry has cluster_url, case_name, etc.
        first = data[0] if isinstance(data, list) else data
        cluster_url = first.get("cluster_url", "") or first.get("absolute_url", "")
        cluster_id = _extract_cluster_id(cluster_url)

        result = LookupResult(
            found=True,
            status="found",
            case_name=first.get("case_name", ""),
            court=first.get("court", ""),
            date_filed=first.get("date_filed", ""),
            cluster_id=cluster_id,
            url=f"https://www.courtlistener.com{first.get('absolute_url', '')}",
            source="courtlistener",
        )

        # Try to fetch the full opinion text
        if cluster_id:
            opinion_text = _fetch_opinion_text(cluster_id)
            if opinion_text:
                result.opinion_text = opinion_text

        return result

    except requests.RequestException as e:
        logger.warning(f"CourtListener lookup failed: {e}")
        return LookupResult(found=False, status="error")


def _extract_cluster_id(url: str) -> int | None:
    """Extract cluster ID from a CourtListener URL."""
    match = re.search(r"/clusters?/(\d+)/", url)
    if match:
        return int(match.group(1))
    # Also try from opinion URL
    match = re.search(r"/opinion/(\d+)/", url)
    if match:
        return int(match.group(1))
    return None


def _fetch_opinion_text(cluster_id: int) -> str | None:
    """Fetch the full opinion text for a cluster from CourtListener."""
    _throttle_cl()
    try:
        # Get cluster to find opinion IDs
        resp = requests.get(
            f"{COURTLISTENER_BASE}/clusters/{cluster_id}/",
            headers=_cl_headers(),
            timeout=30,
        )
        if resp.status_code != 200:
            return None

        cluster_data = resp.json()
        opinion_urls = cluster_data.get("sub_opinions", [])
        if not opinion_urls:
            return None

        # Fetch the first (lead) opinion
        opinion_url = opinion_urls[0]
        if isinstance(opinion_url, str) and opinion_url.startswith("http"):
            _throttle_cl()
            resp = requests.get(
                opinion_url,
                headers=_cl_headers(),
                timeout=30,
            )
        else:
            opinion_id = _extract_opinion_id(str(opinion_url))
            if not opinion_id:
                return None
            _throttle_cl()
            resp = requests.get(
                f"{COURTLISTENER_BASE}/opinions/{opinion_id}/",
                headers=_cl_headers(),
                timeout=30,
            )

        if resp.status_code != 200:
            return None

        opinion_data = resp.json()
        # Prefer plain_text, fall back to html (strip tags)
        text = opinion_data.get("plain_text", "")
        if not text:
            html = opinion_data.get("html_with_citations", "") or opinion_data.get("html", "")
            if html:
                text = _strip_html(html)

        return text if text.strip() else None

    except requests.RequestException as e:
        logger.warning(f"Opinion fetch failed for cluster {cluster_id}: {e}")
        return None


def _extract_opinion_id(url: str) -> int | None:
    """Extract opinion ID from a URL or string."""
    match = re.search(r"/opinions?/(\d+)/", url)
    if match:
        return int(match.group(1))
    # Try bare integer
    try:
        return int(url)
    except (ValueError, TypeError):
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
        resp = requests.post(
            f"{GOVINFO_BASE}/search",
            json={
                "query": citation_text,
                "pageSize": 3,
                "collection": "USCOURTS",
            },
            params={"api_key": GOVINFO_API_KEY},
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
            # GovInfo doesn't provide full opinion text easily
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

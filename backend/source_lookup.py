"""Case law source lookup — CourtListener V4 API and GovInfo.

Lookup strategy (in order):
1. CourtListener Citation Lookup API — purpose-built citation resolver using Eyecite
2. CourtListener V4 Search API — broader keyword/case name search as fallback
3. GovInfo API — secondary confirmation for federal cases
"""

import html as html_module
import logging
import re
import threading
import time
from dataclasses import dataclass, field
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

# Opinion types that represent the majority/controlling opinion
_MAJORITY_OPINION_TYPES = {"010combined", "015unanamous", "020lead", "025plurality"}


# ─── Legal Name Abbreviation Normalization ────────────────────────────────
# Bluebook Table T6 abbreviations + common variants.  Keys are the stripped
# form (periods and apostrophes removed, lowercased).  Both sides of a name
# comparison are normalized so "Int'l" and "International" both become
# "international".

_LEGAL_ABBREVS: dict[str, str] = {
    # Entity types
    "corp": "corporation",
    "co": "company",
    "inc": "incorporated",
    "ltd": "limited",
    "llc": "llc",
    "llp": "llp",
    "lp": "lp",
    "plc": "plc",
    # Apostrophe abbreviations (stripped form)
    "intl": "international",
    "natl": "national",
    "dept": "department",
    "assn": "association",
    "govt": "government",
    "commn": "commission",
    "commr": "commissioner",
    "secy": "secretary",
    "socy": "society",
    "envtl": "environmental",
    # Standard abbreviations
    "bros": "brothers",
    "rr": "railroad",
    "ry": "railway",
    "mfg": "manufacturing",
    "univ": "university",
    "sys": "systems",
    "servs": "services",
    "serv": "service",
    "techs": "technologies",
    "tech": "technology",
    "indus": "industries",
    "hosp": "hospital",
    "hosps": "hospitals",
    "elec": "electric",
    "ins": "insurance",
    "ctr": "center",
    "cnty": "county",
    "twp": "township",
    "sch": "school",
    "schs": "schools",
    "bd": "board",
    "admin": "administration",
    "transp": "transportation",
    "tel": "telephone",
    "telecomm": "telecommunications",
    "telecomms": "telecommunications",
    "pharm": "pharmaceutical",
    "pharms": "pharmaceuticals",
    "med": "medical",
    "sav": "savings",
    "fin": "financial",
    "auth": "authority",
    "reg": "regional",
    "regl": "regional",
    "mgmt": "management",
    "mut": "mutual",
    "auto": "automobile",
    "props": "properties",
    "prop": "property",
    "dev": "development",
    "constr": "construction",
    "eng": "engineering",
    "engrs": "engineers",
    "fed": "federal",
    "gen": "general",
    "dist": "district",
    "div": "division",
    "prods": "products",
    "prod": "product",
    "lab": "laboratory",
    "labs": "laboratories",
    "grp": "group",
    "assocs": "associates",
    "assoc": "associates",
    "amer": "american",
    "pac": "pacific",
    "atl": "atlantic",
    "nw": "northwest",
    "ne": "northeast",
    "sw": "southwest",
    "se": "southeast",
    "fsb": "federal savings bank",
    "na": "national association",
}


def normalize_legal_name(name: str) -> str:
    """Normalize a legal entity/case name by expanding standard abbreviations.

    Strips periods and apostrophes, lowercases, then expands Bluebook
    abbreviations to their full forms.  This allows "Int'l" and
    "International" to compare as equal.
    """
    if not name:
        return ""
    # Remove periods and apostrophes, lowercase
    normalized = name.lower().replace(".", "").replace("'", "").replace("\u2019", "")
    # Split into words and expand abbreviations
    words = normalized.split()
    expanded = []
    for word in words:
        clean = word.strip(",;:()")
        replacement = _LEGAL_ABBREVS.get(clean)
        if replacement:
            # Replace the clean form within the word (preserving any surrounding punctuation)
            expanded.append(word.replace(clean, replacement))
        else:
            expanded.append(word)
    return " ".join(expanded)


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
    actual_citations: list[str] = field(default_factory=list)  # Known citations for this case


# ─── Rate Limiting ────────────────────────────────────────────────────────

_cl_lock = threading.Lock()
_cl_last_request = 0.0
_gi_lock = threading.Lock()
_gi_last_request = 0.0


def _throttle_cl() -> None:
    global _cl_last_request
    wait = 0.0
    with _cl_lock:
        elapsed = time.monotonic() - _cl_last_request
        if elapsed < CL_MIN_INTERVAL:
            wait = CL_MIN_INTERVAL - elapsed
        # Reserve our slot immediately so other threads see the updated timestamp
        _cl_last_request = time.monotonic() + wait
    if wait > 0:
        time.sleep(wait)


def _throttle_gi() -> None:
    global _gi_last_request
    wait = 0.0
    with _gi_lock:
        elapsed = time.monotonic() - _gi_last_request
        if elapsed < GI_MIN_INTERVAL:
            wait = GI_MIN_INTERVAL - elapsed
        _gi_last_request = time.monotonic() + wait
    if wait > 0:
        time.sleep(wait)


# ─── Citation Parsing ─────────────────────────────────────────────────────

_CITE_PATTERN = re.compile(
    r"^(\d+)\s+"                        # volume
    r"([A-Za-z][A-Za-z0-9. ]*[A-Za-z.])"  # reporter — greedy, ends at last letter/period before whitespace+digit
    r"\s+(\d+)"                         # page
)


def _parse_citation_parts(citation_text: str) -> tuple[str, str, str] | None:
    """Try to extract volume, reporter, page from a citation string.

    Returns (volume, reporter, page) or None if not parseable.
    """
    text = _clean_citation_for_search(citation_text)
    m = _CITE_PATTERN.match(text)
    if m:
        return m.group(1), m.group(2).strip(), m.group(3)
    return None


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


def _names_plausibly_match(extracted_name: str, lookup_name: str) -> bool:
    """Quick check if two case names plausibly refer to the same case.

    Used to reject obviously wrong results from broad searches.
    Uses word-boundary matching to avoid false positives.
    """
    if not extracted_name or not lookup_name:
        return True
    ext = normalize_legal_name(extracted_name)
    lkp = normalize_legal_name(lookup_name)
    if ext == lkp:
        return True
    # Check if any substantial party word appears in both (word-boundary)
    lkp_words = set(lkp.split())
    ext_parties = re.split(r'\s+v\s+', ext)
    for party in ext_parties:
        words = [w for w in party.strip().split() if len(w) > 3]
        if words and any(w in lkp_words for w in words[:2]):
            return True
    return False


# ─── Citation Lookup API (primary) ────────────────────────────────────────

def _cl_citation_lookup(citation_text: str, case_name: str = "") -> LookupResult | None:
    """Look up a citation via CourtListener's Citation Lookup API.

    This is the purpose-built citation resolver using Eyecite (trained on 55M+
    citations). It parses, normalizes, and matches citations against CourtListener's
    database of ~50 million citations.

    Tries two modes:
    1. Direct lookup (volume/reporter/page) — most precise
    2. Text mode — sends the full citation string for Eyecite to parse

    Returns:
        200 = found (exactly one cluster matched)
        300 = ambiguous (multiple possible matches) — still returns clusters
        400 = unrecognized reporter
        404 = valid reporter but case not in database
    """
    if not COURTLISTENER_API_TOKEN:
        logger.warning("No CourtListener API token — skipping citation lookup (auth required)")
        return None

    # Mode 1: Direct volume/reporter/page lookup (most precise)
    parts = _parse_citation_parts(citation_text)
    if parts:
        volume, reporter, page = parts
        result = _cl_citation_lookup_direct(volume, reporter, page, case_name=case_name)
        if result:
            return result

    # Mode 2: Text mode — let Eyecite parse the citation
    clean = _clean_citation_for_search(citation_text)
    if clean:
        result = _cl_citation_lookup_text(clean, case_name=case_name)
        if result:
            return result

    return None


def _cl_citation_lookup_direct(volume: str, reporter: str, page: str, case_name: str = "") -> LookupResult | None:
    """Citation Lookup API — direct volume/reporter/page mode."""
    _throttle_cl()
    try:
        resp = requests.post(
            f"{CL_V4}/citation-lookup/",
            headers=_cl_headers(),
            data={"volume": volume, "reporter": reporter, "page": page},
            timeout=30,
        )
        if resp.status_code == 401:
            logger.warning("CourtListener citation-lookup: 401 Unauthorized")
            return None
        if resp.status_code != 200:
            logger.debug(f"Citation lookup direct returned {resp.status_code}")
            return None

        return _parse_citation_lookup_response(resp.json(), case_name=case_name)

    except requests.RequestException as e:
        logger.warning(f"Citation lookup direct failed: {e}")
        return None


def _cl_citation_lookup_text(text: str, case_name: str = "") -> LookupResult | None:
    """Citation Lookup API — text mode (Eyecite parsing)."""
    _throttle_cl()
    try:
        resp = requests.post(
            f"{CL_V4}/citation-lookup/",
            headers=_cl_headers(),
            data={"text": text},
            timeout=30,
        )
        if resp.status_code == 401:
            logger.warning("CourtListener citation-lookup: 401 Unauthorized")
            return None
        if resp.status_code != 200:
            logger.debug(f"Citation lookup text returned {resp.status_code}")
            return None

        return _parse_citation_lookup_response(resp.json(), case_name=case_name)

    except requests.RequestException as e:
        logger.warning(f"Citation lookup text failed: {e}")
        return None


def _parse_citation_lookup_response(data: list, case_name: str = "") -> LookupResult | None:
    """Parse the Citation Lookup API response array into a LookupResult.

    The response is a JSON array of citation results. Each has:
    - status: 200 (found), 300 (ambiguous), 400 (bad reporter), 404 (not found)
    - clusters: array of matching OpinionCluster objects
    """
    if not data or not isinstance(data, list):
        return None

    # Find the first citation result that has clusters
    for item in data:
        status = item.get("status", 0)
        clusters = item.get("clusters", [])

        # Skip bad reporters and not-found
        if status == 400:
            logger.debug(f"Citation lookup: unrecognized reporter — {item.get('error_message', '')}")
            continue
        if status == 404:
            logger.debug(f"Citation lookup: not found — {item.get('citation', '')}")
            continue

        if not clusters:
            continue

        # For ambiguous results (300), try to match by case name
        cluster = clusters[0]
        if status == 300 and case_name and len(clusters) > 1:
            for candidate in clusters:
                cand_name = candidate.get("case_name", "") or candidate.get("case_name_full", "")
                if _names_plausibly_match(case_name, cand_name):
                    cluster = candidate
                    logger.info(
                        f"Ambiguous citation: selected '{cand_name}' from {len(clusters)} candidates"
                    )
                    break
        cluster_id = cluster.get("id")
        absolute_url = cluster.get("absolute_url", "")

        result = LookupResult(
            found=True,
            status="found",
            case_name=cluster.get("case_name", "") or cluster.get("case_name_full", ""),
            court=cluster.get("court", "") or cluster.get("court_id", ""),
            date_filed=cluster.get("date_filed", ""),
            cluster_id=cluster_id,
            url=f"https://www.courtlistener.com{absolute_url}" if absolute_url else "",
            source="courtlistener",
        )

        # Extract known citations from cluster data
        cluster_cites = cluster.get("citations", [])
        if cluster_cites:
            result.actual_citations = _format_cluster_citations(cluster_cites)

        # Fetch opinion text via sub_opinions, preferring majority opinion
        sub_opinions = cluster.get("sub_opinions", [])
        if sub_opinions:
            text, oid = _fetch_best_opinion_from_urls(sub_opinions)
            if text:
                result.opinion_text = text
                result.opinion_id = oid

        # Fallback: fetch via cluster endpoint
        if not result.opinion_text and cluster_id:
            text, oid, cluster_cites = _fetch_opinion_via_cluster(cluster_id)
            if text:
                result.opinion_text = text
                result.opinion_id = oid
            if cluster_cites and not result.actual_citations:
                result.actual_citations = cluster_cites

        logger.info(
            f"Citation lookup found: {result.case_name} (cluster {cluster_id}, "
            f"{'with' if result.opinion_text else 'no'} opinion text)"
        )
        return result

    return None


# ─── CourtListener V4 Search (fallback) ──────────────────────────────────

def lookup_citation_courtlistener(
    citation_text: str,
    case_name: str = "",
) -> LookupResult:
    """Look up a citation via CourtListener V4.

    Strategy order:
    1. Citation Lookup API (purpose-built, uses Eyecite) — most accurate
    2. Search API with quoted citation text
    3. Search API with case name + citation
    4. Search API with case name alone
    5. Search API with unquoted citation (broadest)
    """
    # Strategy 1: Citation Lookup API (primary)
    result = _cl_citation_lookup(citation_text, case_name=case_name)
    if result and result.found:
        return result

    clean_cite = _clean_citation_for_search(citation_text)

    # Strategy 2: Quoted citation text search
    result = _cl_v4_search(q=f'"{clean_cite}"')
    if result and result.found:
        return result

    # Strategy 3: Combined case name + citation
    if case_name:
        clean_name = case_name.strip()
        result = _cl_v4_search(q=f'"{clean_name}" "{clean_cite}"')
        if result and result.found:
            return result

    # Strategy 4: Case name alone (quoted)
    if case_name:
        clean_name = re.sub(r"\s+v\.?\s+", " v. ", case_name.strip())
        result = _cl_v4_search(q=f'"{clean_name}"')
        if result and result.found:
            return result

    # Strategy 5: Unquoted citation (broadest) — validate case name to avoid false positives
    result = _cl_v4_search(q=clean_cite)
    if result and result.found:
        if not case_name or _names_plausibly_match(case_name, result.case_name):
            return result
        logger.info(
            f"Strategy 5 returned '{result.case_name}' for query '{case_name}' — "
            f"name mismatch, discarding broad search result"
        )

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

        if resp.status_code == 429:
            logger.warning(f"CourtListener V4 rate limited for q={q!r}, waiting 5s")
            time.sleep(5)
            return None
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

        result.actual_citations = [c for c in citations if isinstance(c, str)]

        # Get opinion text, preferring majority opinions
        opinions = first.get("opinions", [])
        if opinions:
            fallback_text, fallback_oid = None, None
            for op in opinions:
                opinion_id = op.get("id")
                if opinion_id:
                    text, opinion_type = _fetch_opinion_data(opinion_id)
                    if text:
                        if opinion_type in _MAJORITY_OPINION_TYPES or not opinion_type:
                            result.opinion_text = text
                            result.opinion_id = opinion_id
                            break
                        elif fallback_text is None:
                            fallback_text = text
                            fallback_oid = opinion_id
            else:
                if fallback_text:
                    result.opinion_text = fallback_text
                    result.opinion_id = fallback_oid

        # Fallback: try via cluster sub_opinions
        if not result.opinion_text and cluster_id:
            text, oid, cluster_cites = _fetch_opinion_via_cluster(cluster_id)
            if text:
                result.opinion_text = text
                result.opinion_id = oid
            if cluster_cites and not result.actual_citations:
                result.actual_citations = cluster_cites

        return result

    except requests.RequestException as e:
        logger.warning(f"CourtListener V4 search failed: {e}")
        return None


def _fetch_opinion_data(opinion_id: int) -> tuple[str | None, str]:
    """Fetch opinion text and type from V4 opinions endpoint.

    Returns (text, type_str) where type_str is the CourtListener opinion type
    (e.g., '020lead', '040dissent', '010combined').
    """
    _throttle_cl()
    try:
        resp = requests.get(
            f"{CL_V4}/opinions/{opinion_id}/",
            headers=_cl_headers(),
            timeout=30,
        )
        if resp.status_code != 200:
            return None, ""

        data = resp.json()
        opinion_type = data.get("type", "")
        # Prefer plain_text, then html_with_citations, then html
        text = data.get("plain_text", "")
        if not text or not text.strip():
            html = data.get("html_with_citations", "") or data.get("html", "")
            if html:
                text = _strip_html(html)
        return (text if text and text.strip() else None), opinion_type

    except requests.RequestException as e:
        logger.warning(f"Opinion fetch failed for {opinion_id}: {e}")
        return None, ""


def _fetch_opinion_text(opinion_id: int) -> str | None:
    """Fetch full opinion text from V4 opinions endpoint."""
    text, _ = _fetch_opinion_data(opinion_id)
    return text


def _fetch_best_opinion_from_urls(opinion_urls: list) -> tuple[str | None, int | None]:
    """Fetch the best opinion from a list of URLs, preferring majority opinions.

    CourtListener opinion types: 010combined, 015unanamous (sic), 020lead,
    025plurality are majority. 030concurrence, 040dissent, etc. are secondary.
    """
    fallback_text = None
    fallback_oid = None

    for opinion_url in opinion_urls:
        if isinstance(opinion_url, str) and opinion_url.startswith("http"):
            oid = _extract_id_from_url(opinion_url)
        else:
            oid = _extract_id_from_url(str(opinion_url))
        if not oid:
            continue

        text, opinion_type = _fetch_opinion_data(oid)
        if text:
            if opinion_type in _MAJORITY_OPINION_TYPES or not opinion_type:
                logger.debug(f"Using majority opinion {oid} (type={opinion_type!r})")
                return text, oid
            elif fallback_text is None:
                logger.debug(f"Non-majority opinion {oid} (type={opinion_type!r}), searching for majority")
                fallback_text = text
                fallback_oid = oid

    if fallback_text:
        logger.info(f"No majority opinion found, using fallback opinion {fallback_oid}")
    return fallback_text, fallback_oid


def _fetch_opinion_via_cluster(cluster_id: int) -> tuple[str | None, int | None, list[str]]:
    """Fetch opinion text through cluster → sub_opinions, and extract known citations."""
    _throttle_cl()
    try:
        resp = requests.get(
            f"{CL_V4}/clusters/{cluster_id}/",
            headers=_cl_headers(),
            timeout=30,
        )
        if resp.status_code != 200:
            return None, None, []

        data = resp.json()
        cluster_cites = _format_cluster_citations(data.get("citations", []))
        sub_opinions = data.get("sub_opinions", [])
        if not sub_opinions:
            return None, None, cluster_cites

        text, oid = _fetch_best_opinion_from_urls(sub_opinions)
        return text, oid, cluster_cites

    except requests.RequestException as e:
        logger.warning(f"Cluster fetch failed for {cluster_id}: {e}")
        return None, None, []


def _extract_id_from_url(url: str) -> int | None:
    """Extract the last numeric path segment from a URL."""
    if not url:
        return None
    # Get the last numeric path segment (e.g., /opinions/12345/ → 12345)
    parts = url.rstrip("/").split("/")
    for part in reversed(parts):
        if part.isdigit():
            return int(part)
    try:
        return int(url)
    except (ValueError, TypeError):
        return None


def _format_cluster_citations(citations_data: list) -> list[str]:
    """Convert citations from CourtListener cluster format to strings.

    Handles both string format (from search API) and dict format (from cluster API).
    """
    result = []
    for c in citations_data:
        if isinstance(c, str):
            result.append(c)
        elif isinstance(c, dict):
            v = c.get("volume", "")
            r = c.get("reporter", "")
            p = c.get("page", "")
            if v and r and p:
                result.append(f"{v} {r} {p}")
    return result


def _strip_html(html: str) -> str:
    """Strip HTML tags and decode common entities."""
    # Remove <script> and <style> blocks (including content)
    text = re.sub(r"<script[^>]*>.*?</script>", "", html, flags=re.DOTALL | re.IGNORECASE)
    text = re.sub(r"<style[^>]*>.*?</style>", "", text, flags=re.DOTALL | re.IGNORECASE)
    # Remove HTML comments
    text = re.sub(r"<!--.*?-->", "", text, flags=re.DOTALL)
    # Strip remaining tags
    text = re.sub(r"<[^>]+>", "", text)
    # Decode all HTML entities properly (smart quotes, em-dashes, section symbols, etc.)
    text = html_module.unescape(text)
    # Normalize non-breaking spaces to regular spaces
    text = text.replace("\xa0", " ")
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
            status="mention",  # GovInfo finds documents that mention the citation, not the case itself
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

def confirm_case_by_name(case_name: str) -> bool:
    """Search CourtListener by case name to confirm a case exists.

    Used as a secondary check when citation lookup returns a different case.
    Searches by quoted party names (not full case name) to handle abbreviation
    differences like "International" vs "Int'l".
    Returns True if the case name was found, False otherwise.
    """
    if not case_name or not COURTLISTENER_API_TOKEN:
        return False

    # Split on "v." to get party names
    parties = re.split(r'\s+v\.?\s+', case_name.strip())
    if len(parties) < 2:
        return False

    # Build query with quoted party names (handles abbreviation mismatches)
    # e.g., "Alice Corp" "CLS Bank" instead of "Alice Corp. v. CLS Bank International"
    party_queries = []
    for party in parties:
        clean = party.strip().rstrip(',.')
        # Remove common entity suffixes that may differ between brief and database
        clean = re.sub(
            r',?\s+(Inc|Corp|Corporation|LLC|L\.L\.C|Ltd|Co|International|Int\'l|'
            r'FSB|NA|N\.A\.|LP|L\.P\.)\.?\s*$',
            '', clean, flags=re.IGNORECASE,
        ).strip()
        if len(clean) > 3:
            party_queries.append(f'"{clean}"')

    if len(party_queries) < 2:
        return False

    query = " ".join(party_queries)
    result = _cl_v4_search(q=query)
    if result and result.found and result.case_name:
        # Normalize both sides so "Int'l" == "International", "Corp." == "Corporation"
        ret_norm = normalize_legal_name(result.case_name)
        # Verify BOTH party names appear in the returned case to avoid
        # false positives where only one party name matches a different case.
        # Use word-boundary matching to prevent "jones" matching "jonesboro".
        ret_words = set(ret_norm.split())
        matched_parties = 0
        for party in parties:
            party_norm = normalize_legal_name(party)
            party_words = [w for w in party_norm.split() if len(w) > 3]
            if party_words and any(w in ret_words for w in party_words[:2]):
                matched_parties += 1
        if matched_parties >= 2:
            return True
    return False


def lookup_citation(citation_text: str, case_name: str = "") -> LookupResult:
    """Look up a citation, trying CourtListener V4 first then GovInfo."""
    if not citation_text or not citation_text.strip():
        return LookupResult(found=False, status="not_found")

    result = lookup_citation_courtlistener(citation_text, case_name=case_name)
    if result.found:
        return result

    return lookup_citation_govinfo(citation_text, case_name=case_name)

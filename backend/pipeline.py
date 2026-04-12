"""Verification pipeline — orchestrates extraction, lookup, and verification.

Maximizes accuracy through:
- Two-pass citation extraction (extract + review)
- Id./supra reference resolution before lookup
- Citation plausibility validation
- Three-strategy source lookup (citation param, free-text, case name)
- Thorough multi-step verification with large token budgets
- AI knowledge fallback for citations without source text
- Constrained cross-citation consistency check
"""

import json
import logging
import re
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Callable, Optional

from backend.ai_client import call_ai_json
from backend.citation_extractor import ExtractedCitation, extract_citations
from backend.extractor import extract_document
from backend.source_lookup import (
    LookupResult,
    _parse_citation_parts,
    confirm_case_by_name,
    lookup_citation,
    normalize_legal_name,
)
from backend.verifier import (
    VerificationResult,
    make_unverifiable_result,
    verify_citation,
    verify_citation_from_knowledge,
)

logger = logging.getLogger(__name__)

_MAX_WORKERS = 5  # Parallel workers for source lookups and AI verification


def _case_names_match(extracted_name: str, lookup_name: str) -> bool:
    """Check if extracted and looked-up case names likely refer to the same case.

    Uses abbreviation-aware normalization so "Corp." matches "Corporation",
    "Int'l" matches "International", etc.
    """
    if not extracted_name and not lookup_name:
        return True  # Both empty, can't determine
    if not extracted_name or not lookup_name:
        return False  # One is empty — can't confirm match
    # Normalize both names — expands legal abbreviations to full forms
    ext = normalize_legal_name(extracted_name)
    lkp = normalize_legal_name(lookup_name)
    if ext == lkp:
        return True
    # Extract party names (split on "v" — periods already stripped by normalizer)
    ext_parties = re.split(r'\s+v\s+', ext)
    lkp_parties = re.split(r'\s+v\s+', lkp)
    lkp_words = set(lkp.split())
    ext_words = set(ext.split())
    # Check if at least one substantial party name word matches (word-boundary)
    for party in ext_parties:
        words = [w for w in party.strip().rstrip(',').split() if len(w) > 3]
        if words and any(w in lkp_words for w in words[:2]):
            return True
    for party in lkp_parties:
        words = [w for w in party.strip().rstrip(',').split() if len(w) > 3]
        if words and any(w in ext_words for w in words[:2]):
            return True
    return False


# ── Id./Supra Reference Resolution (#2) ─────────────────────────────────

def _resolve_id_supra_references(citations: list[ExtractedCitation]) -> None:
    """Resolve Id., supra, and bare pinpoint references to parent citation_text.

    Ensures the lookup cache can match Id./supra refs to their parent's lookup result.
    Modifies citations in place.
    """
    # Sort by position to process in document order
    sorted_cites = sorted(citations, key=lambda c: c.position_start)

    # Track the most recent full citation for each case name + globally
    last_full_by_name: dict[str, str] = {}  # case_name_lower -> citation_text
    last_full_cite: str | None = None  # Most recent full citation (for Id.)

    for citation in sorted_cites:
        ct = citation.citation_text.strip()
        ct_lower = ct.lower()

        is_id_ref = ct_lower.startswith("id.") or ct_lower == "id" or ct_lower.startswith("ibid")
        is_supra_ref = "supra" in ct_lower
        # Bare pinpoint: "556 U.S. at 678" — has " at " with digits after
        has_bare_pinpoint = bool(re.search(r'\s+at\s+\d+', ct_lower))

        if is_id_ref and last_full_cite:
            logger.debug(f"Resolving Id. reference '{ct}' -> '{last_full_cite}'")
            citation.citation_text = last_full_cite
        elif is_supra_ref and citation.case_name:
            name_key = citation.case_name.lower().strip()
            for known_name, known_cite in last_full_by_name.items():
                if name_key in known_name or known_name in name_key:
                    logger.debug(f"Resolving supra reference '{ct}' -> '{known_cite}'")
                    citation.citation_text = known_cite
                    break
        elif has_bare_pinpoint and citation.case_name:
            # "556 U.S. at 678" -> resolve to parent "556 U.S. 662 (2009)"
            name_key = citation.case_name.lower().strip()
            for known_name, known_cite in last_full_by_name.items():
                if name_key in known_name or known_name in name_key:
                    logger.debug(f"Resolving pinpoint reference '{ct}' -> '{known_cite}'")
                    citation.citation_text = known_cite
                    break
        elif not is_id_ref and not is_supra_ref and not has_bare_pinpoint:
            # This looks like a full citation — record it
            last_full_cite = ct
            if citation.case_name:
                last_full_by_name[citation.case_name.lower().strip()] = ct


# ── Citation Plausibility Validation (#5) ────────────────────────────────

_REPORTER_ERAS = {
    "F.": (1880, 1924),
    "F.2d": (1924, 1993),
    "F.3d": (1993, 2030),
    "F.4th": (2021, 2030),
    "U.S.": (1790, 2030),
    "S. Ct.": (1882, 2030),
    "S.Ct.": (1882, 2030),
    "L. Ed.": (1790, 1956),
    "L. Ed. 2d": (1956, 2030),
    "F. Supp.": (1932, 1998),
    "F. Supp. 2d": (1998, 2014),
    "F. Supp. 3d": (2014, 2030),
}


def validate_citation_plausibility(citation_text: str) -> dict:
    """Check if a citation is plausible based on reporter/volume/year.

    Returns {"plausible": bool, "reason": str}.
    """
    from backend.source_lookup import _parse_citation_parts

    parts = _parse_citation_parts(citation_text)
    if not parts:
        return {"plausible": True, "reason": ""}  # Can't parse, don't block

    volume, reporter, page = parts

    try:
        volume_int = int(volume)
        page_int = int(page)
    except (ValueError, TypeError):
        return {"plausible": True, "reason": ""}

    if volume_int <= 0 or page_int <= 0:
        return {"plausible": False, "reason": f"Invalid volume ({volume}) or page ({page})"}

    # Check reporter era against year if available
    year_match = re.search(r'\((\d{4})\)', citation_text)
    if year_match:
        year = int(year_match.group(1))
        reporter_stripped = reporter.strip()
        for rep, (start_year, end_year) in _REPORTER_ERAS.items():
            if reporter_stripped == rep or reporter_stripped.replace(" ", "") == rep.replace(" ", ""):
                if year < start_year or year > end_year:
                    return {
                        "plausible": False,
                        "reason": f"Reporter '{reporter_stripped}' did not exist in {year} "
                                  f"(published {start_year}-{end_year})",
                    }
                break

        # Basic year sanity
        if year < 1600 or year > 2030:
            return {"plausible": False, "reason": f"Implausible year: {year}"}

    return {"plausible": True, "reason": ""}


def _check_citation_format_against_lookup(
    citation_text: str,
    actual_citations: list[str],
) -> str | None:
    """Compare brief's citation volume/reporter/page against actual case citations.

    Returns issue description if a discrepancy is found, None otherwise.
    """
    if not actual_citations:
        return None

    parts = _parse_citation_parts(citation_text)
    if not parts:
        return None

    brief_vol, brief_rep, brief_page = parts
    brief_rep_norm = brief_rep.replace(" ", "").replace(".", "").lower()

    for actual in actual_citations:
        actual_parts = _parse_citation_parts(actual)
        if not actual_parts:
            continue
        act_vol, act_rep, act_page = actual_parts
        act_rep_norm = act_rep.replace(" ", "").replace(".", "").lower()

        if brief_rep_norm == act_rep_norm:
            # Same reporter series — compare volume and page
            issues = []
            if brief_vol != act_vol:
                issues.append(f"volume should be {act_vol}, not {brief_vol}")
            if brief_page != act_page:
                issues.append(f"starting page should be {act_page}, not {brief_page}")
            if issues:
                return (
                    f"Citation format mismatch in {brief_rep}: "
                    f"{'; '.join(issues)} (actual: {actual})"
                )
            return None  # Exact match for this reporter

    return None


def _check_year_against_lookup(citation_text: str, date_filed: str) -> str | None:
    """Check if year in citation matches the case's actual filing date.

    Returns issue description if year doesn't match, None otherwise.
    """
    if not date_filed:
        return None

    cite_year_match = re.search(r'\((\d{4})\)', citation_text)
    if not cite_year_match:
        return None
    cite_year = int(cite_year_match.group(1))

    filed_year_match = re.search(r'(\d{4})', date_filed)
    if not filed_year_match:
        return None
    filed_year = int(filed_year_match.group(1))

    if cite_year != filed_year:
        return f"Year mismatch: citation says ({cite_year}) but case was decided in {filed_year}"

    return None


def _detect_extractor_year_correction(
    citation: 'ExtractedCitation',
    document_text: str,
) -> str | None:
    """Detect if the AI extractor auto-corrected the year in a citation.

    Searches the original document text for the citation's volume/reporter/page
    pattern and checks if the nearby year matches the extracted citation's year.
    """
    parts = _parse_citation_parts(citation.citation_text)
    if not parts:
        return None

    volume, reporter, page = parts

    cite_year_match = re.search(r'\((\d{4})\)', citation.citation_text)
    if not cite_year_match:
        return None
    extracted_year = cite_year_match.group(1)

    # Build regex to find this citation in the document
    reporter_pattern = re.escape(reporter).replace(r"\ ", r"\s*").replace(r"\.", r"\.?")
    pattern = rf"{re.escape(volume)}\s+{reporter_pattern}\s+{re.escape(page)}"

    # Find the match closest to the citation's extracted position
    best_match = None
    best_distance = float('inf')
    for match in re.finditer(pattern, document_text):
        distance = abs(match.start() - citation.position_start)
        if distance < best_distance:
            best_distance = distance
            best_match = match

    if not best_match:
        return None

    # Look for year in parentheses after the match (within 50 chars)
    end_pos = best_match.end()
    window = document_text[end_pos:end_pos + 50]
    year_match = re.search(r'\((\d{4})\)', window)
    if year_match:
        doc_year = year_match.group(1)
        if doc_year != extracted_year:
            return (
                f"Extractor corrected year: document says ({doc_year}) "
                f"but extraction reports ({extracted_year})"
            )

    return None


# ── Pass 3: Cross-Citation Consistency Check ─────────────────────────────

CONSISTENCY_SYSTEM = """You are a quality control specialist reviewing citation verification results
for a legal document. Your job is to find INCONSISTENCIES across the full set of results.

Check for:
1. Same case cited multiple times with contradictory statuses — if a case was found and verified
   in one citation, the same case should not be "error" or "not found" in another citation
2. Results where an "error" status seems wrong given that the case clearly exists (found in lookup)
3. Confidence levels that seem inconsistent — same case should not have 0.95 in one citation
   and 0.3 in another unless the quotes/characterizations are genuinely different
4. Cases where a "verified" status with high confidence seems suspicious (e.g., the reasoning
   doesn't support the confidence level)

ABBREVIATION AWARENESS: Legal case names routinely use standard abbreviations that vary
between sources (e.g., Corp./Corporation, Int'l/International, Dep't/Department, and many
others). These are only common examples — apply your full knowledge of legal abbreviations.
A case_name and lookup_case_name that differ only in abbreviation style refer to the SAME
case — this is NOT a mismatch or inconsistency.
If a citation has "name_confirmed_exists: true", the case was confirmed to exist via a
separate name search — do NOT downgrade its status based on a lookup_case_name difference.

CITATION FORMAT: Do NOT promote a citation from warning/error to verified if its citation_text
has a different volume, page number, or year than another citation of the same case. Different
citation numbers suggest the author may have gotten one citation wrong — each must be judged
independently on its own merits.

You are NOT re-verifying each citation — just checking for logical consistency across the set.
Only flag genuine inconsistencies, not expected differences (different quotes from the same case
can legitimately have different accuracies)."""

CONSISTENCY_PROMPT = """Review these {count} citation verification results for consistency.

{citations_summary}

Return a JSON object with:
- adjustments: array of objects, each with:
  - index: integer (0-based citation index)
  - revised_status: "verified" | "warning" | "error" | null (null = no change)
  - revised_confidence: number or null (null = no change)
  - reason: string (why this adjustment is needed)
- consistent: boolean (true if all results look logically consistent)

If everything is consistent, return {{"adjustments": [], "consistent": true}}."""


def _check_cross_citation_consistency(
    citation_reports: list,
    name_confirmed_citations: set[int] | None = None,
    format_discrepancy_indices: set[int] | None = None,
) -> None:
    """Pass 3: AI reviews all verification results for cross-citation consistency.

    Modifies citation_reports in place — adjusts statuses/confidence where inconsistent.
    """
    if len(citation_reports) < 2:
        return

    _name_confirmed = name_confirmed_citations or set()
    summaries = []
    for i, cr in enumerate(citation_reports):
        entry = {
            "index": i,
            "case_name": cr.extraction.case_name,
            "citation_text": cr.extraction.citation_text,
            "lookup_found": cr.lookup.found,
            "lookup_case_name": cr.lookup.case_name,
            "status": cr.verification.status,
            "confidence": cr.verification.confidence,
            "quote_accuracy": cr.verification.quote_accuracy,
            "characterization_accuracy": cr.verification.characterization_accuracy,
            "has_quote": bool(cr.extraction.quoted_text),
            "has_characterization": bool(cr.extraction.characterization),
        }
        if i in _name_confirmed:
            entry["name_confirmed_exists"] = True
            entry["note"] = (
                "Lookup returned wrong case due to citation database mapping issue, "
                "but a separate name search confirmed this case exists. "
                "Do NOT downgrade status based on lookup mismatch alone."
            )
        summaries.append(entry)

    prompt = CONSISTENCY_PROMPT.format(
        count=len(summaries),
        citations_summary=json.dumps(summaries, indent=2),
    )

    try:
        result = call_ai_json(
            messages=[{"role": "user", "content": prompt}],
            system=CONSISTENCY_SYSTEM,
            max_tokens=4096,
            operation_name="Cross-citation consistency check",
        )
    except RuntimeError:
        logger.warning("Consistency check failed; keeping original results")
        return

    if not isinstance(result, dict):
        return

    adjustments = result.get("adjustments", [])
    if not adjustments:
        logger.info("Pass 3: all results consistent")
        return

    _VALID_STATUSES = {"verified", "warning", "error", "unverifiable"}
    applied = 0
    for adj in adjustments:
        if not isinstance(adj, dict):
            continue
        idx = adj.get("index")
        if not isinstance(idx, int) or idx < 0 or idx >= len(citation_reports):
            continue

        cr = citation_reports[idx]
        reason = adj.get("reason", "")

        revised_status = adj.get("revised_status")
        if revised_status and revised_status in _VALID_STATUSES and revised_status != cr.verification.status:
            old_status = cr.verification.status

            # Guard: never downgrade a name-confirmed citation from verified/warning
            # When confirm_case_by_name() found the case, the lookup mismatch is a
            # CourtListener mapping issue, not evidence of fabrication.
            if idx in _name_confirmed and old_status in ("verified", "warning") and revised_status in ("error", "unverifiable"):
                logger.info(
                    f"Pass 3: BLOCKED downgrade of citation[{idx}] "
                    f"'{cr.extraction.case_name}' from {old_status} -> {revised_status} "
                    f"(case confirmed to exist by name search)"
                )
                continue

            # (#3) Guard: never downgrade error when verification found substantive problems
            if old_status == "error" and revised_status in ("warning", "verified"):
                char_acc = cr.verification.characterization_accuracy
                quote_acc = cr.verification.quote_accuracy
                if char_acc in ("unsupported", "misleading") or quote_acc == "inaccurate":
                    logger.info(
                        f"Pass 3: BLOCKED downgrade of citation[{idx}] "
                        f"'{cr.extraction.case_name}' from error -> {revised_status} "
                        f"(substantive accuracy issue: quote={quote_acc}, char={char_acc})"
                    )
                    continue

            # Guard: never upgrade a citation with format discrepancies (wrong volume, year, etc.)
            _format_disc = format_discrepancy_indices or set()
            if idx in _format_disc and revised_status in ("verified",) and old_status in ("warning", "error"):
                logger.info(
                    f"Pass 3: BLOCKED upgrade of citation[{idx}] "
                    f"'{cr.extraction.case_name}' from {old_status} -> {revised_status} "
                    f"(citation has format discrepancies)"
                )
                continue

            cr.verification.status = revised_status
            cr.verification.reasoning = (
                f"[Consistency: {old_status} → {revised_status}] {reason}. "
                f"Original: {cr.verification.reasoning or ''}"
            )
            logger.info(
                f"Pass 3: citation[{idx}] '{cr.extraction.case_name}' "
                f"{old_status} → {revised_status}: {reason}"
            )
            applied += 1

        revised_confidence = adj.get("revised_confidence")
        if revised_confidence is not None:
            try:
                cr.verification.confidence = max(0.0, min(1.0, float(revised_confidence)))
            except (ValueError, TypeError):
                pass

    logger.info(f"Pass 3: applied {applied} consistency adjustments")


@dataclass
class CitationReport:
    """Full report for a single citation."""
    extraction: ExtractedCitation
    lookup: LookupResult
    verification: VerificationResult


@dataclass
class VerificationReport:
    """Complete verification report for a document."""
    id: str
    filename: str
    document_text: str
    total_citations: int
    verified: int
    warnings: int
    errors: int
    unverifiable: int
    citations: list[CitationReport] = field(default_factory=list)
    extraction_warnings: list[str] = field(default_factory=list)
    created_at: str = ""

    def to_dict(self) -> dict:
        """Serialize for JSON response."""
        return {
            "id": self.id,
            "filename": self.filename,
            "document_text": self.document_text,
            "total_citations": self.total_citations,
            "verified": self.verified,
            "warnings": self.warnings,
            "errors": self.errors,
            "unverifiable": self.unverifiable,
            "citations": [
                {
                    "extraction": {
                        "citation_text": c.extraction.citation_text,
                        "case_name": c.extraction.case_name,
                        "full_reference": c.extraction.full_reference,
                        "quoted_text": c.extraction.quoted_text,
                        "characterization": c.extraction.characterization,
                        "context": c.extraction.context,
                        "position_start": c.extraction.position_start,
                        "position_end": c.extraction.position_end,
                    },
                    "lookup": {
                        "found": c.lookup.found,
                        "status": c.lookup.status,
                        "case_name": c.lookup.case_name,
                        "court": c.lookup.court,
                        "date_filed": c.lookup.date_filed,
                        "url": c.lookup.url,
                        "source": c.lookup.source,
                        "has_opinion_text": c.lookup.opinion_text is not None,
                    },
                    "verification": {
                        "status": c.verification.status,
                        "citation_exists": c.verification.citation_exists,
                        "citation_format_correct": c.verification.citation_format_correct,
                        "quote_accuracy": c.verification.quote_accuracy,
                        "quote_diff": c.verification.quote_diff,
                        "actual_quote": c.verification.actual_quote,
                        "characterization_accuracy": c.verification.characterization_accuracy,
                        "characterization_explanation": c.verification.characterization_explanation,
                        "confidence": c.verification.confidence,
                        "reasoning": c.verification.reasoning,
                        "quote_status": c.verification.quote_status,
                        "characterization_status": c.verification.characterization_status,
                    },
                }
                for c in self.citations
            ],
            "extraction_warnings": self.extraction_warnings,
            "created_at": self.created_at,
        }


ProgressCallback = Callable[[int, int, str], None]


def run_verification(
    file_path: str,
    filename: str,
    progress_callback: Optional[ProgressCallback] = None,
) -> VerificationReport:
    """Run the full verification pipeline on a document."""
    report_id = str(uuid.uuid4())

    def progress(step: int, total: int, message: str) -> None:
        if progress_callback:
            progress_callback(step, total, message)

    # ── Step 1: Extract text ─────────────────────────────────────────────
    progress(2, 100, "Extracting text from document...")
    extraction = extract_document(file_path)
    logger.info(f"Extracted {len(extraction.text)} chars from {filename}")
    if extraction.warnings:
        for w in extraction.warnings:
            logger.warning(f"Extraction warning: {w}")

    # ── Step 2: Extract citations via AI (two-pass) ──────────────────────
    progress(5, 100, "Pass 1: Identifying all citations, quotes, and characterizations...")
    citations = extract_citations(extraction.text)
    logger.info(f"Found {len(citations)} citations in {filename}")

    if not citations:
        return VerificationReport(
            id=report_id,
            filename=filename,
            document_text=extraction.text,
            total_citations=0,
            verified=0,
            warnings=0,
            errors=0,
            unverifiable=0,
            extraction_warnings=extraction.warnings,
            created_at=datetime.now(timezone.utc).isoformat(),
        )

    # (#2) Resolve Id., supra, and bare pinpoint references before lookup
    _resolve_id_supra_references(citations)

    progress(20, 100, f"Found {len(citations)} citations. Looking up sources...")

    # ── Step 3: Look up each citation (parallel) ──────────────────────────
    _reports: dict[int, CitationReport] = {}
    lookup_cache: dict[str, LookupResult] = {}
    lookup_progress_start = 20
    lookup_progress_end = 45
    lookup_range = lookup_progress_end - lookup_progress_start

    # Phase 1: Plausibility check + cache dedup (sequential, fast)
    lookup_needed: dict[str, list[int]] = {}  # cache_key -> [citation indices]
    for i, citation in enumerate(citations):
        # (#5) Citation plausibility check — catch impossible reporter/year combos
        plausibility = validate_citation_plausibility(citation.citation_text)
        if not plausibility["plausible"]:
            logger.warning(
                f"Implausible citation '{citation.citation_text}': {plausibility['reason']}"
            )
            _reports[i] = CitationReport(
                extraction=citation,
                lookup=LookupResult(found=False, status="not_found"),
                verification=make_unverifiable_result(
                    f"Implausible citation: {plausibility['reason']}"
                ),
            )
            continue

        cache_key = citation.citation_text.strip()
        if cache_key in lookup_cache:
            logger.debug(f"Reusing cached lookup for '{citation.case_name}' ({cache_key})")
            _reports[i] = CitationReport(
                extraction=citation,
                lookup=lookup_cache[cache_key],
                verification=make_unverifiable_result(),
            )
            continue

        # Group by cache_key to deduplicate (Id./supra resolved to same citation)
        lookup_needed.setdefault(cache_key, []).append(i)

    # Phase 2: Parallel HTTP lookups (one per unique cache_key)
    total_lookups = len(lookup_needed)
    if total_lookups > 0:
        done_count = 0
        with ThreadPoolExecutor(max_workers=_MAX_WORKERS) as executor:
            futures = {}
            for key, indices in lookup_needed.items():
                c = citations[indices[0]]
                fut = executor.submit(
                    lookup_citation, c.citation_text, case_name=c.case_name,
                )
                futures[fut] = (key, indices)

            for fut in as_completed(futures):
                key, indices = futures[fut]
                try:
                    lookup_result = fut.result()
                except Exception as e:
                    logger.warning(f"Lookup failed for '{key}': {e}")
                    lookup_result = LookupResult(found=False, status="not_found")

                lookup_cache[key] = lookup_result
                for idx in indices:
                    _reports[idx] = CitationReport(
                        extraction=citations[idx],
                        lookup=lookup_result,
                        verification=make_unverifiable_result(),
                    )
                done_count += 1
                pct = lookup_progress_start + int((done_count / total_lookups) * lookup_range)
                name = citations[indices[0]].case_name or key
                progress(pct, 100, f"Looked up ({done_count}/{total_lookups}): {name}...")

    citation_reports: list[CitationReport] = [_reports[i] for i in range(len(citations))]

    # ── Step 3.5: Citation format validation ──────────────────────────────
    format_issues: dict[int, list[str]] = {}
    for i, cr in enumerate(citation_reports):
        if not cr.lookup.found:
            continue
        issues: list[str] = []

        # Check 1: Volume/reporter/page against actual citations from CourtListener
        fmt_issue = _check_citation_format_against_lookup(
            cr.extraction.citation_text, cr.lookup.actual_citations,
        )
        if fmt_issue:
            issues.append(fmt_issue)

        # Check 2: Year against date_filed
        year_issue = _check_year_against_lookup(
            cr.extraction.citation_text, cr.lookup.date_filed,
        )
        if year_issue:
            issues.append(year_issue)

        # Check 3: Detect extractor year auto-correction
        doc_year_issue = _detect_extractor_year_correction(
            cr.extraction, extraction.text,
        )
        if doc_year_issue:
            issues.append(doc_year_issue)

        if issues:
            format_issues[i] = issues
            logger.warning(
                f"Citation format issues for '{cr.extraction.case_name}': "
                + "; ".join(issues)
            )

    # ── Step 4: Verify citations with source text (+ pass 2 review) ─────
    verify_progress_start = 45
    verify_progress_end = 65
    verify_range = verify_progress_end - verify_progress_start
    # Filter out citations where lookup returned a different case (wrong opinion text)
    # Track which citations had mismatches for knowledge verification context
    mismatch_citations: set[int] = set()  # indices of citations with lookup mismatch
    name_confirmed_citations: set[int] = set()  # mismatched but confirmed by name search
    verifiable = []
    for i, cr in enumerate(citation_reports):
        if not cr.lookup.opinion_text:
            continue
        if not _case_names_match(cr.extraction.case_name, cr.lookup.case_name):
            logger.warning(
                f"Case name mismatch: extracted '{cr.extraction.case_name}' "
                f"but lookup returned '{cr.lookup.case_name}' — skipping source verification"
            )
            # (#4) Run a case name confirmation search to check if the case exists at all
            name_confirmed = confirm_case_by_name(cr.extraction.case_name)
            if name_confirmed:
                # Case exists on CourtListener under its name — this is a citation
                # mapping issue, not fabrication. Send to knowledge verification without
                # the hostile fabrication warnings or strict confidence cap.
                logger.info(
                    f"Case name search confirmed '{cr.extraction.case_name}' exists "
                    f"— citation mapping issue, not fabricated"
                )
                cr.verification = make_unverifiable_result(
                    f"Source text is for wrong case ('{cr.lookup.case_name}'), "
                    f"but case confirmed to exist by name search"
                )
                name_confirmed_citations.add(i)
                for j, other_cr in enumerate(citation_reports):
                    if j != i and other_cr.extraction.case_name == cr.extraction.case_name:
                        name_confirmed_citations.add(j)
            else:
                logger.info(
                    f"Case name search also failed for '{cr.extraction.case_name}' "
                    f"— likely fabricated"
                )
                cr.verification = make_unverifiable_result(
                    f"Source text may be for wrong case: '{cr.lookup.case_name}'"
                )
                mismatch_citations.add(i)
                # Also mark other citations with the same case name as mismatched
                for j, other_cr in enumerate(citation_reports):
                    if j != i and other_cr.extraction.case_name == cr.extraction.case_name:
                        mismatch_citations.add(j)
            continue
        verifiable.append(cr)

    logger.info(f"{len(verifiable)} of {len(citation_reports)} citations have source text for verification")

    # Handle existence-only citations (no AI call needed)
    to_verify: list[CitationReport] = []
    for cr in verifiable:
        if cr.extraction.quoted_text or cr.extraction.characterization:
            to_verify.append(cr)
        else:
            cr.verification = VerificationResult(
                status="verified",
                citation_exists=True,
                citation_format_correct=True,
                quote_accuracy=None,
                quote_diff=None,
                actual_quote=None,
                characterization_accuracy=None,
                characterization_explanation="Citation confirmed — no quote or characterization to verify",
                confidence=0.9,
            )

    # Parallel AI verification for citations with quotes/characterizations
    if to_verify:
        done_count = 0
        with ThreadPoolExecutor(max_workers=_MAX_WORKERS) as executor:
            futures = {
                executor.submit(verify_citation, cr.extraction, cr.lookup.opinion_text): cr
                for cr in to_verify
            }
            for fut in as_completed(futures):
                cr = futures[fut]
                try:
                    cr.verification = fut.result()
                except Exception as e:
                    logger.warning(f"Verification failed for {cr.extraction.citation_text}: {e}")
                    cr.verification = make_unverifiable_result(f"Verification error: {e}")
                done_count += 1
                pct = verify_progress_start + int((done_count / len(to_verify)) * verify_range)
                progress(pct, 100, f"Verified ({done_count}/{len(to_verify)}): {cr.extraction.case_name or cr.extraction.citation_text}...")

    # ── Step 5: Knowledge-based verification (parallel) ────────────────
    unverified = [(idx, cr) for idx, cr in enumerate(citation_reports)
                  if cr.verification.status == "unverifiable"
                  and (cr.extraction.quoted_text or cr.extraction.characterization)]

    if unverified:
        knowledge_start = 65
        knowledge_end = 80
        knowledge_range = knowledge_end - knowledge_start
        logger.info(f"Running AI knowledge verification for {len(unverified)} unverifiable citations")

        def _build_lookup_context(idx: int, cr: CitationReport) -> str:
            if idx in mismatch_citations:
                lookup_case = cr.lookup.case_name or "unknown"
                return (
                    f"CRITICAL LOOKUP WARNING: The citation database was searched for "
                    f"'{cr.extraction.citation_text}' and returned a DIFFERENT case "
                    f"('{lookup_case}' instead of '{cr.extraction.case_name}'). "
                    f"A separate search by case name also failed to find this case. "
                    f"This strongly suggests the cited case may NOT EXIST or the "
                    f"citation is fabricated. Be extremely skeptical. If you cannot "
                    f"confidently confirm this case exists from your training data, "
                    f"set overall_status to 'error' or 'unverifiable'."
                )
            if not cr.lookup.found:
                return (
                    f"LOOKUP WARNING: This citation was NOT FOUND in any legal "
                    f"database (CourtListener and GovInfo were both searched). "
                    f"This does not necessarily mean the case is fabricated — the "
                    f"database may not cover this reporter or jurisdiction — but "
                    f"you should be more skeptical than usual. If you cannot "
                    f"confidently confirm this case exists from your training data, "
                    f"prefer 'unverifiable' over 'verified'. Cap your confidence "
                    f"accordingly."
                )
            if cr.lookup.status == "mention":
                return (
                    f"LOOKUP NOTE: This citation was not found in CourtListener's "
                    f"case law database. A GovInfo search found a government document "
                    f"that mentions it, but this only confirms the citation text "
                    f"appears somewhere — NOT that the case exists or that the "
                    f"citation is correct. Treat this as weak evidence."
                )
            return ""

        def _knowledge_verify(idx: int, cr: CitationReport) -> VerificationResult:
            return verify_citation_from_knowledge(
                cr.extraction,
                lookup_context=_build_lookup_context(idx, cr),
                has_lookup_mismatch=idx in mismatch_citations,
            )

        done_count = 0
        with ThreadPoolExecutor(max_workers=_MAX_WORKERS) as executor:
            futures = {
                executor.submit(_knowledge_verify, idx, cr): (idx, cr)
                for idx, cr in unverified
            }
            for fut in as_completed(futures):
                idx, cr = futures[fut]
                try:
                    cr.verification = fut.result()
                except Exception as e:
                    logger.warning(f"Knowledge verification failed for {cr.extraction.citation_text}: {e}")
                    cr.verification = make_unverifiable_result(f"Knowledge verification error: {e}")

                # (#2) Escalate low-confidence + mismatch to ERROR
                has_mismatch = idx in mismatch_citations
                if has_mismatch and cr.verification.status in ("warning", "unverifiable"):
                    if cr.verification.confidence <= 0.5:
                        old_status = cr.verification.status
                        cr.verification.status = "error"
                        cr.verification.reasoning = (
                            f"[Escalated from {old_status}: citation database returned wrong case "
                            f"and knowledge confidence is low ({cr.verification.confidence:.2f})] "
                            + (cr.verification.reasoning or "")
                        )
                        logger.info(
                            f"Escalated '{cr.extraction.case_name}' from {old_status} -> error "
                            f"(lookup mismatch + low confidence {cr.verification.confidence:.2f})"
                        )

                done_count += 1
                pct = knowledge_start + int((done_count / len(unverified)) * knowledge_range)
                progress(pct, 100, f"AI knowledge check ({done_count}/{len(unverified)}): {cr.extraction.case_name}...")

    # ── Step 5.5: Apply citation format downgrades ────────────────────────
    for i, issues in format_issues.items():
        cr = citation_reports[i]
        if cr.verification.status == "verified":
            cr.verification.status = "warning"
            cr.verification.reasoning = (
                f"[Citation format: {'; '.join(issues)}] "
                + (cr.verification.reasoning or "")
            )
            logger.info(
                f"Downgraded '{cr.extraction.case_name}' from verified → warning "
                f"due to format issues: {'; '.join(issues)}"
            )

    # ── Step 6: Cross-citation consistency check (pass 3) ────────────────
    progress(82, 100, "Pass 3: Cross-citation consistency check...")
    try:
        _check_cross_citation_consistency(
            citation_reports, name_confirmed_citations,
            format_discrepancy_indices=set(format_issues.keys()),
        )
    except Exception as e:
        logger.warning(f"Consistency check error (non-fatal): {e}")

    # ── Step 7: Assemble report ──────────────────────────────────────────
    progress(95, 100, "Assembling report...")

    verified_count = sum(1 for cr in citation_reports if cr.verification.status == "verified")
    warning_count = sum(1 for cr in citation_reports if cr.verification.status == "warning")
    error_count = sum(1 for cr in citation_reports if cr.verification.status == "error")
    unverifiable_count = sum(1 for cr in citation_reports if cr.verification.status == "unverifiable")

    report = VerificationReport(
        id=report_id,
        filename=filename,
        document_text=extraction.text,
        total_citations=len(citation_reports),
        verified=verified_count,
        warnings=warning_count,
        errors=error_count,
        unverifiable=unverifiable_count,
        citations=citation_reports,
        extraction_warnings=extraction.warnings,
        created_at=datetime.now(timezone.utc).isoformat(),
    )

    progress(100, 100, "Verification complete")
    logger.info(
        f"Report {report_id}: {len(citation_reports)} citations — "
        f"{verified_count} verified, {warning_count} warnings, "
        f"{error_count} errors, {unverifiable_count} unverifiable"
    )

    return report

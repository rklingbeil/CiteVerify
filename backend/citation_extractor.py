"""AI-powered citation, quote, and characterization extraction.

Two-pass extraction for maximum accuracy:
  Pass 1: Full extraction of all citations, quotes, and characterizations
  Pass 2: Review pass — AI checks its own work against the original document
"""

import logging
from dataclasses import dataclass
from typing import Optional

from backend.ai_client import call_ai_json

logger = logging.getLogger(__name__)

EXTRACTION_SYSTEM = """You are a legal citation analysis expert with deep knowledge of legal
citation formats (Bluebook, state-specific reporters, etc.). Your task is to extract EVERY
legal citation, direct quotation, and case characterization from the provided legal document.

You must be EXHAUSTIVE. Missing even one citation is a failure. Check:
- Body text citations
- Footnote citations
- String cites (multiple cites in a row separated by semicolons)
- Parenthetical citations
- "See" and "See also" citations
- "Cf." citations
- "But see" and "But cf." citations
- Block quotes with attribution
- Id. and supra references (identify the case they refer back to)

For each citation found, return:
- citation_text: The FULL formal citation with the STARTING page of the case — NOT pinpoint
  pages. For example: if the document says "Leon, 464 F.3d at 958", the full citation for
  that case is "464 F.3d 951" (the first page). If you know the starting page from earlier in
  the document or from your legal knowledge, use it. If you only have a pinpoint cite like
  "501 U.S. at 44", try to identify the full citation from context (e.g., "501 U.S. 32").
  If the document provides the full citation somewhere (even earlier), use that.
  NEVER use "at" in citation_text — that indicates a pinpoint, not the full cite.
  Include the year parenthetical when known, e.g. "501 U.S. 32 (1991)".
- pinpoint: The specific page(s) referenced (e.g., "958" or "44–46"), or null if none
- case_name: The FULL case name with proper party names (e.g., "Leon v. IDX Systems Corp."),
  not abbreviated. Expand abbreviations when possible.
- full_reference: The complete reference exactly as it appears in the document
- quoted_text: Any direct quote from the cited case that appears in the document (null if none).
  Extract the EXACT quote including ellipses, brackets, emphasis markers.
- characterization: How the document describes what the case held, established, or stands for
  (null if the citation is used without any description of its holding)
- context: The FULL sentence or paragraph containing the citation — enough context to understand
  how the citation is being used
- position_start: Approximate character offset where the citation appears in the document
- position_end: Approximate character offset where the citation reference ends

CRITICAL RULES:
1. If the same case is cited multiple times at different pinpoints, create a SEPARATE entry
   for each occurrence, but always use the same base citation_text for that case.
2. For "Id." references, identify which case "Id." refers to and use that case's citation.
3. For string cites ("See A; B; C"), create separate entries for each case.
4. Do NOT skip any citation even if it seems minor or repetitive — completeness is paramount."""

EXTRACTION_PROMPT = """Extract ALL legal citations, quotes, and characterizations from this document.
Be exhaustive — do not miss any citation, even in footnotes or parentheticals.

Return a JSON array of objects. Each object must have these fields:
- citation_text (string — FULL citation with starting page and year, NO "at" pinpoints)
- pinpoint (string or null — specific page/pages referenced)
- case_name (string — full case name, not abbreviated)
- full_reference (string — exactly as it appears in document)
- quoted_text (string or null)
- characterization (string or null)
- context (string — full sentence/paragraph)
- position_start (integer)
- position_end (integer)

DOCUMENT:
{document_text}"""

REVIEW_SYSTEM = """You are a legal citation verification expert performing a quality review.
You have been given:
1. The original document text
2. A list of citations that were already extracted from it

Your job is to find any MISSED citations and correct any errors in the existing extractions.

Go through the document carefully paragraph by paragraph and check:
- Are there any citations in the document that are NOT in the extracted list?
- Are any case names wrong or incomplete?
- Are any citation_text values incorrect (wrong volume, reporter, or page)?
- Are any quoted_text extractions incomplete or inaccurate?
- Were any characterizations missed?

Return a JSON object with:
- missed: array of new citation objects (same format as the original extraction) that were
  not found in the first pass
- corrections: array of objects with { "index": <0-based index into original list>,
  "field": "<field name>", "old_value": "...", "new_value": "..." } for any corrections needed
"""

REVIEW_PROMPT = """Review the following extraction results against the original document.
Find any MISSED citations and correct any errors.

ORIGINAL DOCUMENT:
{document_text}

EXTRACTED CITATIONS ({count} found):
{citations_json}

Return a JSON object with "missed" (array of new citation objects) and "corrections" (array of correction objects).
If everything looks correct and complete, return {{"missed": [], "corrections": []}}."""


@dataclass
class ExtractedCitation:
    citation_text: str
    case_name: str
    full_reference: str
    quoted_text: Optional[str]
    characterization: Optional[str]
    context: str
    position_start: int
    position_end: int
    pinpoint: Optional[str] = None


def extract_citations(document_text: str) -> list[ExtractedCitation]:
    """Extract all citations with two-pass extraction for maximum accuracy."""
    # For very long documents, process in chunks
    max_chunk = 100_000  # characters — larger chunks for better context
    if len(document_text) <= max_chunk:
        return _extract_with_review(document_text)

    # Split into overlapping chunks
    overlap = 8_000
    chunks = []
    start = 0
    while start < len(document_text):
        end = min(start + max_chunk, len(document_text))
        chunks.append((start, document_text[start:end]))
        if end >= len(document_text):
            break
        start = end - overlap

    logger.info(f"Document split into {len(chunks)} chunks for extraction")

    all_citations: list[ExtractedCitation] = []
    seen: set[str] = set()

    for chunk_offset, chunk_text in chunks:
        chunk_citations = _extract_with_review(chunk_text)
        for c in chunk_citations:
            # Adjust positions for chunk offset
            c.position_start += chunk_offset
            c.position_end += chunk_offset
            # Deduplicate by citation text + approximate position
            key = f"{c.citation_text}:{c.position_start // 500}"
            if key not in seen:
                seen.add(key)
                all_citations.append(c)

    return all_citations


def _extract_with_review(text: str) -> list[ExtractedCitation]:
    """Two-pass extraction: extract then review for missed citations."""
    # Pass 1: Initial extraction
    citations = _extract_from_text(text)
    logger.info(f"Pass 1: extracted {len(citations)} citations")

    # Pass 2: Review pass — check for missed citations and errors
    if citations:  # Only review if we found something (otherwise the doc may not have cites)
        citations = _review_extraction(text, citations)
        logger.info(f"Pass 2 (review): now have {len(citations)} citations")

    return citations


def _extract_from_text(text: str) -> list[ExtractedCitation]:
    """Extract citations from a single text chunk."""
    import json
    prompt = EXTRACTION_PROMPT.format(document_text=text)

    result = call_ai_json(
        messages=[{"role": "user", "content": prompt}],
        system=EXTRACTION_SYSTEM,
        max_tokens=32768,  # Large budget — don't truncate results
        operation_name="Citation extraction",
    )

    if not isinstance(result, list):
        logger.warning("Citation extraction returned non-list; wrapping")
        result = [result] if isinstance(result, dict) else []

    return _parse_citation_list(result)


def _review_extraction(text: str, citations: list[ExtractedCitation]) -> list[ExtractedCitation]:
    """Review pass: find missed citations and correct errors."""
    import json

    # Build summary of what was found
    cite_summaries = []
    for i, c in enumerate(citations):
        summary = {
            "index": i,
            "case_name": c.case_name,
            "citation_text": c.citation_text,
            "quoted_text": c.quoted_text[:100] if c.quoted_text else None,
            "characterization": c.characterization[:100] if c.characterization else None,
        }
        cite_summaries.append(summary)

    prompt = REVIEW_PROMPT.format(
        document_text=text,
        count=len(citations),
        citations_json=json.dumps(cite_summaries, indent=2),
    )

    try:
        result = call_ai_json(
            messages=[{"role": "user", "content": prompt}],
            system=REVIEW_SYSTEM,
            max_tokens=16384,
            operation_name="Citation review",
        )
    except RuntimeError:
        logger.warning("Review pass failed; returning original extraction")
        return citations

    if not isinstance(result, dict):
        return citations

    # Apply corrections
    corrections = result.get("corrections", [])
    for corr in corrections:
        if not isinstance(corr, dict):
            continue
        idx = corr.get("index")
        field = corr.get("field")
        new_value = corr.get("new_value")
        if idx is not None and field and new_value is not None and 0 <= idx < len(citations):
            if hasattr(citations[idx], field):
                logger.info(f"Correction: citation[{idx}].{field} = {new_value!r}")
                setattr(citations[idx], field, new_value)

    # Add missed citations
    missed = result.get("missed", [])
    if isinstance(missed, list):
        new_cites = _parse_citation_list(missed)
        if new_cites:
            logger.info(f"Review found {len(new_cites)} missed citations")
            citations.extend(new_cites)

    # Re-sort by position
    citations.sort(key=lambda c: c.position_start)

    return citations


def _parse_citation_list(items: list) -> list[ExtractedCitation]:
    """Parse a list of dicts into ExtractedCitation objects."""
    citations = []
    for item in items:
        if not isinstance(item, dict):
            continue
        citations.append(ExtractedCitation(
            citation_text=item.get("citation_text", ""),
            case_name=item.get("case_name", ""),
            full_reference=item.get("full_reference", ""),
            quoted_text=item.get("quoted_text"),
            characterization=item.get("characterization"),
            context=item.get("context", ""),
            position_start=item.get("position_start", 0),
            position_end=item.get("position_end", 0),
            pinpoint=item.get("pinpoint"),
        ))
    return citations

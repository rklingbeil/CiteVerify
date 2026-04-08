"""AI-powered citation, quote, and characterization extraction."""

import logging
from dataclasses import dataclass
from typing import Optional

from backend.ai_client import call_ai_json

logger = logging.getLogger(__name__)

EXTRACTION_SYSTEM = """You are a legal citation analysis expert. Your task is to extract every
legal citation, direct quotation, and case characterization from the provided legal document.

For each citation found, return:
- citation_text: The formal citation (e.g., "325 Or App 648")
- case_name: The case name (e.g., "Smith v. Jones")
- full_reference: The complete reference as it appears
- quoted_text: Any direct quote from the cited case that appears in the document (null if none)
- characterization: How the document describes what the case held or established (null if none)
- context: The full sentence or paragraph containing the citation
- position_start: Approximate character offset where the citation appears in the document
- position_end: Approximate character offset where the citation reference ends

Be thorough — find EVERY citation, including footnotes. Include both case citations and
statutory citations. For quoted text, extract the EXACT quote as it appears in the document,
including any ellipses, brackets, or emphasis markers."""

EXTRACTION_PROMPT = """Extract all legal citations, quotes, and characterizations from this document.

Return a JSON array of objects. Each object must have these fields:
- citation_text (string)
- case_name (string)
- full_reference (string)
- quoted_text (string or null)
- characterization (string or null)
- context (string)
- position_start (integer)
- position_end (integer)

DOCUMENT:
{document_text}"""


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


def extract_citations(document_text: str) -> list[ExtractedCitation]:
    """Extract all citations, quotes, and characterizations from document text."""
    # For very long documents, process in chunks
    max_chunk = 80_000  # characters
    if len(document_text) <= max_chunk:
        return _extract_from_text(document_text)

    # Split into overlapping chunks
    overlap = 5_000
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
        chunk_citations = _extract_from_text(chunk_text)
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


def _extract_from_text(text: str) -> list[ExtractedCitation]:
    """Extract citations from a single text chunk."""
    prompt = EXTRACTION_PROMPT.format(document_text=text)

    result = call_ai_json(
        messages=[{"role": "user", "content": prompt}],
        system=EXTRACTION_SYSTEM,
        max_tokens=16384,
        operation_name="Citation extraction",
    )

    if not isinstance(result, list):
        logger.warning("Citation extraction returned non-list; wrapping")
        result = [result] if isinstance(result, dict) else []

    citations = []
    for item in result:
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
        ))

    logger.info(f"Extracted {len(citations)} citations from text ({len(text)} chars)")
    return citations

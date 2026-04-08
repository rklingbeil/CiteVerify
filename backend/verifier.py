"""Quote and characterization verification against source opinion text."""

import logging
from dataclasses import dataclass
from typing import Optional

from backend.ai_client import call_ai_json
from backend.citation_extractor import ExtractedCitation

logger = logging.getLogger(__name__)

VERIFY_SYSTEM = """You are a legal citation verification expert. You will be given:
1. A quote or characterization from a legal document
2. The full opinion text from the cited case

Your job is to verify accuracy. For quotes, find the matching passage in the opinion and
assess whether the quote is exact, close (minor formatting differences like ellipses or
bracket insertions are acceptable in legal writing), or inaccurate.

For characterizations of holdings, assess whether the document's description of what the
case held is supported by the actual opinion text.

Standard legal quotation conventions are acceptable:
- Ellipses (...) indicating omitted text
- Brackets [like this] for editorial insertions
- [Emphasis added] or [internal citations omitted]
- Minor punctuation differences

These should NOT be flagged as errors."""

VERIFY_PROMPT = """Verify the following citation from a legal document against the source opinion.

CITATION: {case_name}, {citation_text}

{quote_section}

{characterization_section}

SOURCE OPINION TEXT:
{opinion_text}

Return a JSON object with:
- citation_format_correct: boolean (is the citation format valid?)
- quote_accuracy: "exact" | "close" | "inaccurate" | null (null if no quote to verify)
- quote_diff: string or null (describe any differences if not exact)
- actual_quote: string or null (the matching passage from the opinion, if found)
- characterization_accuracy: "accurate" | "misleading" | "unsupported" | null (null if no characterization)
- characterization_explanation: string or null (explain your assessment)
- confidence: number 0.0 to 1.0
- overall_status: "verified" | "warning" | "error"
"""


@dataclass
class VerificationResult:
    status: str  # "verified", "warning", "error", "unverifiable"
    citation_exists: bool
    citation_format_correct: bool
    quote_accuracy: Optional[str]  # "exact", "close", "inaccurate"
    quote_diff: Optional[str]
    actual_quote: Optional[str]
    characterization_accuracy: Optional[str]  # "accurate", "misleading", "unsupported"
    characterization_explanation: Optional[str]
    confidence: float


def verify_citation(
    citation: ExtractedCitation,
    opinion_text: str,
) -> VerificationResult:
    """Verify a citation's quotes and characterizations against the source opinion."""
    # Build prompt sections
    quote_section = ""
    if citation.quoted_text:
        quote_section = f'QUOTED TEXT FROM DOCUMENT:\n"{citation.quoted_text}"'
    else:
        quote_section = "QUOTED TEXT: (none — no direct quote used)"

    char_section = ""
    if citation.characterization:
        char_section = f"CHARACTERIZATION FROM DOCUMENT:\n{citation.characterization}"
    else:
        char_section = "CHARACTERIZATION: (none — no characterization to verify)"

    # Truncate opinion text if very long (keep first 80K chars)
    truncated_opinion = opinion_text[:80_000]
    if len(opinion_text) > 80_000:
        truncated_opinion += "\n\n[... opinion text truncated for length ...]"

    prompt = VERIFY_PROMPT.format(
        case_name=citation.case_name,
        citation_text=citation.citation_text,
        quote_section=quote_section,
        characterization_section=char_section,
        opinion_text=truncated_opinion,
    )

    result = call_ai_json(
        messages=[{"role": "user", "content": prompt}],
        system=VERIFY_SYSTEM,
        max_tokens=4096,
        operation_name=f"Verify {citation.citation_text}",
    )

    if not isinstance(result, dict):
        return VerificationResult(
            status="error",
            citation_exists=True,
            citation_format_correct=False,
            quote_accuracy=None,
            quote_diff="AI returned invalid response",
            actual_quote=None,
            characterization_accuracy=None,
            characterization_explanation=None,
            confidence=0.0,
        )

    return VerificationResult(
        status=result.get("overall_status", "error"),
        citation_exists=True,
        citation_format_correct=result.get("citation_format_correct", False),
        quote_accuracy=result.get("quote_accuracy"),
        quote_diff=result.get("quote_diff"),
        actual_quote=result.get("actual_quote"),
        characterization_accuracy=result.get("characterization_accuracy"),
        characterization_explanation=result.get("characterization_explanation"),
        confidence=result.get("confidence", 0.0),
    )


def make_unverifiable_result(reason: str = "Source text not available") -> VerificationResult:
    """Create a result for citations where the source text could not be retrieved."""
    return VerificationResult(
        status="unverifiable",
        citation_exists=False,
        citation_format_correct=True,
        quote_accuracy=None,
        quote_diff=None,
        actual_quote=None,
        characterization_accuracy=None,
        characterization_explanation=reason,
        confidence=0.0,
    )

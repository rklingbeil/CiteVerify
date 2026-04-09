"""Quote and characterization verification against source opinion text.

Uses thorough, multi-step verification with large token budgets for maximum accuracy.
"""

import logging
from dataclasses import dataclass
from typing import Optional

from backend.ai_client import call_ai_json
from backend.citation_extractor import ExtractedCitation

logger = logging.getLogger(__name__)

VERIFY_SYSTEM = """You are an expert legal citation verification specialist. You perform
meticulous, thorough verification of legal citations against source opinions.

You will be given:
1. A citation from a legal document, possibly with a direct quote and/or characterization
2. The full (or partial) text of the source opinion

Your verification process must be THOROUGH and MULTI-STEP:

STEP 1 — CITATION EXISTENCE: Confirm the case exists and the citation format is valid.

STEP 2 — QUOTE VERIFICATION (if a quote is provided):
  a. Search the ENTIRE opinion text for the quoted passage
  b. If found, compare character by character
  c. Standard legal quotation conventions are ACCEPTABLE and should NOT be flagged:
     - Ellipses (...) indicating omitted text
     - Brackets [like this] for editorial insertions or capitalization changes
     - [Emphasis added] or [internal citations omitted] or [internal quotation marks omitted]
     - Minor punctuation differences (period vs. comma at end)
     - Differences in spacing or line breaks
  d. Rate as "exact" (verbatim or only standard legal conventions differ),
     "close" (minor substantive differences that don't change meaning),
     or "inaccurate" (materially different from source)
  e. If the quote cannot be found in the opinion, check if it might be from a
     different opinion cited within this opinion (nested quotation)

STEP 3 — CHARACTERIZATION VERIFICATION (if a characterization is provided):
  a. Read the relevant portions of the opinion carefully
  b. Determine if the characterization accurately describes what the court held
  c. Consider whether the characterization is:
     - "accurate": Correctly describes the holding or reasoning
     - "misleading": Technically contains true elements but omits important context
       or presents the holding in a way that could lead to a wrong impression
     - "unsupported": The opinion does not support this characterization

STEP 4 — CONFIDENCE ASSESSMENT:
  Rate your confidence from 0.0 to 1.0 based on:
  - 0.9-1.0: Quote found verbatim, characterization clearly supported
  - 0.7-0.9: Quote found with minor differences, characterization supported
  - 0.5-0.7: Quote not found but opinion discusses the topic, characterization partially supported
  - 0.3-0.5: Uncertain — opinion text may be incomplete or ambiguous
  - 0.0-0.3: Cannot verify — opinion text doesn't seem related

STEP 5 — OVERALL STATUS:
  - "verified": Quote is exact/close AND characterization is accurate, confidence >= 0.7
  - "warning": Minor issues (close but not exact quote, or partially supported characterization)
  - "error": Materially inaccurate quote or unsupported/misleading characterization"""

VERIFY_PROMPT = """Perform thorough, multi-step verification of this citation.

CITATION: {case_name}, {citation_text}

{quote_section}

{characterization_section}

CONTEXT IN DOCUMENT:
{context}

SOURCE OPINION TEXT ({opinion_length} characters):
{opinion_text}

Carefully verify the quote and characterization against the source opinion using the
multi-step process described in your instructions. Take your time — accuracy matters
more than speed.

Return a JSON object with:
- citation_format_correct: boolean
- quote_accuracy: "exact" | "close" | "inaccurate" | null
- quote_diff: string or null (detailed description of any differences)
- actual_quote: string or null (the matching passage from the opinion, verbatim)
- characterization_accuracy: "accurate" | "misleading" | "unsupported" | null
- characterization_explanation: string or null (detailed explanation of your assessment)
- reasoning: string (your step-by-step reasoning for the verification — be thorough)
- confidence: number 0.0 to 1.0
- overall_status: "verified" | "warning" | "error"
"""

# For citations where we have no source text but can use AI knowledge
KNOWLEDGE_VERIFY_SYSTEM = """You are an expert legal citation verification specialist with
extensive knowledge of case law. You are asked to verify a citation using your own legal
knowledge because the source opinion text could not be retrieved from databases.

Be HONEST about your confidence level:
- If you recognize the case and know its holding, verify with appropriate confidence
- If you are uncertain, say so and rate confidence low
- NEVER fabricate or guess about case holdings you don't know

For well-known cases (Supreme Court cases, landmark circuit decisions), you should be
able to verify with moderate confidence. For obscure or very recent cases, indicate low
confidence."""

KNOWLEDGE_VERIFY_PROMPT = """Verify this citation using your legal knowledge (source text unavailable):

CITATION: {case_name}, {citation_text}

{quote_section}

{characterization_section}

CONTEXT IN DOCUMENT:
{context}

Using your knowledge of this case:
1. Do you recognize this case? Is the citation format correct?
2. If a quote is attributed to this case, can you verify it?
3. If the document characterizes what this case held, is that characterization accurate?

Be honest about uncertainty. Return a JSON object with:
- citation_format_correct: boolean
- quote_accuracy: "exact" | "close" | "inaccurate" | null
- quote_diff: string or null
- actual_quote: string or null
- characterization_accuracy: "accurate" | "misleading" | "unsupported" | null
- characterization_explanation: string or null
- reasoning: string (explain what you know about this case and your verification logic)
- confidence: number 0.0 to 1.0 (be conservative — lower if any uncertainty)
- overall_status: "verified" | "warning" | "error" | "unverifiable"
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
    reasoning: Optional[str] = None


def verify_citation(
    citation: ExtractedCitation,
    opinion_text: str,
) -> VerificationResult:
    """Verify a citation's quotes and characterizations against the source opinion."""
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

    # Use generous truncation — 150K chars
    truncated_opinion = opinion_text[:150_000]
    if len(opinion_text) > 150_000:
        truncated_opinion += "\n\n[... opinion text truncated for length ...]"

    prompt = VERIFY_PROMPT.format(
        case_name=citation.case_name,
        citation_text=citation.citation_text,
        quote_section=quote_section,
        characterization_section=char_section,
        context=citation.context or "(no context available)",
        opinion_length=len(opinion_text),
        opinion_text=truncated_opinion,
    )

    result = call_ai_json(
        messages=[{"role": "user", "content": prompt}],
        system=VERIFY_SYSTEM,
        max_tokens=8192,  # Large budget for thorough reasoning
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
        reasoning=result.get("reasoning"),
    )


def verify_citation_from_knowledge(
    citation: ExtractedCitation,
) -> VerificationResult:
    """Verify a citation using AI's legal knowledge when source text is unavailable."""
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

    prompt = KNOWLEDGE_VERIFY_PROMPT.format(
        case_name=citation.case_name,
        citation_text=citation.citation_text,
        quote_section=quote_section,
        characterization_section=char_section,
        context=citation.context or "(no context available)",
    )

    try:
        result = call_ai_json(
            messages=[{"role": "user", "content": prompt}],
            system=KNOWLEDGE_VERIFY_SYSTEM,
            max_tokens=8192,
            operation_name=f"Knowledge verify {citation.citation_text}",
        )
    except RuntimeError:
        return make_unverifiable_result("AI knowledge verification failed")

    if not isinstance(result, dict):
        return make_unverifiable_result("AI returned invalid response")

    return VerificationResult(
        status=result.get("overall_status", "unverifiable"),
        citation_exists=True,
        citation_format_correct=result.get("citation_format_correct", True),
        quote_accuracy=result.get("quote_accuracy"),
        quote_diff=result.get("quote_diff"),
        actual_quote=result.get("actual_quote"),
        characterization_accuracy=result.get("characterization_accuracy"),
        characterization_explanation=result.get("characterization_explanation"),
        confidence=result.get("confidence", 0.0),
        reasoning=result.get("reasoning"),
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

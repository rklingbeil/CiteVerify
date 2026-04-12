"""Quote and characterization verification against source opinion text.

Uses thorough, multi-step verification with large token budgets for maximum accuracy.
Includes programmatic quote pre-search and AI cross-validation for reliability.
"""

import difflib
import logging
import re
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

STEP 1 — CITATION EXISTENCE AND IDENTITY:
  a. Confirm the case exists and the citation format is valid.
  b. Check if the source opinion actually matches the cited case. If the opinion text
     appears to be for a DIFFERENT case than the one cited (e.g., different parties,
     different subject matter), set overall_status to "error" and explain the mismatch.
     This can happen when a citation volume/page matches a different case in the database.
  c. ABBREVIATION AWARENESS: Legal case names routinely use standard abbreviations that
     vary between sources, courts, reporters, and citation styles. Any recognized legal
     abbreviation and its full form are equivalent and must NOT be treated as a mismatch.
     Common examples include Corp./Corporation, Int'l/International, Dep't/Department,
     Ass'n/Association, Gov't/Government, Mfg./Manufacturing, R.R./Railroad — but these
     are only examples. The full set of legal abbreviations is extensive (see Bluebook
     Table T6) and includes entity types, government bodies, geographic terms, and
     industry-specific terms. When comparing case names, apply your knowledge of ALL
     standard legal abbreviations, not just the examples listed here.

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
confidence.

ABBREVIATION AWARENESS: Legal case names routinely use standard abbreviations that vary
between sources. Any recognized legal abbreviation and its full form are equivalent —
for example, Corp./Corporation, Int'l/International, Dep't/Department, Ass'n/Association,
Gov't/Government, and many others per Bluebook Table T6 and general legal convention.
These are only common examples; apply your full knowledge of legal abbreviations when
identifying cases. "Alice Corp. v. CLS Bank Int'l" is the same case as "Alice Corporation
v. CLS Bank International"."""

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

# ── Pass 2: Adversarial Review Prompts ──────────────────────────────────

VERIFY_REVIEW_SYSTEM = """You are a senior legal citation verification reviewer performing quality
control. You are reviewing verification results produced by an initial analyst and must
independently double-check them against the source opinion text.

Be CRITICAL and THOROUGH — the initial analyst may have made mistakes:

QUOTE RE-VERIFICATION:
- If marked "inaccurate" or the quote was "not found", search the ENTIRE opinion text again.
  Look for: partial matches, reformatted text, quotes split across paragraphs, quotes from
  cases cited within this opinion (nested quotations), OCR artifacts that changed characters.
- If marked "exact", verify it truly IS verbatim — not just similar.
- Pay special attention to whether the actual_quote field matches what's really in the opinion.

CHARACTERIZATION RE-VERIFICATION:
- If "accurate", consider whether important qualifications or context were omitted.
- If "unsupported", search the opinion again for any passage supporting the characterization.
- If "misleading", verify that the characterization truly misrepresents the holding.

ABBREVIATION AWARENESS: Legal case names routinely use standard abbreviations that vary
between sources (e.g., Corp./Corporation, Int'l/International, Dep't/Department, and many
others). These are only common examples — apply your full knowledge of legal abbreviations.
Any abbreviation difference in a case name is NOT an error or mismatch.

Your final assessment may AGREE or DISAGREE with the initial review. Be honest."""

VERIFY_REVIEW_PROMPT = """Review this citation verification for accuracy.

CITATION: {case_name}, {citation_text}

{quote_section}

{characterization_section}

INITIAL VERIFICATION RESULTS:
- Status: {status}
- Quote accuracy: {quote_accuracy}
- Quote diff: {quote_diff}
- Actual quote found: {actual_quote_preview}
- Characterization accuracy: {characterization_accuracy}
- Characterization explanation: {characterization_explanation}
- Confidence: {confidence}
- Reasoning: {reasoning}

SOURCE OPINION TEXT ({opinion_length} characters):
{opinion_text}

Independently verify the quote and characterization. Return a JSON object with:
- citation_format_correct: boolean
- quote_accuracy: "exact" | "close" | "inaccurate" | null
- quote_diff: string or null
- actual_quote: string or null (the matching passage from the opinion, verbatim)
- characterization_accuracy: "accurate" | "misleading" | "unsupported" | null
- characterization_explanation: string or null
- reasoning: string (your review reasoning — reference specific passages)
- confidence: number 0.0 to 1.0
- overall_status: "verified" | "warning" | "error"
- agrees_with_initial: boolean"""

KNOWLEDGE_REVIEW_SYSTEM = """You are reviewing a knowledge-based legal citation verification.
The initial analyst verified this citation using only legal knowledge (no source text available).
Double-check their assessment.

Be particularly critical about:
- Claims about case holdings that might be confused with similar cases
- Quote attributions the analyst claims to recognize — are they really from this case?
- Confidence levels that seem too high for knowledge-based verification

Knowledge-based verification confidence should generally NOT exceed 0.7 unless the case is
extremely well-known (e.g., Brown v. Board of Education, Miranda v. Arizona, Roe v. Wade).

Note: Legal case names routinely use standard abbreviations that vary between sources (e.g.,
Corp./Corporation, Int'l/International, Dep't/Department, and many others). Apply your full
knowledge of legal abbreviations — any such difference is standard convention, not a mismatch."""

KNOWLEDGE_REVIEW_PROMPT = """Review this knowledge-based citation verification.

CITATION: {case_name}, {citation_text}

{quote_section}

{characterization_section}

INITIAL VERIFICATION:
- Status: {status}
- Quote accuracy: {quote_accuracy}
- Characterization accuracy: {characterization_accuracy}
- Confidence: {confidence}
- Reasoning: {reasoning}

Do you agree with this assessment? Return a JSON object with:
- citation_format_correct: boolean
- quote_accuracy: "exact" | "close" | "inaccurate" | null
- quote_diff: string or null
- actual_quote: string or null
- characterization_accuracy: "accurate" | "misleading" | "unsupported" | null
- characterization_explanation: string or null
- reasoning: string
- confidence: number 0.0 to 1.0
- overall_status: "verified" | "warning" | "error" | "unverifiable"
- agrees_with_initial: boolean"""


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
    quote_status: Optional[str] = None  # "verified", "warning", "error"
    characterization_status: Optional[str] = None  # "verified", "warning", "error"


# ── Programmatic Quote Search (#1) ─────────────────────────────────────

def _normalize_for_search(text: str) -> str:
    """Normalize text for comparison: lowercase, collapse whitespace, normalize punctuation."""
    text = text.lower()
    # Remove common legal editorial markers
    text = re.sub(r'\[emphasis added\]', '', text, flags=re.IGNORECASE)
    text = re.sub(r'\[internal (?:citations?|quotation marks?) omitted\]', '', text, flags=re.IGNORECASE)
    text = re.sub(r'\[[\w\s]*omitted\]', '', text, flags=re.IGNORECASE)
    # Normalize Unicode quotes and dashes to ASCII
    text = text.replace('\u2018', "'").replace('\u2019', "'")
    text = text.replace('\u201c', '"').replace('\u201d', '"')
    text = text.replace('\u2013', '-').replace('\u2014', '-')
    # Collapse whitespace
    text = re.sub(r'\s+', ' ', text)
    return text.strip()


def _check_word_proximity(words: list[str], text: str, max_gap: int = 30) -> dict:
    """Check if key content words from a quote appear near each other in the text.

    Catches compressed legal phrases like "mere scintilla" when the actual text
    says "the mere existence of a scintilla of evidence."
    """
    result = {"found": False, "match_type": "not_found", "best_match": None,
              "similarity": 0.0, "position": None}

    # Extract content words (skip very short/common words)
    _STOP_WORDS = {"the", "a", "an", "of", "in", "to", "and", "or", "for", "is",
                   "was", "that", "this", "with", "not", "but", "are", "be", "by",
                   "it", "its", "as", "at", "on", "from", "has", "had", "have"}
    content_words = [w for w in words if len(w) > 2 and w not in _STOP_WORDS]

    if len(content_words) < 2:
        return result

    # Find positions of each content word in the text
    word_positions: dict[str, list[int]] = {}
    for word in content_words:
        positions = []
        start = 0
        while True:
            idx = text.find(word, start)
            if idx == -1:
                break
            positions.append(idx)
            start = idx + 1
        if positions:
            word_positions[word] = positions

    # Need most content words to be present
    if len(word_positions) < len(content_words) * 0.7:
        return result

    # Check if all found words appear within max_gap of each other somewhere
    if len(word_positions) >= 2:
        # Use the least-frequent word as anchor
        anchor_word = min(word_positions, key=lambda w: len(word_positions[w]))
        for anchor_pos in word_positions[anchor_word]:
            nearby_count = 1  # Count anchor itself
            for other_word, positions in word_positions.items():
                if other_word == anchor_word:
                    continue
                # Check if any occurrence of this word is near the anchor
                for pos in positions:
                    if abs(pos - anchor_pos) <= max_gap * len(content_words):
                        nearby_count += 1
                        break

            if nearby_count >= len(word_positions):
                # All words found near each other
                region_start = max(0, anchor_pos - 50)
                region_end = min(len(text), anchor_pos + 100)
                return {
                    "found": True,
                    "match_type": "close_paraphrase",
                    "best_match": text[region_start:region_end],
                    "similarity": 0.75,
                    "position": anchor_pos,
                }

    return result


def _find_quote_in_text(quote: str, text: str) -> dict:
    """Programmatically search for a quote in opinion text.

    Uses exact substring search, then phrase-based fuzzy matching.
    Returns dict with: found, match_type, best_match, similarity, position.
    """
    result = {"found": False, "match_type": "not_found", "best_match": None,
              "similarity": 0.0, "position": None}

    if not quote or not text or len(quote) < 10:
        return result

    norm_quote = _normalize_for_search(quote)
    norm_text = _normalize_for_search(text)

    if not norm_quote or not norm_text:
        return result

    # 1. Exact substring search
    idx = norm_text.find(norm_quote)
    if idx != -1:
        return {"found": True, "match_type": "exact", "best_match": norm_quote,
                "similarity": 1.0, "position": idx}

    # 2. Word proximity check — catches compressed legal phrases like "mere scintilla"
    words = norm_quote.split()
    if len(words) >= 2:
        proximity_result = _check_word_proximity(words, norm_text)
        if proximity_result["found"]:
            return proximity_result

    if len(words) < 4:
        return result

    # Search for 5-word phrases from the quote to find candidate regions
    candidate_positions = []
    phrase_len = min(5, len(words))
    for i in range(0, len(words) - phrase_len + 1, 2):
        phrase = " ".join(words[i:i + phrase_len])
        pos = norm_text.find(phrase)
        if pos != -1:
            candidate_positions.append(pos)

    if not candidate_positions:
        # No phrases found — quote likely doesn't exist in opinion
        return result

    # Cluster around median hit and do precise SequenceMatcher comparison
    candidate_positions.sort()
    median_pos = candidate_positions[len(candidate_positions) // 2]
    window_start = max(0, median_pos - len(norm_quote) // 2)
    window_end = min(len(norm_text), window_start + len(norm_quote) * 2)
    window = norm_text[window_start:window_end]

    # Find best alignment within the window
    best_ratio = 0.0
    qlen = len(norm_quote)
    step = max(1, qlen // 20)
    for offset in range(0, max(1, len(window) - qlen + 1), step):
        candidate = window[offset:offset + qlen + qlen // 5]
        ratio = difflib.SequenceMatcher(None, norm_quote, candidate).ratio()
        if ratio > best_ratio:
            best_ratio = ratio

    result["similarity"] = best_ratio
    if best_ratio >= 0.7:
        match_text = norm_text[window_start:window_start + qlen + 100]
        result.update({"found": True, "match_type": "fuzzy",
                       "best_match": match_text, "position": window_start})

    return result


# ── Pinpoint-Targeted Context (#6) ─────────────────────────────────────

def _extract_pinpoint_context(opinion_text: str, pinpoint: str) -> str | None:
    """Extract text around a pinpoint page reference in the opinion.

    CourtListener plain text uses *NNN page markers.
    """
    if not pinpoint or not opinion_text:
        return None

    page_match = re.match(r'(\d+)', pinpoint.strip())
    if not page_match:
        return None
    page_num = page_match.group(1)

    # Look for page markers in various formats
    patterns = [
        rf'\*{page_num}\s',           # *255 (most common in CL plain text)
        rf'\[{page_num}\]',           # [255]
        rf'Page\s+{page_num}\b',      # Page 255
    ]

    for pattern in patterns:
        matches = list(re.finditer(pattern, opinion_text))
        if matches:
            pos = matches[0].start()
            start = max(0, pos - 1000)
            end = min(len(opinion_text), pos + 5000)
            return opinion_text[start:end]

    return None


# ── AI actual_quote Validation (#4) ────────────────────────────────────

def _validate_ai_actual_quote(result: VerificationResult, opinion_text: str) -> None:
    """Cross-check AI's claimed actual_quote against opinion text. Modifies in place."""
    if not result.actual_quote or not opinion_text:
        return
    if len(result.actual_quote) < 20 or len(opinion_text) < 1000:
        return  # Skip for trivially small texts (e.g. test fixtures)

    norm_actual = _normalize_for_search(result.actual_quote[:300])
    norm_opinion = _normalize_for_search(opinion_text)

    # Quick exact check
    if norm_actual in norm_opinion:
        return  # Confirmed — AI's actual_quote exists in opinion

    # Fuzzy check on first 100 normalized chars
    snippet = norm_actual[:100]
    best_ratio = 0.0
    step = max(1, len(snippet) // 2)
    for i in range(0, max(1, len(norm_opinion) - len(snippet) + 1), step):
        window = norm_opinion[i:i + len(snippet) + 50]
        ratio = difflib.SequenceMatcher(None, snippet, window).ratio()
        if ratio > best_ratio:
            best_ratio = ratio
        if best_ratio > 0.8:
            break  # Good enough

    if best_ratio < 0.5:
        logger.warning(
            f"AI's actual_quote not verified in opinion text (similarity={best_ratio:.2f})"
        )
        result.reasoning = (
            (result.reasoning or "")
            + f" [Note: AI's claimed actual_quote could not be located in opinion text"
            f" (similarity={best_ratio:.0%})]"
        )
        if result.quote_accuracy == "exact":
            result.quote_accuracy = "close"
            result.confidence = min(result.confidence, 0.7)


# ── Element Status Derivation (#7) ─────────────────────────────────────

def _derive_element_statuses(result: VerificationResult) -> None:
    """Derive individual quote_status and characterization_status from accuracy fields."""
    if result.quote_accuracy:
        if result.quote_accuracy == "exact":
            result.quote_status = "verified"
        elif result.quote_accuracy == "close":
            result.quote_status = "warning"
        else:
            result.quote_status = "error"

    if result.characterization_accuracy:
        if result.characterization_accuracy == "accurate":
            result.characterization_status = "verified"
        elif result.characterization_accuracy == "misleading":
            result.characterization_status = "warning"
        else:
            result.characterization_status = "error"


# ── Knowledge Confidence Hard Cap (#8) ─────────────────────────────────

_LANDMARK_CASES = {
    "brown v. board of education",
    "miranda v. arizona",
    "roe v. wade",
    "marbury v. madison",
    "mcculloch v. maryland",
    "gibbons v. ogden",
    "dred scott v. sandford",
    "plessy v. ferguson",
    "griswold v. connecticut",
    "gideon v. wainwright",
    "mapp v. ohio",
    "terry v. ohio",
    "new york times co. v. sullivan",
    "tinker v. des moines",
    "texas v. johnson",
    "citizens united v. federal election commission",
    "obergefell v. hodges",
    "dobbs v. jackson women's health organization",
    "chevron u.s.a. v. natural resources defense council",
    "korematsu v. united states",
}


def _cap_knowledge_confidence(
    result: VerificationResult, case_name: str, has_lookup_mismatch: bool = False,
) -> None:
    """Hard cap on knowledge-based verification confidence.

    When a lookup mismatch occurred (database returned a different case), cap is
    lowered to 0.4 since the citation is likely fabricated.
    """
    if has_lookup_mismatch:
        cap = 0.4
    else:
        name_lower = case_name.lower().strip()
        is_landmark = any(
            landmark in name_lower or name_lower in landmark
            for landmark in _LANDMARK_CASES
        )
        cap = 0.85 if is_landmark else 0.7
    if result.confidence > cap:
        logger.info(f"Capping knowledge confidence from {result.confidence:.2f} to {cap} for {case_name}"
                     + (" (lookup mismatch)" if has_lookup_mismatch else ""))
        result.confidence = cap


def _truncate_at_sentence(text: str, max_chars: int) -> str:
    """Truncate text at the nearest sentence boundary before max_chars."""
    if len(text) <= max_chars:
        return text
    # Search backward from max_chars for a sentence-ending punctuation
    search_start = max(0, max_chars - 500)
    candidate = text[:max_chars]
    # Find last sentence boundary (. ! ? followed by space or newline)
    last_boundary = -1
    for m in re.finditer(r'[.!?]\s', candidate[search_start:]):
        last_boundary = search_start + m.end()
    if last_boundary > 0:
        return text[:last_boundary].rstrip() + "\n\n[... opinion text truncated for length ...]"
    # No sentence boundary found — cut at max_chars
    return text[:max_chars] + "\n\n[... opinion text truncated for length ...]"


# ── Holdings Extraction (#5) ──────────────────────────────────────────

_HOLDING_PATTERNS = [
    r'[Ww]e (?:hold|conclude|decide|determine|rule) that\b',
    r'[Tt]he (?:Court|court) (?:holds?|concludes?|decides?|rules?) that\b',
    r'[Ww]e (?:reverse|affirm|remand|vacate)\b',
    r'[Tt]he judgment (?:of the|is)\b',
    r'[Ii]t is (?:so )?ordered\b',
    r'[Aa]ccordingly,? we\b',
]


def _extract_holdings(opinion_text: str, max_excerpts: int = 3) -> str | None:
    """Extract holding-related passages from opinion text for focused verification."""
    if not opinion_text or len(opinion_text) < 500:
        return None

    excerpts = []
    seen_positions: set[int] = set()

    for pattern in _HOLDING_PATTERNS:
        for match in re.finditer(pattern, opinion_text):
            pos = match.start()
            # Avoid overlapping excerpts
            if any(abs(pos - sp) < 500 for sp in seen_positions):
                continue
            seen_positions.add(pos)
            # Extract ~500 chars around the holding sentence
            start = max(0, opinion_text.rfind('.', max(0, pos - 200), pos) + 1)
            end = min(len(opinion_text), opinion_text.find('.', pos + 100, pos + 800) + 1)
            if end <= start:
                end = min(len(opinion_text), pos + 500)
            excerpt = opinion_text[start:end].strip()
            if excerpt:
                excerpts.append(excerpt)
            if len(excerpts) >= max_excerpts:
                break
        if len(excerpts) >= max_excerpts:
            break

    return "\n---\n".join(excerpts) if excerpts else None


# ── Vague Characterization Detection (#6) ────────────────────────────

_VAGUE_VERBS = {"addressed", "discussed", "refined", "analyzed", "examined",
                "considered", "recognized", "noted", "observed", "stated"}

_VAGUE_OBJECTS = {"this", "that", "the", "their", "such", "analysis",
                  "issue", "matter", "question", "point", "topic"}


def _is_vague_characterization(characterization: str) -> bool:
    """Detect characterizations too vague to verify (e.g., 'refined this analysis')."""
    if not characterization:
        return False
    words = characterization.lower().split()
    # Very short characterizations with only relational/vague verbs
    if len(words) <= 5:
        content_words = [w for w in words if len(w) > 3]
        if content_words and all(
            w in _VAGUE_VERBS or w in _VAGUE_OBJECTS
            for w in content_words
        ):
            return True
    return False


# ── Main Verification Functions ──────────────────────────���─────────────

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

    # Use generous truncation — 150K chars, cut at sentence boundary
    truncated_opinion = _truncate_at_sentence(opinion_text, 150_000)

    prompt = VERIFY_PROMPT.format(
        case_name=citation.case_name,
        citation_text=citation.citation_text,
        quote_section=quote_section,
        characterization_section=char_section,
        context=citation.context or "(no context available)",
        opinion_length=len(opinion_text),
        opinion_text=truncated_opinion,
    )

    # (#1) Programmatic quote pre-search — give AI evidence to work with
    if citation.quoted_text and len(opinion_text) > 100:
        pre_search = _find_quote_in_text(citation.quoted_text, opinion_text)
        if pre_search["match_type"] == "exact":
            prompt += (
                f"\n\nPROGRAMMATIC SEARCH RESULT: The quoted text was found VERBATIM "
                f"in the opinion text. This confirms the quote exists in the source."
            )
        elif pre_search["match_type"] == "fuzzy":
            prompt += (
                f"\n\nPROGRAMMATIC SEARCH RESULT: A similar passage was found "
                f"(similarity={pre_search['similarity']:.0%}). Best match: "
                f"\"{(pre_search['best_match'] or '')[:500]}\""
            )
        elif pre_search["match_type"] == "close_paraphrase":
            prompt += (
                f"\n\nPROGRAMMATIC SEARCH RESULT: Key words from the quote were found "
                f"in close proximity in the opinion (likely a paraphrase or compressed "
                f"quotation). Region: \"{(pre_search['best_match'] or '')[:500]}\". "
                f"Rate as 'close' if the meaning is preserved even if wording differs."
            )
        else:
            prompt += (
                f"\n\nPROGRAMMATIC SEARCH RESULT: The quoted text was NOT found in the "
                f"opinion text (best similarity={pre_search['similarity']:.0%}). This may "
                f"indicate a fabricated or significantly altered quote."
            )

    # (#6) Pinpoint-targeted context — focus AI on the right section
    if citation.pinpoint:
        pinpoint_ctx = _extract_pinpoint_context(opinion_text, citation.pinpoint)
        if pinpoint_ctx:
            prompt += (
                f"\n\nPINPOINT PAGE CONTEXT (text around page {citation.pinpoint}):\n"
                f"{pinpoint_ctx[:5000]}"
            )

    # (#5) Holdings excerpt — focus characterization verification on key passages
    if citation.characterization and len(opinion_text) > 2000:
        holdings = _extract_holdings(opinion_text)
        if holdings:
            prompt += (
                f"\n\nKEY HOLDING PASSAGES (extracted from opinion):\n{holdings[:3000]}"
            )

    # (#6) Vague characterization hint
    if citation.characterization and _is_vague_characterization(citation.characterization):
        prompt += (
            f"\n\nNOTE: The characterization is very vague and does not describe a "
            f"specific holding, test, or rule. If you cannot determine what specific "
            f"legal proposition is being attributed to this case, rate as 'unsupported'."
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

    _VALID_STATUSES = {"verified", "warning", "error", "unverifiable"}
    status = result.get("overall_status", "error")
    if status not in _VALID_STATUSES:
        logger.warning(f"AI returned invalid status '{status}', defaulting to 'error'")
        status = "error"
    confidence = max(0.0, min(1.0, float(result.get("confidence", 0.0))))

    initial = VerificationResult(
        status=status,
        citation_exists=True,
        citation_format_correct=result.get("citation_format_correct", False),
        quote_accuracy=result.get("quote_accuracy"),
        quote_diff=result.get("quote_diff"),
        actual_quote=result.get("actual_quote"),
        characterization_accuracy=result.get("characterization_accuracy"),
        characterization_explanation=result.get("characterization_explanation"),
        confidence=confidence,
        reasoning=result.get("reasoning"),
    )

    # (#4) Validate AI's claimed actual_quote against opinion text
    _validate_ai_actual_quote(initial, opinion_text)

    # Pass 2: Adversarial review
    if citation.quoted_text or citation.characterization:
        logger.info(f"Pass 2: reviewing verification for {citation.case_name}")
        reviewed = _review_verification(citation, opinion_text, initial)
        _derive_element_statuses(reviewed)
        return reviewed

    _derive_element_statuses(initial)
    return initial


def verify_citation_from_knowledge(
    citation: ExtractedCitation,
    lookup_context: str = "",
    has_lookup_mismatch: bool = False,
) -> VerificationResult:
    """Verify a citation using AI's legal knowledge when source text is unavailable.

    Args:
        citation: The extracted citation to verify.
        lookup_context: Context from the lookup phase (e.g., mismatch info).
        has_lookup_mismatch: True if the lookup returned a different case name.
    """
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

    # (#1) Propagate lookup mismatch signal — critical for fake case detection
    if lookup_context:
        prompt += f"\n\n{lookup_context}"

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

    _VALID_STATUSES = {"verified", "warning", "error", "unverifiable"}
    status = result.get("overall_status", "unverifiable")
    if status not in _VALID_STATUSES:
        logger.warning(f"AI returned invalid status '{status}', defaulting to 'unverifiable'")
        status = "unverifiable"
    confidence = max(0.0, min(1.0, float(result.get("confidence", 0.0))))

    initial = VerificationResult(
        status=status,
        citation_exists=True,
        citation_format_correct=result.get("citation_format_correct", True),
        quote_accuracy=result.get("quote_accuracy"),
        quote_diff=result.get("quote_diff"),
        actual_quote=result.get("actual_quote"),
        characterization_accuracy=result.get("characterization_accuracy"),
        characterization_explanation=result.get("characterization_explanation"),
        confidence=confidence,
        reasoning=result.get("reasoning"),
    )

    # Pass 2: Review knowledge verification
    if citation.quoted_text or citation.characterization:
        logger.info(f"Pass 2: reviewing knowledge verification for {citation.case_name}")
        reviewed = _review_knowledge_verification(citation, initial)
        # (#8) Hard cap on knowledge confidence (stricter when lookup mismatch)
        _cap_knowledge_confidence(reviewed, citation.case_name, has_lookup_mismatch)
        _derive_element_statuses(reviewed)
        return reviewed

    _cap_knowledge_confidence(initial, citation.case_name, has_lookup_mismatch)
    _derive_element_statuses(initial)
    return initial


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


# ── Pass 2 Implementation ───────────────────────────────────────────────

def _review_verification(
    citation: ExtractedCitation,
    opinion_text: str,
    initial: VerificationResult,
) -> VerificationResult:
    """Pass 2: Adversarial review of source-based verification."""
    truncated_opinion = _truncate_at_sentence(opinion_text, 150_000)

    quote_section = (
        f'QUOTED TEXT FROM DOCUMENT:\n"{citation.quoted_text}"'
        if citation.quoted_text
        else "QUOTED TEXT: (none)"
    )
    char_section = (
        f"CHARACTERIZATION FROM DOCUMENT:\n{citation.characterization}"
        if citation.characterization
        else "CHARACTERIZATION: (none)"
    )
    actual_quote_preview = "none"
    if initial.actual_quote:
        actual_quote_preview = (
            initial.actual_quote[:300] + "..."
            if len(initial.actual_quote) > 300
            else initial.actual_quote
        )

    prompt = VERIFY_REVIEW_PROMPT.format(
        case_name=citation.case_name,
        citation_text=citation.citation_text,
        quote_section=quote_section,
        characterization_section=char_section,
        status=initial.status,
        quote_accuracy=initial.quote_accuracy or "not assessed",
        quote_diff=initial.quote_diff or "none",
        actual_quote_preview=actual_quote_preview,
        characterization_accuracy=initial.characterization_accuracy or "not assessed",
        characterization_explanation=initial.characterization_explanation or "none",
        confidence=initial.confidence,
        reasoning=initial.reasoning or "none",
        opinion_length=len(opinion_text),
        opinion_text=truncated_opinion,
    )

    try:
        result = call_ai_json(
            messages=[{"role": "user", "content": prompt}],
            system=VERIFY_REVIEW_SYSTEM,
            max_tokens=8192,
            operation_name=f"Review verify {citation.citation_text}",
        )
    except RuntimeError:
        logger.warning(f"Review verification failed for {citation.citation_text}; using initial result")
        return initial

    if not isinstance(result, dict):
        return initial

    return _apply_review(initial, result, "review")


def _review_knowledge_verification(
    citation: ExtractedCitation,
    initial: VerificationResult,
) -> VerificationResult:
    """Pass 2: Review of knowledge-based verification."""
    quote_section = (
        f'QUOTED TEXT FROM DOCUMENT:\n"{citation.quoted_text}"'
        if citation.quoted_text
        else "QUOTED TEXT: (none)"
    )
    char_section = (
        f"CHARACTERIZATION FROM DOCUMENT:\n{citation.characterization}"
        if citation.characterization
        else "CHARACTERIZATION: (none)"
    )

    prompt = KNOWLEDGE_REVIEW_PROMPT.format(
        case_name=citation.case_name,
        citation_text=citation.citation_text,
        quote_section=quote_section,
        characterization_section=char_section,
        status=initial.status,
        quote_accuracy=initial.quote_accuracy or "not assessed",
        characterization_accuracy=initial.characterization_accuracy or "not assessed",
        confidence=initial.confidence,
        reasoning=initial.reasoning or "none",
    )

    try:
        result = call_ai_json(
            messages=[{"role": "user", "content": prompt}],
            system=KNOWLEDGE_REVIEW_SYSTEM,
            max_tokens=4096,
            operation_name=f"Review knowledge verify {citation.citation_text}",
        )
    except RuntimeError:
        logger.warning(f"Knowledge review failed for {citation.citation_text}; using initial result")
        return initial

    if not isinstance(result, dict):
        return initial

    return _apply_review(initial, result, "knowledge review")


def _apply_review(
    initial: VerificationResult,
    review: dict,
    label: str,
) -> VerificationResult:
    """Apply review results — confirm or override the initial verification."""
    _VALID_STATUSES = {"verified", "warning", "error", "unverifiable"}
    agrees = review.get("agrees_with_initial", True)

    if agrees:
        logger.info(f"Pass 2 ({label}) confirms initial assessment")
        # Boost confidence slightly when reviewer agrees
        boosted = min(1.0, initial.confidence + 0.05)
        return VerificationResult(
            status=initial.status,
            citation_exists=initial.citation_exists,
            citation_format_correct=initial.citation_format_correct,
            quote_accuracy=initial.quote_accuracy,
            quote_diff=initial.quote_diff,
            actual_quote=initial.actual_quote,
            characterization_accuracy=initial.characterization_accuracy,
            characterization_explanation=initial.characterization_explanation,
            confidence=boosted,
            reasoning=f"[Confirmed by {label}] {initial.reasoning or ''}",
        )

    # Reviewer disagrees — use reviewer's assessment
    logger.info(f"Pass 2 ({label}) OVERRIDES initial assessment")
    status = review.get("overall_status", initial.status)
    if status not in _VALID_STATUSES:
        status = initial.status
    confidence = max(0.0, min(1.0, float(review.get("confidence", initial.confidence))))

    return VerificationResult(
        status=status,
        citation_exists=initial.citation_exists,
        citation_format_correct=review.get("citation_format_correct", initial.citation_format_correct),
        quote_accuracy=review.get("quote_accuracy", initial.quote_accuracy),
        quote_diff=review.get("quote_diff", initial.quote_diff),
        actual_quote=review.get("actual_quote", initial.actual_quote),
        characterization_accuracy=review.get("characterization_accuracy", initial.characterization_accuracy),
        characterization_explanation=review.get("characterization_explanation", initial.characterization_explanation),
        confidence=confidence,
        reasoning=f"[{label.capitalize()} override] {review.get('reasoning', '')}",
    )

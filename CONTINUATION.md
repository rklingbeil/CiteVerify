# CiteVerify — Session Continuation Document

*Last updated: 2026-04-11*

## What This Is

A standalone legal citation verification web app. Upload a legal document (PDF/DOCX), and the system:

1. Extracts all citations, quotes, and case characterizations (2-pass AI extraction)
2. Resolves Id./supra references and validates citation plausibility
3. Looks up each cited case via CourtListener and GovInfo APIs
4. Retrieves the full opinion text (preferring majority opinions)
5. Programmatically pre-searches for quotes in opinion text before AI verification
6. Verifies quotes and characterizations with 2-pass AI verification + AI output cross-validation
7. Runs a constrained cross-citation consistency check across the entire document
8. Generates a report with color-coded results and per-element (quote/characterization) statuses

**Target:** ~6 users with HTTP Basic Auth. Standalone deployment (separate from LCIS).

## Current Status (2026-04-11)

| Component | Status |
|-----------|--------|
| Backend (16 modules) | **Complete** — all fixes + 31 accuracy improvements applied |
| Frontend (React/Vite) | **Complete** — upload, progress, report view |
| Tests (170 total) | **Passing** — all green |
| Auth (HTTP Basic + rate limiting) | **Complete** |
| Docker (multi-stage + nginx) | **Complete** — Dockerfile + docker-compose.yml |
| Accuracy testing | **Complete** — 3 test briefs, results below |
| Cloud deployment | **Not started** — needs DigitalOcean droplet |

### Accuracy Test Results (Pre-Session-2 — before latest 8 improvements)

**Brief A — All Real Cases (15 citations)**
- 14 verified, 1 warning, 0 errors, 0 unverifiable
- Previous (pre-session-1): 8 verified, 0 warnings, 7 errors

**Brief B — Fabricated Quotes (9 citations)**
- 2 verified, 3 warnings, 4 errors, 0 unverifiable
- All fabricated content correctly caught (Twombly fake quote, Iqbal "automatic inference", Palsgraf wrong characterization, Miranda broadened scope, East River altered quote)
- Saratoga Fishing (control case) correctly verified
- **Known issues fixed in session 2:**
  - Id. references ("Id. at 444", "Id. at 871") looked up as literal text, matched "Adar Bays v. GeneSYS **ID**" — **fixed by #25 Id. resolution**
  - Palsgraf (fundamentally wrong characterization) downgraded from error→warning by consistency check — **fixed by #26 constrained consistency**
  - Miranda (broadened scope characterization) also wrongly downgraded — **fixed by #26**

**Brief C — Mixed Real + Fake (16 citations)**
- 7 verified, 3 warnings, 2 errors, 4 unverifiable
- Case name mismatch detection correctly identified 8 citations where CourtListener returned wrong cases
- All real cases verified; hallucinated cases caught or marked unverifiable

### Expected Impact of Session 2 Improvements

| Issue | Before | After (expected) |
|-------|--------|-------------------|
| "Id. at 444" matches "Adar Bays v. GeneSYS ID" | 3 wrong lookups in Brief B | Resolved to parent citation, correct lookup |
| Palsgraf wrong characterization | Downgraded error→warning | Stays error (blocked by constraint) |
| Miranda broadened scope | Downgraded error→warning | Stays error (blocked by constraint) |
| AI claims to find non-existent quotes | No cross-check | actual_quote validated against opinion text |
| Knowledge-based confidence too high | Prompt says 0.7 cap, not enforced | Hard cap: 0.7 general, 0.85 landmark |
| F.3d cited before 1993 | Goes to CourtListener, gets wrong case | Caught as implausible, skipped |
| Quote exists verbatim but AI uncertain | AI searches entire 150K opinion | AI told "FOUND VERBATIM" by pre-search |

**Re-run accuracy tests to confirm:** `python tests/test_briefs/run_accuracy_test.py --brief all`

## Architecture

```
React (port 5173) → FastAPI (port 8000) → AI + Case Law APIs
```

**No database.** Reports are in-memory (dict keyed by job ID), auto-purged after 24 hours.

## Verification Pipeline (7 Steps)

```
Step 1: Extract text from PDF/DOCX (PyMuPDF / python-docx)
Step 2: Extract citations via AI — 2-pass (extract + review)
         └─ Id./supra/pinpoint resolution (resolves to parent citation_text)
         └─ Citation plausibility validation (reporter/year sanity check)
Step 3: Look up each citation — CourtListener → GovInfo
         └─ Lookup cache: same citation_text reuses results
         └─ Majority opinion preference (020lead > 040dissent)
Step 4: Verify citations with source text — 2-pass (verify + adversarial review)
         └─ Case name mismatch detection (skip wrong-case opinions)
         └─ Programmatic quote pre-search (exact + fuzzy, result sent to AI)
         └─ Pinpoint-targeted context (focused window around *NNN page markers)
         └─ AI actual_quote cross-validation (catch AI hallucination)
         └─ Element status derivation (separate quote_status + characterization_status)
Step 5: Knowledge-based verification for unverifiable — 2-pass (verify + review)
         └─ Hard confidence cap (0.7 general, 0.85 for ~20 landmark cases)
Step 6: Cross-citation consistency check — single AI call reviews all results
         └─ Constrained: blocks error→warning downgrades on substantive issues
Step 7: Assemble report
```

**Total AI calls per citation (worst case):** 2 extraction + 2 verification + 1 consistency = 5 calls

## File Structure

```
backend/
├── main.py              — FastAPI app, CORS, health, SPA catch-all, global error handler
├── config.py            — Env config (API keys, model, rate limits)
├── auth.py              — HTTP Basic Auth (bcrypt) + sliding window rate limiter
├── ai_client.py         — Anthropic client: retry, JSON parsing, thread-safe singleton
├── extractor.py         — PDF (PyMuPDF) + DOCX (python-docx) text extraction
├── citation_extractor.py — 2-pass AI citation/quote/characterization extraction
├── source_lookup.py     — CourtListener citation-lookup + search + GovInfo fallback
├── verifier.py          — 2-pass AI verification + pre-search + AI cross-validation
├── pipeline.py          — Orchestrator: 7-step pipeline with Id. resolution + consistency
├── jobs.py              — In-memory job manager (ThreadPoolExecutor)
├── pdf_export.py        — ReportLab PDF report generation
└── routers/
    ├── upload.py        — POST /api/upload (auth + rate limited)
    ├── jobs.py          — GET /api/jobs/{id} (auth required)
    └── reports.py       — GET /api/reports/{id} + /api/reports/{id}/pdf (auth required)

frontend/src/
├── main.tsx, App.tsx, globals.css
├── api/client.ts        — Axios client (baseURL: /api)
├── types/index.ts       — TypeScript interfaces
└── components/
    ├── UploadPanel.tsx   — Drag-and-drop file upload
    ├── ProgressPanel.tsx — Job progress bar
    ├── ReportView.tsx    — Split-pane report layout
    ├── DocumentPane.tsx  — Left: document text with highlighted citations
    ├── SourcePane.tsx    — Right: source opinion + verification details
    ├── CitationCard.tsx  — Individual citation detail card
    └── SummaryBar.tsx    — Stats bar (verified/warnings/errors/unverifiable)

tests/
├── conftest.py          — Shared fixtures (client, _make_citation)
├── test_ai_client.py    — JSON extraction, retry, thread safety
├── test_api.py          — API endpoint tests
├── test_citation_extractor.py — Extraction + review pass
├── test_extractor.py    — PDF/DOCX extraction, encrypted PDF
├── test_fixes.py        — All code review fixes + accuracy improvements (76 tests)
├── test_jobs.py         — Job lifecycle
├── test_pdf_export.py   — PDF generation
├── test_pipeline.py     — Pipeline orchestration
├── test_source_lookup.py — CourtListener/GovInfo lookup
├── test_verifier.py     — Verification + review pass
└── test_briefs/
    ├── create_test_briefs.py  — Generate 3 test DOCX files
    ├── run_accuracy_test.py   — Run pipeline on test briefs
    ├── brief_a_all_real.docx
    ├── brief_b_fabricated_quotes.docx
    ├── brief_c_mixed_real_fake.docx
    └── report_a/b/c.json     — Latest accuracy results

Root:
├── .env                 — API keys + model config
├── .env.example         — Template
├── pyproject.toml       — pytest config
├── requirements.txt     — Production deps (11 packages)
├── requirements-dev.txt — Dev deps (pytest, httpx)
├── Dockerfile           — Multi-stage (Node 22 build + Python 3.13)
├── docker-compose.yml   — app + nginx services
└── CONTINUATION.md      — This file
```

## Environment Variables (.env)

```
ANTHROPIC_API_KEY=sk-ant-...            # Required
COURTLISTENER_API_TOKEN=...             # Required (5000 req/hr)
GOVINFO_API_KEY=...                     # Optional, default DEMO_KEY
CLAUDE_MODEL=claude-opus-4-20250514     # MUST be Opus — accuracy is everything
AI_PROVIDER=anthropic                   # Only anthropic supported currently
DISABLE_DOCS=false                      # Set true in production
CORS_ORIGINS=http://localhost:5173      # Comma-separated origins
CITEVERIFY_USERS=                       # Empty = dev mode (no auth)
# Format: "user1:$2b$12$bcrypt_hash1,user2:$2b$12$bcrypt_hash2"
```

**Critical:** CiteVerify MUST use Claude Opus (not Sonnet). Accuracy is the top priority — attorneys rely on this tool. The .env already has `CLAUDE_MODEL=claude-opus-4-20250514`.

## API Endpoints

| Method | Path | Auth | Purpose |
|--------|------|------|---------|
| POST | /api/upload | Yes + rate limit | Upload PDF/DOCX, returns job_id |
| GET | /api/jobs/{id} | Yes | Poll job status + progress |
| GET | /api/reports/{id} | Yes | Get completed report JSON |
| GET | /api/reports/{id}/pdf | Yes | Download PDF report |
| GET | /api/health | No | Health check |

## All Fixes Applied (31 total)

### P0 — Critical (1-6)
1. **HTML entity decoding** (`source_lookup.py`): `html.unescape()` replaces broken regex that destroyed smart quotes
2. **setattr allowlist** (`citation_extractor.py`): AI review pass can only modify whitelisted fields
3. **Status/confidence validation** (`verifier.py`): AI-returned status validated against enum, confidence clamped [0,1]
4. **Truncated JSON recovery** (`ai_client.py`): Filters incomplete objects from recovered arrays
5. **Auth** (`auth.py`): HTTP Basic Auth with bcrypt + sliding window rate limiter
6. **Rate limiting** on upload endpoint (10/hr default)

### P1 — High (7-17)
7. **Pipeline step 5 try/except** (`pipeline.py`): Knowledge verification errors don't kill pipeline
8. **Job state locking** (`jobs.py`): All mutations under `_lock`
9. **Position int casting** (`citation_extractor.py`): AI may return strings
10. **Thread-safe AI client** (`ai_client.py`): Double-checked locking singleton
11. **Content-Disposition sanitization** (`reports.py`): Prevents header injection
12. **Citation regex fix** (`source_lookup.py`): Handles multi-word reporters (S. Ct., L. Ed. 2d)
13. **Dedup bucket size** (`citation_extractor.py`): Increased from 500 to 2000 chars
14. **CORS from env var** (`main.py`): `CORS_ORIGINS` env var
15. **Encrypted PDF detection** (`extractor.py`): Clear error message
16. **DOCX footnote/endnote extraction** (`extractor.py`)
17. **page_count bug** (`extractor.py`): Uses `len(doc)` not text-page count

### Accuracy — Session 1 (18-23)
18. **Lookup cache** (`pipeline.py`): Id./supra references reuse parent lookup
19. **Majority opinion preference** (`source_lookup.py`): `_fetch_best_opinion_from_urls()` prefers 010combined/015unanamous/020lead/025plurality
20. **Case name mismatch detection** (`pipeline.py`): `_case_names_match()` fuzzy party-name matching skips wrong-case verification
21. **Wrong-case detection prompt** (`verifier.py`): STEP 1b tells AI to check if opinion matches cited case
22. **Pass 2: Adversarial review** (`verifier.py`): `_review_verification()` + `_review_knowledge_verification()` double-check each result
23. **Pass 3: Cross-citation consistency** (`pipeline.py`): `_check_cross_citation_consistency()` reviews all results together

### Accuracy — Session 2 (24-31)
24. **Programmatic quote pre-search** (`verifier.py`): `_find_quote_in_text()` uses exact substring + phrase-based fuzzy matching before AI verification. Tells AI whether quote was found, fuzzy-matched, or absent — reduces AI guessing.
25. **Id./supra resolution** (`pipeline.py`): `_resolve_id_supra_references()` resolves "Id. at 444", "supra", and bare pinpoints ("556 U.S. at 678") to their parent citation_text before lookup. Fixes broken CourtListener lookups on "Id." text.
26. **Constrained consistency check** (`pipeline.py`): Blocks error→warning downgrades when quote is "inaccurate" or characterization is "unsupported"/"misleading". Previously the consistency check was incorrectly softening real errors (Palsgraf, Miranda).
27. **AI actual_quote cross-validation** (`verifier.py`): `_validate_ai_actual_quote()` verifies the AI's claimed "actual_quote" actually exists in the opinion text. Catches AI hallucination of matching passages.
28. **Citation plausibility validation** (`pipeline.py`): `validate_citation_plausibility()` checks reporter/year combos (e.g., F.3d didn't exist before 1993) to catch fabricated citations before API calls.
29. **Pinpoint-targeted context** (`verifier.py`): `_extract_pinpoint_context()` finds `*NNN` page markers in CourtListener opinion text and sends a focused ~6K char window to the AI alongside the full text.
30. **Separate quote/characterization status** (`verifier.py`): `_derive_element_statuses()` adds `quote_status` and `characterization_status` fields to VerificationResult. Attorneys see which element passed/failed independently.
31. **Knowledge confidence hard cap** (`verifier.py`): `_cap_knowledge_confidence()` enforces max 0.7 for non-landmark cases, 0.85 for ~20 landmark cases (Miranda, Brown v. Board, Marbury, etc.). Prompt guidance was not being enforced programmatically.

## Data Model

### ExtractedCitation
```python
@dataclass
class ExtractedCitation:
    citation_text: str          # "477 U.S. 242 (1986)" — full cite, no "at" pinpoints
    case_name: str              # "Anderson v. Liberty Lobby, Inc."
    full_reference: str         # Exactly as written in document
    quoted_text: Optional[str]  # Direct quote from cited case
    characterization: Optional[str]  # How document describes holding
    context: str                # Surrounding paragraph
    position_start: int         # Character offset in document
    position_end: int
    pinpoint: Optional[str]     # "242" or "44-46"
```

### LookupResult
```python
@dataclass
class LookupResult:
    found: bool
    status: str                 # "found", "not_found", "error"
    case_name: str
    court: str
    date_filed: str
    cluster_id: int | None
    opinion_id: int | None
    opinion_text: str | None    # Full opinion (plain text or stripped HTML)
    url: str                    # Link to CourtListener
    source: str                 # "courtlistener" or "govinfo"
```

### VerificationResult
```python
@dataclass
class VerificationResult:
    status: str                          # "verified" | "warning" | "error" | "unverifiable"
    citation_exists: bool
    citation_format_correct: bool
    quote_accuracy: Optional[str]        # "exact" | "close" | "inaccurate"
    quote_diff: Optional[str]
    actual_quote: Optional[str]
    characterization_accuracy: Optional[str]  # "accurate" | "misleading" | "unsupported"
    characterization_explanation: Optional[str]
    confidence: float                    # 0.0–1.0
    reasoning: Optional[str]             # Prefixed with [Confirmed by review], [Review override], [Consistency: ...]
    quote_status: Optional[str]          # "verified" | "warning" | "error" — derived from quote_accuracy
    characterization_status: Optional[str]  # "verified" | "warning" | "error" — derived from characterization_accuracy
```

## Running Locally

```bash
# Terminal 1: Backend
cd ~/Projects/citeverify
source venv/bin/activate
uvicorn backend.main:app --reload --port 8000

# Terminal 2: Frontend
cd ~/Projects/citeverify/frontend
npm run dev

# Run tests (170 total)
source venv/bin/activate && python -m pytest tests/ -v

# Run accuracy tests on test briefs
source venv/bin/activate && python tests/test_briefs/run_accuracy_test.py --brief all
```

## Remaining Work

### Deployment (Next Priority)
1. **DigitalOcean droplet** — $6/mo, separate from LCIS (161.35.116.252)
2. **DNS + HTTPS** — Choose domain, Let's Encrypt
3. **Set CITEVERIFY_USERS** — Create bcrypt hashes for ~6 users
4. **Set DISABLE_DOCS=true** in production
5. **Set CORS_ORIGINS** to production domain

### Re-run Accuracy Tests
- The 8 new improvements (session 2) have not been tested against the test briefs yet
- Run `python tests/test_briefs/run_accuracy_test.py --brief all` to measure impact
- Expected: Brief B Id. references fixed, Palsgraf/Miranda stay as errors, knowledge confidence capped

### Future Enhancements
- **Multi-LLM ensemble** — Add OpenAI + Gemini for consensus verification (needs API keys)
- **Statutory/regulatory citation support** — Currently handles case law only
- **Negative treatment detection** — "overruled by", "abrogated by" via CourtListener citing_cluster data
- **Jurisdiction-specific rules** — Per roadmap

## Test Briefs

Three test briefs in `tests/test_briefs/`:

| Brief | Content | Purpose |
|-------|---------|---------|
| A | 9 real cases (Celotex, Anderson, Virginia Pharmacy, Central Hudson, Mathews, Goldberg, Harlow, Ashcroft, Pearson) | All should verify — tests for false negatives |
| B | 6 real cases with fabricated quotes/characterizations (Twombly fake quote, Iqbal "automatic inference", Palsgraf wrong holding, Miranda broadened, East River altered, Saratoga Fishing control) | Should catch all fabrications |
| C | Mix of 6 real + 5 hallucinated cases (Daubert, Kumho, Darling, Burrage, BMW, State Farm real; Whitfield, Morrison, Rodriguez, Kellerman, Chen fake) | Should verify real, flag fake |

Run with: `python tests/test_briefs/run_accuracy_test.py --brief a|b|c|all`

## Key Design Decisions

- **Opus only** — Accuracy is everything. Claude Opus, not Sonnet.
- **2-pass extraction** — Extract then AI reviews its own work for missed citations
- **2-pass verification** — Initial verify, then adversarial review catches false positives/negatives
- **3-pass pipeline** — Per-citation verify + review, then document-wide consistency check
- **Programmatic pre-search before AI** — Fuzzy text matching tells AI whether quote exists, reducing hallucination
- **Id./supra resolution before lookup** — Prevents CourtListener from matching "Id." as a case name
- **Constrained consistency** — Consistency check can upgrade but cannot downgrade substantive errors
- **AI output cross-validation** — actual_quote field verified against opinion text programmatically
- **Plausibility validation** — Reporter/year sanity check catches fabrications before API calls
- **Knowledge confidence caps** — Hard enforcement (0.7 general, 0.85 landmark) not just prompt guidance
- **Lookup caching** — Same `citation_text` reuses lookup result (handles resolved Id./supra references)
- **Majority opinion preference** — When CourtListener returns multiple opinions, prefer 010combined/020lead over 040dissent
- **Case name mismatch guard** — If lookup returns a different case name, skip source verification and fall back to knowledge-based
- **No database** — Reports are ephemeral in-memory. Job auto-purge after 24 hours.
- **Rate limiting** — CourtListener 0.75s/req, GovInfo 0.1s/req, upload 10/hr/user

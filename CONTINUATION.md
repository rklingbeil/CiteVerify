# CiteVerify — Project Continuation Document

## What This Is

A standalone legal citation verification web app. Upload a legal document (PDF/DOCX), and the system:

1. Extracts all citations, quotes, and case characterizations
2. Looks up each cited case via CourtListener and GovInfo APIs
3. Retrieves the full opinion text
4. Uses AI to verify quote accuracy and characterization fairness
5. Generates an interactive report with color-coded citations

**Single user tool** for Rick. No auth, no multi-tenancy.

## Current Status

### Backend — COMPLETE (Phase 1-2)

All core backend modules are written and ready:

```
backend/
├── main.py              — FastAPI app with CORS, health check, SPA catch-all
├── config.py            — Environment config (API keys, model, limits)
├── ai_client.py         — Anthropic client with retry, JSON parsing, streaming threshold
├── extractor.py         — PDF (PyMuPDF) and DOCX (python-docx) text extraction
├── citation_extractor.py — AI-powered citation/quote/characterization extraction
├── source_lookup.py     — CourtListener citation-lookup + opinion fetch + GovInfo fallback
├── verifier.py          — AI quote/characterization comparison against source text
├── pipeline.py          — Orchestrator: extract → lookup → verify → report
├── jobs.py              — In-memory job manager (ThreadPoolExecutor, no database)
└── routers/
    ├── upload.py        — POST /api/upload (file upload + job creation)
    ├── jobs.py          — GET /api/jobs/{id} (poll status/progress)
    └── reports.py       — GET /api/reports/{id} (fetch completed report)
```

### Frontend — NOT STARTED (Phase 3)

Needs to be built. React + Vite + TypeScript with CSS Modules.

### Tests — NOT STARTED

Need `tests/` with pytest tests for each backend module.

### Deployment — NOT STARTED (Phase 4)

Needs Dockerfile, docker-compose.yml, nginx config, DigitalOcean droplet setup.

## Architecture

```
React (port 5173) → FastAPI (port 8000) → AI + Case Law APIs
```

**No database.** Reports are in-memory (dict keyed by job ID), auto-purged after 24 hours.

## Tech Stack

- **Backend:** FastAPI, Python 3.13
- **Frontend:** React 19, Vite, TypeScript, CSS Modules
- **AI:** Anthropic Claude (start), extensible to OpenAI + Gemini
- **Case Law:** CourtListener API + GovInfo API
- **Deploy:** DigitalOcean droplet (separate from LCIS)

## API Endpoints

| Method | Path | Purpose |
|--------|------|---------|
| POST | /api/upload | Upload PDF/DOCX, returns job_id |
| GET | /api/jobs/{id} | Poll job status + progress |
| GET | /api/reports/{id} | Get completed verification report |
| GET | /api/health | Health check |

## Environment Variables (.env)

```
ANTHROPIC_API_KEY=sk-ant-...          # Required
COURTLISTENER_API_TOKEN=...           # Required (5000 req/hr)
GOVINFO_API_KEY=...                   # Optional, default DEMO_KEY (36000 req/hr)
CLAUDE_MODEL=claude-sonnet-4-20250514 # Default, can use opus for higher accuracy
AI_PROVIDER=anthropic                 # anthropic | openai | gemini (future)
MAX_UPLOAD_SIZE_MB=50
UPLOAD_DIR=/tmp/citeverify_uploads
JOB_TTL_HOURS=24
```

**API credentials are already configured in LCIS** at `/Users/rick/Projects/lcis/.env` — copy `COURTLISTENER_API_TOKEN` and `GOVINFO_API_KEY` from there.

## Verification Pipeline Flow

```
1. User uploads document
   └─→ POST /api/upload → saves file, creates job, returns job_id

2. Background job runs:
   a. extract_document(file_path)     → plain text
   b. extract_citations(text)         → list[ExtractedCitation] via Claude
   c. For each citation:
      └─→ lookup_citation(text)       → CourtListener → GovInfo fallback
          └─→ _fetch_opinion_text()   → full opinion text
   d. For each citation with source text:
      └─→ verify_citation(citation, opinion_text) → VerificationResult via Claude
   e. Assemble VerificationReport

3. User polls GET /api/jobs/{id} until status=completed
   └─→ GET /api/reports/{report_id} → full report JSON
```

## Data Model

### ExtractedCitation (from AI)
- citation_text: "325 Or App 648"
- case_name: "Smith v. Jones"
- full_reference: complete reference as written
- quoted_text: exact quote from document (or null)
- characterization: how document describes the holding (or null)
- context: surrounding paragraph
- position_start/end: character offsets in document

### LookupResult (from APIs)
- found: bool
- case_name, court, date_filed
- cluster_id, opinion_id (CourtListener)
- opinion_text: full opinion text (or null)
- url: link to source
- source: "courtlistener" or "govinfo"

### VerificationResult (from AI)
- status: "verified" | "warning" | "error" | "unverifiable"
- citation_format_correct: bool
- quote_accuracy: "exact" | "close" | "inaccurate" | null
- quote_diff: description of differences
- actual_quote: matching passage from opinion
- characterization_accuracy: "accurate" | "misleading" | "unsupported" | null
- characterization_explanation: AI's reasoning
- confidence: 0.0–1.0

## Frontend Specification (TO BUILD)

### App States
Three states: `upload` → `processing` → `report`

### UploadPanel
- Drag-and-drop file zone accepting PDF/DOCX
- "Verify" button → POST /api/upload
- Shows file name and size after selection

### ProgressPanel
- Polls GET /api/jobs/{id} every 2 seconds
- Progress bar (0-100%)
- Current step message from backend
- Error display with "Try Again"

### ReportView (Main UI)

```
┌───────────────────────────────────────────────────────────┐
│ SummaryBar: "12 citations: 8 verified, 2 warnings, 2 errors"   │
├─────────────────────────┬─────────────────────────────────┤
│                         │                                 │
│   DocumentPane          │   SourcePane                    │
│   (left, 50%)           │   (right, 50%)                  │
│                         │                                 │
│   Full document text    │   When citation clicked:        │
│   with highlighted      │   - Source opinion text          │
│   citations:            │   - Matched quote highlighted   │
│                         │   - Verification details        │
│   Green = verified      │   - Case name, court, date      │
│   Yellow = warning      │   - Link to CourtListener       │
│   Red = error           │                                 │
│   Gray = unverifiable   │                                 │
│                         │                                 │
│   Click to select       │                                 │
│                         │                                 │
└─────────────────────────┴─────────────────────────────────┘
```

### Components
- `UploadPanel.tsx` + `.module.css` — file upload
- `ProgressPanel.tsx` + `.module.css` — job progress
- `ReportView.tsx` + `.module.css` — split-pane layout
- `DocumentPane.tsx` + `.module.css` — left pane with highlighted text
- `SourcePane.tsx` + `.module.css` — right pane with source details
- `CitationCard.tsx` + `.module.css` — citation detail card
- `SummaryBar.tsx` + `.module.css` — stats bar at top
- `api/client.ts` — axios instance (baseURL: /api, timeout: 300000, no auth)
- `types/index.ts` — TypeScript interfaces mirroring backend data model

### Frontend Dependencies
```json
{
  "react": "^19",
  "react-dom": "^19",
  "axios": "^1",
  "typescript": "^5",
  "vite": "^7",
  "@vitejs/plugin-react": "^4"
}
```

Node 22+ required (.nvmrc).

## Running Locally

```bash
# Terminal 1: Backend
cd ~/Projects/citeverify
python -m venv venv && source venv/bin/activate
pip install -r requirements.txt
cp .env.example .env  # Fill in API keys
uvicorn backend.main:app --reload --port 8000

# Terminal 2: Frontend
cd ~/Projects/citeverify/frontend
npm install
npm run dev
```

## Deployment Plan (DigitalOcean)

1. Create a new $6/mo droplet (separate from LCIS at 161.35.116.252)
2. Dockerfile: Python 3.13-slim + Node 22 build stage (same pattern as LCIS)
3. docker-compose.yml: single `app` service + nginx
4. nginx: HTTPS via Let's Encrypt, proxy to uvicorn
5. No database, no Redis, no Qdrant — just the app container + nginx

## Multi-LLM Plan (Future)

Currently `AI_PROVIDER=anthropic` only. To add GPT and Gemini:

1. Add `OPENAI_API_KEY` and `GEMINI_API_KEY` to config
2. Add provider functions in `ai_client.py` (same pattern as LCIS `api_client.py`)
3. For verification, run all three providers independently on each citation
4. Flag disagreements: if 2/3 providers say a quote is inaccurate, that's a strong signal
5. Report shows per-provider results with consensus indicator

## Key Design Notes

- **Rate limiting:** CourtListener at 0.75s/request, GovInfo at 0.1s/request — thread-safe throttle
- **Long documents:** Citation extraction splits into 80K-char chunks with 5K overlap, deduplicates
- **Opinion text:** Prefer `plain_text`, fall back to `html_with_citations` (strip tags). Some old cases have no machine-readable text → marked "unverifiable"
- **Legal quote conventions:** The AI prompt explicitly accounts for ellipses, brackets, [sic], [emphasis added] — these are NOT errors
- **No database needed:** Reports are ephemeral. If persistence is ever wanted, add a single SQLite table
- **Timeout:** AI calls use streaming for max_tokens > 16K to avoid Anthropic SDK's non-streaming timeout

## LCIS References (patterns, not shared code)

These LCIS files were referenced for design patterns:
- `/Users/rick/Projects/lcis/case_law.py` — CourtListener/GovInfo integration
- `/Users/rick/Projects/lcis/ingestion/extractors.py` — PDF/DOCX extraction
- `/Users/rick/Projects/lcis/api_client.py` — Anthropic client with retry
- `/Users/rick/Projects/lcis/backend/jobs.py` — Background job pattern

## Remaining Work

1. **Frontend** — Build all React components listed above
2. **Tests** — pytest tests for extractor, citation_extractor, source_lookup, verifier, pipeline, API endpoints
3. **PDF export** — Report export endpoint (weasyprint or reportlab)
4. **Dockerfile + docker-compose.yml** — Containerization
5. **DigitalOcean deployment** — Droplet, nginx, HTTPS, DNS
6. **Multi-LLM** — Add OpenAI and Gemini providers

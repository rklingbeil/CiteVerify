"""CiteVerify configuration — loaded from environment variables."""

import os
from dotenv import load_dotenv

load_dotenv()

# ─── AI Provider ──────────────────────────────────────────────────────────
ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY", "")
CLAUDE_MODEL = os.getenv("CLAUDE_MODEL", "claude-sonnet-4-20250514")
AI_PROVIDER = os.getenv("AI_PROVIDER", "anthropic")  # anthropic | openai | gemini

# ─── Case Law APIs ───────────────────────────────────────────────────────
COURTLISTENER_API_TOKEN = os.getenv("COURTLISTENER_API_TOKEN", "")
COURTLISTENER_BASE = "https://www.courtlistener.com/api/rest/v4"

GOVINFO_API_KEY = os.getenv("GOVINFO_API_KEY", "DEMO_KEY")
GOVINFO_BASE = "https://api.govinfo.gov"

# Rate limits (seconds between requests)
CL_MIN_INTERVAL = 0.75   # CourtListener: ~5000 req/hr
GI_MIN_INTERVAL = 0.1    # GovInfo: ~36000 req/hr

# ─── Upload ──────────────────────────────────────────────────────────────
MAX_UPLOAD_SIZE_MB = int(os.getenv("MAX_UPLOAD_SIZE_MB", "50"))
UPLOAD_DIR = os.getenv("UPLOAD_DIR", "/tmp/citeverify_uploads")

# ─── Jobs ────────────────────────────────────────────────────────────────
JOB_TTL_HOURS = int(os.getenv("JOB_TTL_HOURS", "24"))

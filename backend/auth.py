"""Simple authentication for CiteVerify cloud deployment.

Supports ~6 users with username/password via HTTP Basic Auth.
Credentials stored as bcrypt hashes in CITEVERIFY_USERS env var.

Format: CITEVERIFY_USERS="user1:$2b$12$hash1,user2:$2b$12$hash2"

Generate a hash: python -c "import bcrypt; print(bcrypt.hashpw(b'password', bcrypt.gensalt()).decode())"
"""

import logging
import os
import time
from collections import defaultdict
from threading import Lock
from typing import Optional

from fastapi import Depends, HTTPException, Request, status
from fastapi.security import HTTPBasic, HTTPBasicCredentials

logger = logging.getLogger(__name__)

security = HTTPBasic(auto_error=False)

# ─── Credential Store ─────────────────────────────────────────────────────

_users: Optional[dict[str, str]] = None  # username -> bcrypt hash


def _load_users() -> dict[str, str]:
    """Parse CITEVERIFY_USERS env var into {username: hash} dict."""
    global _users
    if _users is not None:
        return _users

    raw = os.getenv("CITEVERIFY_USERS", "")
    _users = {}
    if not raw:
        logger.warning("CITEVERIFY_USERS not set — authentication disabled (dev mode)")
        return _users

    for entry in raw.split(","):
        entry = entry.strip()
        if ":" not in entry:
            continue
        username, password_hash = entry.split(":", 1)
        _users[username.strip()] = password_hash.strip()

    logger.info(f"Loaded {len(_users)} user(s) for authentication")
    return _users


def _check_password(plain: str, hashed: str) -> bool:
    """Verify a plaintext password against a bcrypt hash."""
    import bcrypt
    return bcrypt.checkpw(plain.encode("utf-8"), hashed.encode("utf-8"))


# ─── Rate Limiting ────────────────────────────────────────────────────────

_rate_lock = Lock()
_rate_store: dict[str, list[float]] = defaultdict(list)

UPLOAD_RATE_LIMIT = int(os.getenv("UPLOAD_RATE_LIMIT", "10"))  # per hour
UPLOAD_RATE_WINDOW = 3600  # 1 hour
GENERAL_RATE_LIMIT = int(os.getenv("GENERAL_RATE_LIMIT", "120"))  # per minute
GENERAL_RATE_WINDOW = 60  # 1 minute


def _check_rate(key: str, limit: int, window: int) -> bool:
    """Sliding window rate limiter. Returns True if request is allowed."""
    now = time.time()
    with _rate_lock:
        timestamps = _rate_store[key]
        # Remove expired entries
        cutoff = now - window
        _rate_store[key] = [t for t in timestamps if t > cutoff]
        if len(_rate_store[key]) >= limit:
            return False
        _rate_store[key].append(now)
        return True


# ─── FastAPI Dependencies ─────────────────────────────────────────────────

def verify_auth(
    request: Request,
    credentials: Optional[HTTPBasicCredentials] = Depends(security),
) -> str:
    """Authenticate user via HTTP Basic Auth. Returns username.

    If CITEVERIFY_USERS is not set, authentication is skipped (dev mode).
    """
    users = _load_users()

    # Dev mode — no users configured
    if not users:
        return "dev"

    if not credentials:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authentication required",
            headers={"WWW-Authenticate": "Basic"},
        )

    password_hash = users.get(credentials.username)
    if not password_hash or not _check_password(credentials.password, password_hash):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid credentials",
            headers={"WWW-Authenticate": "Basic"},
        )

    # General rate limit
    if not _check_rate(f"general:{credentials.username}", GENERAL_RATE_LIMIT, GENERAL_RATE_WINDOW):
        raise HTTPException(status_code=429, detail="Rate limit exceeded")

    return credentials.username


def check_upload_rate(username: str) -> None:
    """Check upload-specific rate limit. Call from upload endpoint."""
    if not _check_rate(f"upload:{username}", UPLOAD_RATE_LIMIT, UPLOAD_RATE_WINDOW):
        raise HTTPException(status_code=429, detail="Upload rate limit exceeded (max {}/hour)".format(UPLOAD_RATE_LIMIT))

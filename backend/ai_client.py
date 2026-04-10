"""AI provider abstraction — Anthropic (Claude) with extensibility for GPT/Gemini."""

import json
import logging
import re
import time
from typing import Any, Dict, List, Optional

from anthropic import Anthropic, APIError, RateLimitError

from backend.config import ANTHROPIC_API_KEY, CLAUDE_MODEL, AI_PROVIDER

logger = logging.getLogger(__name__)

_client: Optional[Anthropic] = None

DEFAULT_MAX_RETRIES = 5
DEFAULT_BASE_DELAY = 2.0


def get_client() -> Anthropic:
    """Thread-safe singleton Anthropic client."""
    global _client
    if _client is None:
        if not ANTHROPIC_API_KEY:
            raise RuntimeError("ANTHROPIC_API_KEY is not set")
        _client = Anthropic(api_key=ANTHROPIC_API_KEY)
    return _client


def call_ai(
    messages: List[Dict[str, str]],
    system: Optional[str] = None,
    max_tokens: int = 4096,
    max_retries: int = DEFAULT_MAX_RETRIES,
    operation_name: str = "AI call",
) -> str:
    """Call AI provider with retry on transient failures."""
    if AI_PROVIDER != "anthropic":
        raise NotImplementedError(f"Provider '{AI_PROVIDER}' not yet supported")

    client = get_client()
    last_error: Optional[Exception] = None

    for attempt in range(max_retries):
        try:
            kwargs: Dict[str, Any] = {
                "model": CLAUDE_MODEL,
                "max_tokens": max_tokens,
                "messages": messages,
                "temperature": 0,
            }
            if system:
                kwargs["system"] = system

            # Use streaming for large responses to avoid SDK timeout
            if max_tokens > 8_000:
                with client.messages.stream(**kwargs) as stream:
                    return stream.get_final_text()
            else:
                response = client.messages.create(**kwargs)
                if not response.content:
                    raise RuntimeError(f"{operation_name}: empty response from AI")
                return response.content[0].text

        except RateLimitError as e:
            last_error = e
            delay = min(DEFAULT_BASE_DELAY * (2 ** attempt), 30)
            logger.warning(f"{operation_name}: rate limited, retrying in {delay}s")
            time.sleep(delay)

        except APIError as e:
            if e.status_code and e.status_code >= 500:
                last_error = e
                delay = min(DEFAULT_BASE_DELAY * (2 ** attempt), 30)
                logger.warning(f"{operation_name}: server error {e.status_code}, retrying in {delay}s")
                time.sleep(delay)
            else:
                raise

        except Exception as e:
            last_error = e
            logger.error(f"{operation_name}: unexpected error: {e}")
            break

    raise RuntimeError(f"{operation_name} failed after {max_retries} retries: {last_error}")


def strip_code_fences(text: str) -> str:
    """Strip markdown code fences from AI responses."""
    text = text.strip()
    text = re.sub(r"^```(?:json)?\s*\n?", "", text)
    text = re.sub(r"\n?```\s*$", "", text)
    return text.strip()


def extract_json(text: str) -> Any:
    """Extract JSON from text that may contain surrounding prose.

    Tries in order:
    1. Direct parse after stripping code fences
    2. Extract content between code fences
    3. Find the first { or [ and match to its closing bracket
    4. Recover truncated JSON arrays
    """
    # 1. Direct parse
    cleaned = strip_code_fences(text)
    try:
        return json.loads(cleaned)
    except json.JSONDecodeError:
        pass

    # 2. Extract from code fences anywhere in text
    fence_match = re.search(r"```(?:json)?\s*\n(.*?)\n```", text, re.DOTALL)
    if fence_match:
        try:
            return json.loads(fence_match.group(1).strip())
        except json.JSONDecodeError:
            pass

    # 3. Find first { or [ and match to closing bracket
    for start_char, end_char in [('{', '}'), ('[', ']')]:
        start_idx = cleaned.find(start_char)
        if start_idx == -1:
            continue
        # Walk forward counting brackets to find the matching close
        depth = 0
        in_string = False
        escape_next = False
        for i in range(start_idx, len(cleaned)):
            ch = cleaned[i]
            if escape_next:
                escape_next = False
                continue
            if ch == '\\' and in_string:
                escape_next = True
                continue
            if ch == '"' and not escape_next:
                in_string = not in_string
                continue
            if in_string:
                continue
            if ch == start_char:
                depth += 1
            elif ch == end_char:
                depth -= 1
                if depth == 0:
                    candidate = cleaned[start_idx:i + 1]
                    try:
                        return json.loads(candidate)
                    except json.JSONDecodeError:
                        break

    # 4. Recover truncated JSON arrays
    if cleaned.rstrip().endswith(","):
        recovered = cleaned.rstrip().rstrip(",") + "]"
        try:
            return json.loads(recovered)
        except json.JSONDecodeError:
            pass

    raise json.JSONDecodeError("No valid JSON found in response", text, 0)


def call_ai_json(
    messages: List[Dict[str, str]],
    system: Optional[str] = None,
    max_tokens: int = 4096,
    operation_name: str = "AI call",
) -> Any:
    """Call AI and parse response as JSON with recovery."""
    json_hint = "IMPORTANT: Respond with ONLY valid JSON. No prose, no markdown, no explanation — just the JSON object or array."
    full_system = f"{system}\n\n{json_hint}" if system else json_hint

    for attempt in range(2):
        raw = call_ai(messages, system=full_system, max_tokens=max_tokens, operation_name=operation_name)
        try:
            return extract_json(raw)
        except (json.JSONDecodeError, ValueError):
            if attempt == 0:
                logger.warning(f"{operation_name}: JSON parse failed, retrying. Raw: {raw[:200]}")
                continue
            logger.error(f"{operation_name}: JSON parse failed after retry. Raw: {raw[:500]}")
            raise RuntimeError(f"{operation_name}: could not parse JSON response")

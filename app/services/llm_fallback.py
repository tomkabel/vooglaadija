"""LLM fallback extractor for unsupported or broken yt-dlp extractors.

When yt-dlp fails with an extraction error (Unsupported URL, extractor breakage,
or dynamically generated content), this module fetches the raw page HTML and
asks an LLM to identify direct stream URLs (.mp4, .m3u8, .mpd) or underlying
JSON API endpoints. Discovered URLs are handed back to the core downloader.

Design notes (KEEP):
- Opt-in via ``LLM_FALLBACK_ENABLED`` — must never run implicitly on every job.
- Provider-agnostic: uses OpenAI-compatible chat completions (OpenRouter,
  OpenAI, local LLMs via litellm-compatible endpoints).
- Sanitized prompt: strips scripts/styles, truncates to context window.
- URL validation: SSRF check + scheme whitelist before handing to downloader.
- Structured output: JSON-mode response with explicit URL + format fields.
"""

from __future__ import annotations

import json
import re
from typing import Any
from urllib.parse import urlparse

import httpx

from app.utils.validators import validate_url_not_ssrf
from core.config import settings
from core.logging_config import get_logger

logger = get_logger(__name__)

# Prompt template sent to the LLM. The model receives the page URL and a
# sanitized excerpt of the HTML body, and must respond with JSON containing
# the direct media URL and its format.
_EXTRACT_PROMPT = """You are a media URL extractor. Given a webpage URL and its HTML content, identify direct stream URLs for video or media content.

Respond ONLY with a JSON object in this exact format:
{{"url": "<direct_media_url>", "format": "<mp4|m3u8|mpd|json|title>", "title": "<optional_title>"}}

Rules:
- The URL must be a DIRECT link to a media file or manifest (.mp4, .m3u8, .mpd) OR a JSON API endpoint that returns such URLs.
- If you find a .m3u8 or .mpd manifest URL, return it directly.
- If you find a JSON API endpoint (containing 'json', 'api', 'player', 'sources', 'streamingData'), return it with format "json".
- If no direct media URL is found, respond with: {{"url": null, "format": "none", "title": null}}
- Prefer direct .mp4 URLs when available, then .m3u8, then .mpd, then JSON APIs.
- Do not include any explanation — ONLY the JSON object.

Page URL: {url}

HTML Content (truncated):
{html_content}
"""

# Maximum HTML content to send to LLM (chars). Keeps prompts within context
# window limits for most models while still capturing enough structure.
_MAX_HTML_CHARS = 15000

# Whitelisted URL schemes for the discovered media URL
_ALLOWED_SCHEMES = frozenset({"http", "https"})


class LLMFallbackError(Exception):
    """Raised when the LLM fallback extraction fails."""


class LLMFallbackResult:
    """Result from LLM fallback extraction."""

    __slots__ = ("url", "format", "title")

    def __init__(self, url: str | None, format: str, title: str | None = None) -> None:
        self.url = url
        self.format = format
        self.title = title

    @property
    def found(self) -> bool:
        return self.url is not None and self.format != "none"


async def _fetch_page_html(url: str, timeout: float = 30.0) -> str:
    """Fetch raw HTML content from a URL.

    Uses a standard User-Agent to avoid trivial blocks. Returns empty string
    on any failure — the caller decides how to handle it.
    """
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/125.0.0.0 Safari/537.36"
        ),
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.5",
    }
    try:
        async with httpx.AsyncClient(
            timeout=httpx.Timeout(timeout),
            follow_redirects=True,
            trust_env=False,
        ) as client:
            response = await client.get(url, headers=headers)
            response.raise_for_status()
            return response.text
    except httpx.HTTPError as e:
        logger.warning("llm_fallback_fetch_failed", url=url[:80], error=str(e))
        return ""
    except Exception as e:
        logger.warning("llm_fallback_fetch_unexpected", url=url[:80], error=str(e))
        return ""


def _sanitize_html(html: str) -> str:
    """Sanitize HTML for LLM prompt: remove scripts, styles, comments, and collapse whitespace.

    The goal is to reduce token count while preserving structure and any
    embedded JSON/config objects that might contain media URLs.
    """
    # Remove <script>...</script> tags and their contents
    html = re.sub(r"<script[^>]*>.*?</script>", "", html, flags=re.DOTALL | re.IGNORECASE)
    # Remove <style>...</style> tags and their contents
    html = re.sub(r"<style[^>]*>.*?</style>", "", html, flags=re.DOTALL | re.IGNORECASE)
    # Remove HTML comments
    html = re.sub(r"<!--.*?-->", "", html, flags=re.DOTALL)
    # Remove noscript, iframe, object, embed tags (but keep their content if any)
    html = re.sub(r"</?(?:noscript|iframe|object|embed|svg)[^>]*>", "", html, flags=re.IGNORECASE)
    # Collapse whitespace
    html = re.sub(r"\s+", " ", html).strip()
    # Remove HTML tags but keep their text content
    html = re.sub(r"<[^>]+>", " ", html)
    # Collapse whitespace again after tag removal
    html = re.sub(r"\s+", " ", html).strip()
    return html[:_MAX_HTML_CHARS]


def _validate_discovered_url(url: str) -> bool:
    """Validate a discovered media URL for safety.

    Checks:
    - Scheme is http/https
    - URL is well-formed
    - Does not resolve to a private/internal IP (SSRF check)
    """
    try:
        parsed = urlparse(url)
    except (ValueError, TypeError):
        return False

    if parsed.scheme not in _ALLOWED_SCHEMES:
        return False

    if not parsed.hostname:
        return False

    # Reject obviously internal hostnames
    hostname = parsed.hostname.lower()
    if hostname in ("localhost", "127.0.0.1", "0.0.0.0", "::1"):
        return False
    if hostname.startswith("169.254.") or hostname.startswith("10."):
        return False
    if hostname.startswith("192.168.") or hostname.startswith("172."):
        return False

    return True


async def _call_llm_provider(prompt: str) -> str:
    """Send prompt to the configured LLM provider and return the response text.

    Uses OpenAI-compatible chat completions endpoint. Configuration:
    - LLM_FALLBACK_API_KEY: API key for the provider
    - LLM_FALLBACK_API_BASE: Base URL (default: https://openrouter.ai/api/v1)
    - LLM_FALLBACK_MODEL: Model name (default: google/gemini-2.0-flash-001)
    """
    api_key = settings.llm_fallback_api_key
    if not api_key:
        raise LLMFallbackError("LLM_FALLBACK_API_KEY not configured")

    api_base = settings.llm_fallback_api_base.rstrip("/")
    model = settings.llm_fallback_model

    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
        "HTTP-Referer": settings.llm_fallback_referer or "https://github.com/tomkabel/vooglaadija",
        "X-Title": "Vooglaadija LLM Fallback",
    }

    payload = {
        "model": model,
        "messages": [
            {
                "role": "system",
                "content": "You are a media URL extraction assistant. Respond ONLY with valid JSON.",
            },
            {
                "role": "user",
                "content": prompt,
            },
        ],
        "temperature": 0.1,
        "max_tokens": 512,
        "response_format": {"type": "json_object"},
    }

    endpoint = f"{api_base}/chat/completions"

    try:
        async with httpx.AsyncClient(
            timeout=httpx.Timeout(
                connect=15.0,
                read=60.0,
                write=15.0,
                pool=15.0,
            ),
            trust_env=False,
        ) as client:
            response = await client.post(endpoint, headers=headers, json=payload)
            response.raise_for_status()
            data = response.json()

        content = data["choices"][0]["message"]["content"]
        return content

    except httpx.HTTPError as e:
        raise LLMFallbackError(f"LLM API request failed: {e}") from e
    except (KeyError, IndexError, json.JSONDecodeError) as e:
        raise LLMFallbackError(f"Unexpected LLM response format: {e}") from e


def _parse_llm_response(response_text: str) -> LLMFallbackResult:
    """Parse the LLM's JSON response into an LLMFallbackResult."""
    # Try to extract JSON from the response (handle markdown code blocks)
    text = response_text.strip()
    if text.startswith("```"):
        # Extract from markdown code block
        match = re.search(r"```(?:json)?\s*\n?(.*?)\n?```", text, re.DOTALL)
        if match:
            text = match.group(1).strip()

    try:
        data = json.loads(text)
    except json.JSONDecodeError as e:
        logger.warning("llm_fallback_json_parse_failed", response=response_text[:200])
        raise LLMFallbackError(f"Failed to parse LLM response as JSON: {e}") from e

    url = data.get("url")
    format_type = data.get("format", "none")
    title = data.get("title")

    if url is None or format_type == "none":
        return LLMFallbackResult(url=None, format="none", title=None)

    return LLMFallbackResult(url=url, format=format_type, title=title)


async def extract_with_llm_fallback(url: str) -> LLMFallbackResult:
    """Attempt to extract a media URL using the LLM fallback mechanism.

    This is the main entry point for the LLM fallback. It:
    1. Fetches the page HTML
    2. Sanitizes it and sends to the LLM
    3. Parses the response to find a direct media URL
    4. Validates the discovered URL

    Returns an LLMFallbackResult. If no media URL is found, the `found`
    property will be False.
    """
    if not settings.llm_fallback_enabled:
        logger.debug("llm_fallback_disabled", url=url[:80])
        return LLMFallbackResult(url=None, format="none", title=None)

    if not _validate_discovered_url(url):
        # The original URL itself must pass basic validation
        logger.warning("llm_fallback_invalid_source_url", url=url[:80])
        return LLMFallbackResult(url=None, format="none", title=None)

    logger.info("llm_fallback_started", url=url[:80])

    # Fetch page HTML
    html = await _fetch_page_html(url)
    if not html:
        logger.info("llm_fallback_no_html", url=url[:80])
        return LLMFallbackResult(url=None, format="none", title=None)

    # Sanitize
    sanitized = _sanitize_html(html)
    if not sanitized:
        logger.info("llm_fallback_empty_after_sanitize", url=url[:80])
        return LLMFallbackResult(url=None, format="none", title=None)

    # Build prompt
    prompt = _EXTRACT_PROMPT.format(url=url, html_content=sanitized)

    # Call LLM
    try:
        response_text = await _call_llm_provider(prompt)
    except LLMFallbackError as e:
        logger.warning("llm_fallback_provider_error", url=url[:80], error=str(e))
        return LLMFallbackResult(url=None, format="none", title=None)

    # Parse response
    result = _parse_llm_response(response_text)

    if not result.found:
        logger.info("llm_fallback_no_media_found", url=url[:80])
        return result

    # Validate discovered URL
    if not _validate_discovered_url(result.url):
        logger.warning(
            "llm_fallback_invalid_discovered_url",
            url=url[:80],
            discovered=result.url[:100] if result.url else None,
        )
        return LLMFallbackResult(url=None, format="none", title=None)

    # SSRF check on discovered URL
    if not await validate_url_not_ssrf(result.url):
        logger.warning(
            "llm_fallback_ssrf_blocked",
            url=url[:80],
            discovered=result.url[:100],
        )
        return LLMFallbackResult(url=None, format="none", title=None)

    logger.info(
        "llm_fallback_success",
        url=url[:80],
        discovered_format=result.format,
        discovered_url=result.url[:100],
    )
    return result


def is_llm_fallback_available() -> bool:
    """Check if the LLM fallback is enabled and properly configured."""
    return settings.llm_fallback_enabled and bool(settings.llm_fallback_api_key)

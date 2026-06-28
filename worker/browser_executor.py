"""Browser downloader microservice HTTP client.

Phase 2 worker integration: the worker calls the standalone Node.js
microservice at `packages/browser-downloader/` (Phase 0) over HTTP for
platforms that yt-dlp cannot handle (TikTok, Instagram, Twitter/X).

This module owns three concerns:
1. Translate `POST :3000/download` responses into the worker's existing
   `(file_path, file_name, title)` tuple shape.
2. Map the microservice's structured error codes into the existing
   `app.services.error_classifier.ErrorCategory` values so the existing
   retry/DLQ pipeline handles them.
3. Wrap every call in a named circuit breaker so a flaky downstream
   cannot stall the worker.

Design notes (KEEP):
- Single `BrowserExecutorError` exception type carries `category` and
  `signal`. The job executor passes these to the existing retry machinery
  unchanged.
- The error-code → category mapping is centralized in
  `_map_response_to_category` — every failure path goes through it.
- The circuit breaker is a named singleton alongside
  `get_youtube_circuit_breaker` in `app.services.circuit_breaker`.
- No progress streaming (microservice is single-shot HTTP); the worker
  passes `progress_callback=None` from the caller.
"""

from __future__ import annotations

import json
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlparse

import httpx

from app.services.circuit_breaker import (
    CircuitBreaker,
    CircuitBreakerOpenError,
)
from app.services.error_classifier import ErrorCategory
from core.config import settings
from core.logging_config import get_logger

logger = get_logger(__name__)


class BrowserExecutorError(Exception):
    """Failure with a classified retry category and a stable signal.

    The `category` value is consumed by the existing retry/DLQ pipeline in
    `worker/job_executor.py`; the `signal` value is the original microservice
    error code (or a synthetic marker for transport failures) preserved for
    observability and tests.

    The `str(...)` representation embeds a marker that the existing
    `app.services.error_classifier.classify_error` regex patterns can match,
    so the typed `category` is corroborated by the string-based classifier
    used in `worker/retry_scheduler.evaluate`.
    """

    _CATEGORY_MARKERS: dict[ErrorCategory, str] = {
        ErrorCategory.BLOCKED: "blocked (anti-bot or DRM)",
        ErrorCategory.NOT_FOUND: "404 not found",
        ErrorCategory.TIMEOUT: "Request timeout",
        ErrorCategory.TRANSIENT: "network error (transient)",
        ErrorCategory.UNKNOWN: "unknown error",
    }

    def __init__(self, category: ErrorCategory, signal: str) -> None:
        self.category = category
        self.signal = signal
        marker = self._CATEGORY_MARKERS.get(category, "transient error")
        super().__init__(f"{marker}: {signal}")


@dataclass(slots=True)
class _HttpResponse:
    """Minimal shape for httpx responses we read here.

    Allows tests to inject a fake without standing up an httpx transport.
    The real path passes through `httpx.Response` (duck-typed).
    """

    status_code: int
    body: bytes


ProgressCallback = Callable[[dict], Awaitable[None]]


# -- Singleton client & breaker -------------------------------------------

_client: httpx.AsyncClient | None = None
_breaker: CircuitBreaker | None = None


def _get_client() -> httpx.AsyncClient:
    """Lazy singleton httpx client.

    `trust_env=False` so the worker does not pick up a stray HTTP_PROXY
    from the environment and route internal service-to-service traffic
    through an unrelated proxy. Operators who DO need proxy support can
    add a `browser_downloader_proxy` setting in P4.

    Timeout model: 30s connect (matches the spec's "existing 30s connect"
    pattern; httpx's default of 5s is too tight for a slow microservice
    cold start) plus the configurable `browser_downloader_timeout` for
    read/write/pool (default 300s — covers the full Tier 1 + Tier 2
    window in the microservice).
    """
    global _client
    if _client is None:
        _client = httpx.AsyncClient(
            timeout=httpx.Timeout(
                connect=30.0,
                read=settings.browser_downloader_timeout,
                write=settings.browser_downloader_timeout,
                pool=settings.browser_downloader_timeout,
            ),
            trust_env=False,
        )
    return _client


def get_browser_downloader_circuit_breaker() -> CircuitBreaker:
    """Lazy singleton circuit breaker for the browser-downloader service."""
    global _breaker
    if _breaker is None:
        _breaker = CircuitBreaker(
            name="browser_downloader",
            failure_threshold=5,
            success_threshold=3,
            reset_timeout=30.0,
            half_open_max_calls=3,
            use_redis_distributed=settings.browser_downloader_cb_use_redis,
        )
    return _breaker


# -- Public API ------------------------------------------------------------


def select_executor(url: str) -> str:
    """Pick the executor kind for a job URL.

    Returns the literal string `"browser"` for known browser-only platforms
    (TikTok, Instagram, Twitter/X) and `"youtube"` for everything else. The
    feature flag (`browser_downloader_enabled`) is enforced by the caller in
    `worker/job_executor.py` — this function is a pure hostname lookup so it
    can be unit-tested without touching settings.

    Hostname matching uses suffix equality (e.g. `www.tiktok.com`,
    `m.tiktok.com`, `vm.tiktok.com` all match `tiktok.com`). This keeps the
    set small while still catching mobile subdomains that platforms
    frequently serve on.

    Unknown hosts fall through to `"youtube"` to preserve pre-Phase-2
    behavior (yt-dlp attempts and fails as today).
    """
    try:
        hostname = (urlparse(url).hostname or "").lower()
    except (ValueError, TypeError):
        return "youtube"
    # Strip a leading "www." so www.tiktok.com → tiktok.com.
    if hostname.startswith("www."):
        hostname = hostname[4:]
    # FQDN form (trailing dot) — "tiktok.com." → "tiktok.com" before matching.
    if hostname.endswith("."):
        hostname = hostname[:-1]
    browser_suffixes = (
        "tiktok.com",
        "tiktokv.com",
        "instagram.com",
        "instagr.am",
        "twitter.com",
        "x.com",
        "t.co",
    )
    for suffix in browser_suffixes:
        if hostname == suffix or hostname.endswith("." + suffix):
            return "browser"
    return "youtube"


async def extract_media(
    url: str,
    storage_path: str,
    *,
    progress_callback: ProgressCallback | None = None,
    client: httpx.AsyncClient | None = None,
) -> tuple[str, str, str | None]:
    """Call the browser-downloader microservice and return `(file_path, file_name, title)`.

    `title` is always `None` for browser-platform downloads in Phase 2 (the
    microservice does not extract titles). The DB column is nullable so this
    is safe to insert directly.

    Raises:
        BrowserExecutorError: every failure mode is wrapped in this with a
            classified `ErrorCategory`. No raw `httpx` exception leaks.
    """
    _ = progress_callback  # Microservice is single-shot; no progress stream.
    http_client = client or _get_client()
    breaker = get_browser_downloader_circuit_breaker()

    request_body = {"url": url, "output_dir": storage_path}
    endpoint = settings.browser_downloader_endpoint.rstrip("/") + "/download"

    try:
        return await breaker.execute(_call_service, http_client, endpoint, request_body)
    except CircuitBreakerOpenError as exc:
        logger.warning(
            "browser_downloader_circuit_open",
            service=exc.service_name,
            reset_timeout=exc.reset_timeout,
        )
        raise BrowserExecutorError(
            category=ErrorCategory.TRANSIENT, signal="circuit_open"
        ) from exc
    except BrowserExecutorError:
        raise
    except Exception as exc:  # noqa: BLE001 — last-resort classification
        logger.error("browser_downloader_unexpected_error", error=str(exc), exc_info=True)
        raise BrowserExecutorError(
            category=ErrorCategory.UNKNOWN, signal="unexpected_error"
        ) from exc


# -- Internal helpers ------------------------------------------------------


async def _call_service(
    http_client: httpx.AsyncClient, endpoint: str, body: dict[str, Any]
) -> tuple[str, str, str | None]:
    """Single HTTP attempt. Returns the success tuple or raises BrowserExecutorError.

    Split out from `extract_media` so the circuit breaker can wrap exactly
    one HTTP round-trip per recorded success/failure.
    """
    try:
        response = await http_client.post(endpoint, json=body)
    except httpx.TimeoutException as exc:
        logger.warning("browser_downloader_timeout", error=str(exc))
        raise BrowserExecutorError(
            category=ErrorCategory.TIMEOUT, signal="request_timeout"
        ) from exc
    except httpx.ConnectError as exc:
        logger.warning("browser_downloader_connect_error", error=str(exc))
        raise BrowserExecutorError(
            category=ErrorCategory.TRANSIENT, signal="connect_error"
        ) from exc
    except httpx.HTTPError as exc:
        logger.warning("browser_downloader_http_error", error=str(exc))
        raise BrowserExecutorError(
            category=ErrorCategory.TRANSIENT, signal="http_error"
        ) from exc

    if response.status_code == 200:
        return _parse_success(response)
    return _parse_failure_response(response)


def _parse_success(response: httpx.Response) -> tuple[str, str, str | None]:
    """Parse a 200 OK response into the worker's tuple shape."""
    try:
        payload = response.json()
    except (json.JSONDecodeError, ValueError) as exc:
        logger.warning("browser_downloader_non_json_success", status=response.status_code)
        raise BrowserExecutorError(
            category=ErrorCategory.TRANSIENT, signal="non_json_response"
        ) from exc

    if not isinstance(payload, dict):
        logger.warning(
            "browser_downloader_invalid_response_shape",
            payload_type=type(payload).__name__,
        )
        raise BrowserExecutorError(
            category=ErrorCategory.TRANSIENT, signal="invalid_response_shape"
        )

    if payload.get("status") != "success":
        # 200 OK with a failed status is a microservice-side failure
        # (e.g. a graceful degraded path). Reuse the failure parser.
        return _parse_failure_payload(payload)

    file_path = payload.get("file_path")
    if not isinstance(file_path, str) or not file_path:
        logger.warning(
            "browser_downloader_missing_file_path", payload_keys=list(payload.keys()),
        )
        raise BrowserExecutorError(
            category=ErrorCategory.TRANSIENT, signal="missing_file_path"
        )
    file_name = file_path.rsplit("/", 1)[-1] or file_path
    return file_path, file_name, None


def _parse_failure_response(response: httpx.Response) -> tuple[str, str, str | None]:
    """Parse a non-200 response, mapping HTTP status + JSON error code to a category."""
    signal = f"http_{response.status_code}"
    try:
        payload = response.json()
    except (json.JSONDecodeError, ValueError):
        # Non-JSON error body — treat as transient
        logger.warning(
            "browser_downloader_non_json_error", status=response.status_code,
        )
        raise BrowserExecutorError(
            category=ErrorCategory.TRANSIENT, signal=signal
        )

    if not isinstance(payload, dict):
        logger.warning(
            "browser_downloader_invalid_response_shape",
            payload_type=type(payload).__name__,
        )
        raise BrowserExecutorError(
            category=ErrorCategory.TRANSIENT, signal=signal
        )

    return _parse_failure_payload(payload)


def _parse_failure_payload(payload: dict[str, Any]) -> tuple[str, str, str | None]:
    """Map a structured failure payload to an error category.

    Accepts both 200-with-failed-status and non-200 responses.
    Raises BrowserExecutorError so the circuit breaker records a failure.
    """
    code = payload.get("error")
    if not isinstance(code, str) or not code:
        code = "unknown_error"
    category = _map_response_to_category(code, payload)
    raise BrowserExecutorError(category=category, signal=code)


def _map_response_to_category(code: str, payload: dict[str, Any] | None = None) -> ErrorCategory:
    """Single source of truth for microservice error code → ErrorCategory.

    Mapping is intentionally narrow: anything not explicitly recognized is
    TRANSIENT (retries are safe; the worker can reclassify later if needed).
    """
    # Terminal categories first — these never retry
    if code in {"drm_detected", "anti_bot_block"}:
        return ErrorCategory.BLOCKED
    if code in {"no_media_found", "not_found", "private_content"}:
        return ErrorCategory.NOT_FOUND
    # 429 rate-limit (microservice body) — TRANSIENT so the existing
    # backoff machinery applies; falling through to BLOCKED here would
    # send rate-limited jobs straight to the DLQ.
    if code == "http_429":
        return ErrorCategory.TRANSIENT
    # Retryable categories
    if code in {"network_error", "http_error", "connect_error", "non_json_response"}:
        return ErrorCategory.TRANSIENT
    if code in {"timeout", "request_timeout"}:
        return ErrorCategory.TIMEOUT
    # Generic 4xx (other than the BLOCKED codes above) → BLOCKED,
    # except 429 (rate-limit) which is transient.
    if code.startswith("http_4"):
        return ErrorCategory.BLOCKED
    if code.startswith("http_5"):
        return ErrorCategory.TRANSIENT
    if code == "circuit_open":
        return ErrorCategory.TRANSIENT
    if code == "invalid_request":
        return ErrorCategory.BLOCKED
    return ErrorCategory.TRANSIENT

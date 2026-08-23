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
- Progress: the microservice streams NDJSON progress events
  (`Accept: application/x-ndjson`); `extract_media` forwards them to the
  `progress_callback` (same shape as yt-dlp progress) so browser-platform
  jobs get the same pub/sub progress as YouTube jobs.
"""

from __future__ import annotations

import json
import os
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any, ClassVar, NoReturn, cast
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

    _CATEGORY_MARKERS: ClassVar[dict[ErrorCategory, str]] = {
        ErrorCategory.BLOCKED: "blocked (anti-bot or DRM)",
        ErrorCategory.NOT_FOUND: "404 not found",
        ErrorCategory.TIMEOUT: "Request timeout",
        ErrorCategory.TRANSIENT: "network error (transient)",
        ErrorCategory.UNKNOWN: "unknown error",
    }

    def __init__(self, category: ErrorCategory, signal: str) -> None:
        """
        Initialize an executor error with its category and original error signal.

        Parameters:
            category (ErrorCategory): Classification assigned to the error.
            signal (str): Original error signal associated with the failure.
        """
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

# Cap on the microservice error body we are willing to buffer/parse. The
# service's own error payloads are a few hundred bytes; anything larger is
# treated as a non-JSON body (fall back to the HTTP-status signal).
_MAX_ERROR_BODY_BYTES = 1 << 20  # 1 MiB


# -- Singleton client & breaker -------------------------------------------

_client: httpx.AsyncClient | None = None
_breaker: CircuitBreaker | None = None


def _get_client() -> httpx.AsyncClient:
    """
    Create or retrieve the shared HTTP client for browser-downloader requests.

    Returns:
        httpx.AsyncClient: The lazily initialized HTTP client.
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


async def close_browser_client() -> None:
    """Close the shared HTTP client (worker shutdown hook)."""
    global _client
    if _client is not None:
        await _client.aclose()
        _client = None


# -- Public API ------------------------------------------------------------


def select_executor(url: str) -> str:
    """
    Determine which executor should handle a job URL.

    Parameters:
        url (str): The job URL to classify.

    Returns:
        str: `"browser"` for supported TikTok, Instagram, Twitter/X, and related domains; `"youtube"` for all other or malformed URLs.
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
    )
    # NOTE: `t.co` is intentionally excluded. It is a generic URL shortener that
    # can redirect to ANY host (including YouTube). Routing every `t.co` URL to
    # the browser executor misroutes YouTube links shared via t.co (the
    # microservice cannot build blob: media for YouTube) and fails. Until
    # redirect resolution is implemented, t.co links fall through to yt-dlp.
    # TODO(kilo): resolve t.co redirects before dispatch and route accordingly.
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
    """
    Download media through the browser-downloader microservice.

    Parameters:
        url: The media URL to download.
        storage_path: Directory where the downloaded media is stored.

    Returns:
        A tuple containing the file path, file name, and a nullable title.

    Raises:
        BrowserExecutorError: If the request or response processing fails.
        CircuitBreakerOpenError: If the browser-downloader circuit is open.
    """
    http_client = client or _get_client()
    breaker = get_browser_downloader_circuit_breaker()

    # The microservice writes into the `downloads` subfolder of the worker's
    # storage root. This MUST match the serve-side base
    # (`app.services.download_service._downloads_base_path` →
    # `storage_path/downloads`); otherwise `DownloadService.get_file_path`
    # rejects the stored path and every browser download 403s.
    downloads_dir = os.path.join(storage_path, "downloads")

    request_body = {"url": url, "output_dir": downloads_dir}
    endpoint = settings.browser_downloader_endpoint.rstrip("/") + "/download"

    try:
        result = await breaker.execute(
            _call_service_terminal_safe,
            http_client,
            endpoint,
            request_body,
            downloads_dir,
            progress_callback,
        )
        if isinstance(result, BrowserExecutorError):
            # Terminal verdict — re-raise after breaker execution (returned as
            # a value so it was never counted as a downstream failure).
            raise result
        return cast(tuple[str, str, str | None], result)
    except CircuitBreakerOpenError:
        # Let CircuitBreakerOpenError propagate so the processor's dedicated
        # deferred-job path can handle it (worker/processor.py:_handle_circuit_open).
        raise
    except BrowserExecutorError:
        raise
    except Exception as exc:
        logger.error(
            "browser_downloader_unexpected_error",
            error=str(exc),
            error_type=type(exc).__name__,
            exc_info=True,
        )
        raise BrowserExecutorError(
            category=ErrorCategory.UNKNOWN, signal="unexpected_error"
        ) from exc


# -- Internal helpers ------------------------------------------------------


# Terminal verdicts describe THIS request (blocked content, missing media),
# not downstream health — they must not count as circuit-breaker failures.
_TERMINAL_CATEGORIES = frozenset({ErrorCategory.BLOCKED, ErrorCategory.NOT_FOUND})


async def _call_service_terminal_safe(
    *args: Any, **kwargs: Any
) -> tuple[str, str, str | None] | BrowserExecutorError:
    """Run ``_call_service``, returning terminal verdicts as values.

    The breaker records every exception raised through ``execute`` as a
    downstream failure. BLOCKED / NOT_FOUND verdicts are request-specific
    (a geo-blocked or missing video) and would eventually open the circuit
    for ALL downloads, so they are returned as values here and re-raised by
    the caller after breaker execution instead.
    """
    try:
        return await _call_service(*args, **kwargs)
    except BrowserExecutorError as exc:
        if exc.category in _TERMINAL_CATEGORIES:
            return exc
        raise


async def _call_service(
    http_client: httpx.AsyncClient,
    endpoint: str,
    body: dict[str, Any],
    downloads_dir: str,
    progress_callback: ProgressCallback | None = None,
) -> tuple[str, str, str | None]:
    """
    Send one download request and parse the service response.

    The request uses `Accept: application/x-ndjson`: the microservice streams
    newline-delimited progress events before the final result line. Progress
    events (any line without a `status` field) are forwarded to
    ``progress_callback`` in the same shape yt-dlp progress uses
    (percent/downloaded_bytes/total_bytes); the last line is the result.

    Parameters:
        http_client (httpx.AsyncClient): HTTP client used for the request.
        endpoint (str): Download service endpoint.
        body (dict[str, Any]): JSON request payload.
        downloads_dir (str): The `storage_path/downloads` directory the
            microservice writes into; used as the path-traversal base.
        progress_callback (ProgressCallback | None): Optional async callback
            receiving progress dicts.

    Returns:
        tuple[str, str, str | None]: The media file path, file name, and optional title.

    Raises:
        BrowserExecutorError: If the request fails or the service returns an unsuccessful or invalid response.
    """
    try:
        async with http_client.stream(
            "POST",
            endpoint,
            json=body,
            headers={"accept": "application/x-ndjson"},
        ) as response:
            if response.status_code != 200:
                return await _parse_failure_response(response)
            final_payload: dict[str, Any] | None = None
            async for line in response.aiter_lines():
                if not line.strip():
                    continue
                try:
                    payload = json.loads(line)
                except (json.JSONDecodeError, ValueError):
                    raise BrowserExecutorError(
                        category=ErrorCategory.TRANSIENT, signal="non_json_response"
                    ) from None
                if not isinstance(payload, dict):
                    raise BrowserExecutorError(
                        category=ErrorCategory.TRANSIENT, signal="invalid_response_shape"
                    )
                if payload.get("status") in ("success", "failed"):
                    final_payload = payload
                elif progress_callback is not None:
                    await _emit_progress(progress_callback, payload)
            if final_payload is None:
                raise BrowserExecutorError(
                    category=ErrorCategory.TRANSIENT, signal="non_json_response"
                )
            if final_payload.get("status") == "success":
                return _parse_success_payload(final_payload, downloads_dir)
            # Streamed failure (HTTP 200 + failed final line): map its
            # structured error code like any other failure payload.
            raw_error = final_payload.get("error")
            fallback_code = (
                raw_error if isinstance(raw_error, str) and raw_error else "unknown_error"
            )
            _parse_failure_payload(final_payload, fallback_code=fallback_code)
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
        raise BrowserExecutorError(category=ErrorCategory.TRANSIENT, signal="http_error") from exc


def _parse_success_payload(
    payload: dict[str, Any], downloads_dir: str
) -> tuple[str, str, str | None]:
    """
    Parse a successful downloader payload into the worker's media tuple.

    Returns:
        file_data (tuple[str, str, str | None]): The file path, derived file name, and null title.
    """
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
        _parse_failure_payload(payload)

    file_path = payload.get("file_path")
    if not isinstance(file_path, str) or not file_path:
        logger.warning(
            "browser_downloader_missing_file_path",
            payload_keys=list(payload.keys()),
        )
        raise BrowserExecutorError(category=ErrorCategory.TRANSIENT, signal="missing_file_path")

    # The microservice returns only a JSON `file_path`; it does NOT stream the
    # bytes back over HTTP. The worker stores this path and later serves/deletes
    # it from its OWN filesystem, so the worker and browser-downloader pods must
    # mount a *shared* volume at the identical `storage_path`. The compose
    # deployment wires this (both services mount the `storage` volume;
    # browser-downloader writes under BD_OUTPUT_BASE=/app/storage). Non-compose
    # deployments without the shared volume still hit the gap below:
    # `download_file` would fail with a missing file and
    # `cleanup_downloaded_file` would be a silent no-op.
    # TODO(kilo): add a clear runtime error + fetch-via-service path when the
    # file is absent on this pod (cross-pod delivery gap, non-compose deploys).
    file_path = _validate_file_path(file_path, downloads_dir)

    file_name = file_path.rsplit("/", 1)[-1] or file_path
    return file_path, file_name, None


async def _emit_progress(progress_callback: ProgressCallback, payload: dict[str, Any]) -> None:
    """Forward one NDJSON progress event to the caller's callback.

    The microservice emits `{phase, percent?, downloaded_bytes?, total_bytes?}`;
    the worker maps it to the same dict shape the yt-dlp path uses so
    ``worker.job_executor`` can publish it to pub/sub unchanged. Best-effort:
    a failing callback must never fail the download.
    """
    try:
        await progress_callback(
            {
                "percent": payload.get("percent"),
                "speed": None,
                "eta": None,
                "downloaded_bytes": payload.get("downloaded_bytes"),
                "total_bytes": payload.get("total_bytes"),
            }
        )
    except Exception:
        logger.warning("browser_downloader_progress_publish_failed", exc_info=True)


def _validate_file_path(file_path: str, downloads_dir: str) -> str:
    """
    Validate that the microservice-returned path stays within the download root.

    The external microservice is trusted with the worker's downloads directory
    but a compromised or buggy service could return an absolute path outside it,
    which would then be served via ``FileResponse`` and passed to ``os.remove``.
    Mirror ``app.services.download_service._validate_download_path`` (whose base
    is ``_downloads_base_path`` → ``storage_path/downloads``): the resolved path
    must live strictly beneath ``downloads_dir`` — the root directory itself is
    never a valid file path.
    """
    root = Path(downloads_dir).resolve()
    resolved = Path(file_path).resolve()
    if root not in resolved.parents:
        logger.warning(
            "browser_downloader_path_traversal",
            file_path=file_path,
            downloads_dir=downloads_dir,
        )
        raise BrowserExecutorError(category=ErrorCategory.BLOCKED, signal="invalid_file_path")
    return str(resolved)


async def _parse_failure_response(response: httpx.Response) -> tuple[str, str, str | None]:
    """
    Classify a non-successful downloader response using its structured error code or HTTP status.

    Parameters:
        response (httpx.Response): The HTTP response containing the failure details.

    Raises:
        BrowserExecutorError: Always, with the category and signal derived from the response.
    """
    signal = f"http_{response.status_code}"
    try:
        # The body arrives as an unconsumed stream (client.stream); read it
        # first — `response.json()` on a streaming response raises
        # `httpx.ResponseNotRead` (a RuntimeError, not an HTTPError), which
        # escaped every handler here and degraded all failures to UNKNOWN.
        # `aread()` is idempotent for already-consumed responses.
        await response.aread()
        if len(response.content) > _MAX_ERROR_BODY_BYTES:
            logger.warning(
                "browser_downloader_error_body_too_large",
                status=response.status_code,
                size=len(response.content),
            )
            raise BrowserExecutorError(
                category=_map_response_to_category(signal),
                signal=signal,
            )
        payload = response.json()
    except (json.JSONDecodeError, ValueError) as err:
        # Non-JSON error body — use the synthesized HTTP status signal so 404/403/429
        # are categorized correctly even when the body is empty or non-JSON.
        logger.warning(
            "browser_downloader_non_json_error",
            status=response.status_code,
        )
        raise BrowserExecutorError(
            category=_map_response_to_category(signal),
            signal=signal,
        ) from err

    if not isinstance(payload, dict):
        logger.warning(
            "browser_downloader_invalid_response_shape",
            payload_type=type(payload).__name__,
        )
        raise BrowserExecutorError(
            category=_map_response_to_category(signal),
            signal=signal,
        )

    return _parse_failure_payload(payload, fallback_code=signal)


def _parse_failure_payload(
    payload: dict[str, Any],
    *,
    fallback_code: str = "unknown_error",
) -> NoReturn:
    """
    Map a structured service failure to a classified browser executor error.

    Accepts both 200-with-failed-status and non-200 responses. Always raises
    ``BrowserExecutorError`` so the circuit breaker records a failure.

    When the JSON body lacks an explicit ``error`` field, ``fallback_code``
    (typically the synthesized ``http_<status>`` signal) is used so HTTP
    404/403/429 still map to their correct categories.

    Parameters:
        payload (dict[str, Any]): Structured failure data from the service.
        fallback_code (str): Error signal used when the payload does not provide one.

    Raises:
        BrowserExecutorError: Always, with the category and signal derived.
    """
    raw_code = payload.get("error")
    code = raw_code if isinstance(raw_code, str) and raw_code else fallback_code
    category = _map_response_to_category(code, payload)
    raise BrowserExecutorError(category=category, signal=code)


def _map_response_to_category(code: str, payload: dict[str, Any] | None = None) -> ErrorCategory:
    """
    Map a microservice error code to its worker error category.

    Parameters:
        code (str): Microservice error code to classify.
        payload (dict[str, Any] | None): Optional response payload associated with the error.

    Returns:
        ErrorCategory: Category assigned to the error code.
    """
    # Terminal categories first — these never retry
    if code in {"drm_detected", "anti_bot_block"}:
        return ErrorCategory.BLOCKED
    if code in {"no_media_found", "not_found", "private_content", "http_404"}:
        return ErrorCategory.NOT_FOUND
    # Storage failure (disk full) — retried once after a long delay by the
    # STORAGE policy in app.services.error_classifier.
    if code == "storage_error":
        return ErrorCategory.STORAGE
    # 429 rate-limit (microservice body) — TRANSIENT so the existing
    # backoff machinery applies; falling through to BLOCKED here would
    # send rate-limited jobs straight to the DLQ.
    if code in {"http_429", "http_503"}:
        return ErrorCategory.TRANSIENT
    if code in {"network_error", "http_error", "connect_error", "non_json_response"}:
        return ErrorCategory.TRANSIENT
    if code in {"timeout", "request_timeout"}:
        return ErrorCategory.TIMEOUT
    # Generic 4xx (other than the BLOCKED/NOT_FOUND codes above) → BLOCKED
    if code.startswith("http_4"):
        return ErrorCategory.BLOCKED
    if code.startswith("http_5"):
        return ErrorCategory.TRANSIENT
    if code == "circuit_open":
        return ErrorCategory.TRANSIENT
    if code == "invalid_request":
        return ErrorCategory.BLOCKED
    return ErrorCategory.TRANSIENT

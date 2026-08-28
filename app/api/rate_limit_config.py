"""Rate limiting configuration using slowapi with Redis-backed storage."""

import os
import re
from collections.abc import Callable
from typing import Any
from urllib.parse import quote_plus

from slowapi import Limiter
from slowapi.errors import RateLimitExceeded
from starlette.requests import Request
from starlette.responses import HTMLResponse, JSONResponse

from app.api.routes.web_helpers import _rate_limit_error_html
from app.schemas.error import ErrorCode, error_response_dict

# Disable rate limiting in test mode
is_testing = os.environ.get("TESTING", "").lower() in ("1", "true", "yes", "on")


# Redis storage URL for distributed rate limiting across replicas.
# Falls back to local Redis when not explicitly configured.
def _resolve_redis_storage_url() -> str:
    """Resolve the Redis URL used for shared rate-limit counters.

    Precedence: ``RATE_LIMIT_REDIS_URL`` > ``REDIS_URL`` > components
    (``REDIS_HOST``/``REDIS_PORT``/``REDIS_PASSWORD``) > local Redis.
    Empty values are treated as unset, so a blank ``REDIS_URL`` (e.g. from a
    compose file interpolating ``${REDIS_URL:-}``) cannot produce a useless
    ``storage_uri=""`` that silently degrades the limiter.

    When no explicit URL is given, the components are assembled exactly like
    ``core.config.Settings._build_redis_url``: the password is
    ``quote_plus``-encoded so values containing ``@``/``:``/``/`` never yield
    a malformed URL. Docker Compose cannot URL-encode interpolated values, so
    deriving the URL here keeps rate-limit counters on the same Redis as the
    queue without requiring operators to pre-encode ``REDIS_PASSWORD``.
    """
    explicit = os.environ.get("RATE_LIMIT_REDIS_URL") or os.environ.get("REDIS_URL")
    if explicit:
        return explicit

    if os.environ.get("REDIS_HOST") or os.environ.get("REDIS_PORT"):
        host = os.environ.get("REDIS_HOST", "redis")
        port = os.environ.get("REDIS_PORT", "6379")
        password = os.environ.get("REDIS_PASSWORD")
        if password:
            return f"redis://:{quote_plus(password)}@{host}:{port}/1"
        return f"redis://{host}:{port}/1"

    return "redis://localhost:6379"


REDIS_STORAGE_URL = _resolve_redis_storage_url()


class NoOpLimiter:
    """A no-op limiter that doesn't enforce rate limits."""

    def limit(
        self,
        *args: Any,
        **kwargs: Any,
    ) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
        """
        Provide a decorator that leaves the decorated function unchanged.

        Returns:
            Callable[..., Any]: A decorator that returns the original function.
        """

        def noop_decorator(func: Callable[..., Any]) -> Callable[..., Any]:
            """
            Return the original function unchanged.

            Parameters:
                func (Callable[..., Any]): The function to leave undecorated.

            Returns:
                Callable[..., Any]: The original function.
            """
            return func

        return noop_decorator

    async def __call__(self, request: Request, *args: Any, **kwargs: Any) -> None:
        """Allow all requests."""


def _client_ip(request: Request) -> str:
    """Rate-limit bucket key.

    Behind the deploy proxy every client shares the proxy IP via
    ``request.client.host``, which would make every bucket effectively global
    (one client's 429s lock out the whole site). Traefik and the deploy
    proxies *append* the direct peer IP as the rightmost ``X-Forwarded-For``
    entry, so the rightmost value is the one added by the trusted proxy and
    cannot be forged by a client sending a spoofed leftmost entry. Reading the
    rightmost entry keeps the app-level buckets aligned with the gateway's
    rate limiter (``ipStrategy.depth: 1``). Falls back to the socket address
    when the header is absent.

    Trust note: this assumes the API is only reachable through a proxy that
    appends/overwrites XFF (the compose deployment exposes the API only
    through one). If the API is ever exposed directly, clients could rotate
    buckets by spoofing the header.
    """
    forwarded: str | None = request.headers.get("x-forwarded-for")
    if forwarded:
        entries = [entry.strip() for entry in forwarded.split(",") if entry.strip()]
        if entries:
            return entries[-1]
    return request.client.host if request.client else "unknown"


def _build_limiter() -> Limiter | NoOpLimiter:
    """Build a rate limiter backed by Redis storage for distributed state.

    Uses slowapi's ``storage_uri`` option (resolved through ``limits`` to a
    Redis storage backend) so rate-limit counters are shared across all API
    replicas. When Redis is unreachable, ``in_memory_fallback_enabled`` makes
    slowapi fall back to per-process in-memory counters and ``swallow_errors``
    keeps a failing fallback from breaking requests, so the API never 500s
    just because the rate-limit backend is down.
    """
    if is_testing:
        return NoOpLimiter()

    return Limiter(
        key_func=_client_ip,
        storage_uri=REDIS_STORAGE_URL,
        in_memory_fallback_enabled=True,
        swallow_errors=True,
    )


limiter = _build_limiter()


def _parse_retry_after(detail: str) -> int:
    """
    Parse a rate-limit detail string into a retry interval.

    Parameters:
        detail (str): Rate-limit description such as ``"5 per 1 minute"``.

    Returns:
        int: Retry interval in seconds, defaulting to 60 when the description
            cannot be parsed or uses an unsupported unit.
    """
    match = re.match(r"(\d+)\s+per\s+(\d+)\s+(\w+)", detail)
    if not match:
        return 60  # Default to 60 seconds if parsing fails
    _limit, window, unit = match.groups()
    _limit = int(_limit)
    window = int(window)
    unit = unit.lower()
    unit = unit.removesuffix("s")  # "minutes" → "minute", "secs" → "sec"
    # Handle "sec" variant
    if unit == "sec":
        unit = "second"
    multipliers = {
        "second": 1,
        "minute": 60,
        "hour": 3600,
        "day": 86400,
    }
    multiplier = multipliers.get(unit, 60)  # Default to minute (60s)
    return window * multiplier


async def rate_limit_exceeded_handler(
    request: Request,
    exc: Exception,
) -> JSONResponse | HTMLResponse:
    """
    Handle rate-limit violations with a standardized response.

    HTMX requests receive an HTML error fragment; other requests receive a JSON
    error response. Both responses include the retry interval in the
    ``Retry-After`` header.

    Parameters:
        request (Request): The incoming request.
        exc (Exception): The exception raised by the rate-limit check.

    Returns:
        JSONResponse | HTMLResponse: A 429 response in JSON or HTML format.

    Raises:
        Exception: Re-raises exceptions that are not rate-limit violations.
    """
    if not isinstance(exc, RateLimitExceeded):
        raise exc

    retry_after = _parse_retry_after(str(exc.detail))
    detail = str(exc.detail)
    headers = {"Retry-After": str(retry_after)}

    # HTMX requests get an HTML fragment so the error renders correctly
    # even if the JS error handler swaps the response into the DOM target.
    if request.headers.get("HX-Request") == "true":
        return HTMLResponse(
            status_code=429,
            content=_rate_limit_error_html(detail),
            headers=headers,
        )

    return JSONResponse(
        status_code=429,
        content=error_response_dict(ErrorCode.RATE_LIMIT_EXCEEDED, detail),
        headers=headers,
    )

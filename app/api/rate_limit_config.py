"""Rate limiting configuration using slowapi with Redis-backed storage."""

import os
import re
from collections.abc import Callable
from typing import Any

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
REDIS_STORAGE_URL = os.environ.get(
    "RATE_LIMIT_REDIS_URL",
    os.environ.get("REDIS_URL", "redis://localhost:6379"),
)


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
    (one client's 429s lock out the whole site). Prefer the leftmost
    ``X-Forwarded-For`` entry, which the proxy prepends with the real client
    IP. Falls back to the socket address when the header is absent.

    Trust note: this assumes a proxy that overwrites/validates XFF (the
    compose deployment only exposes the API through one). If the API is ever
    exposed directly, clients could rotate buckets by spoofing the header.
    """
    forwarded: str | None = request.headers.get("x-forwarded-for")
    if forwarded:
        first = forwarded.split(",", 1)[0].strip()
        if first:
            return first
    return request.client.host if request.client else "unknown"


def _build_limiter() -> Limiter | NoOpLimiter:
    """Build a rate limiter backed by Redis storage for distributed state.

    Uses slowapi's RedisStorage so rate limit counters are shared across
    all API replicas. This ensures consistent enforcement when running
    multiple containers behind a load balancer.
    """
    if is_testing:
        return NoOpLimiter()

    try:
        from slowapi.storage import RedisStorage

        storage = RedisStorage(REDIS_STORAGE_URL)
        return Limiter(key_func=_client_ip, storage=storage)
    except ImportError:
        return Limiter(key_func=_client_ip)


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

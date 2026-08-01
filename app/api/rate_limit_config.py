"""Rate limiting configuration using slowapi."""

import os
import re
from collections.abc import Callable
from typing import Any

from slowapi import Limiter
from slowapi.errors import RateLimitExceeded
from slowapi.util import get_remote_address
from starlette.requests import Request
from starlette.responses import HTMLResponse, JSONResponse

from app.api.routes.web_helpers import _rate_limit_error_html
from app.schemas.error import ErrorCode, error_response_dict

# Disable rate limiting in test mode
is_testing = os.environ.get("TESTING", "").lower() in ("1", "true", "yes", "on")


class NoOpLimiter:
    """A no-op limiter that doesn't enforce rate limits."""

    def limit(
        self, *args: Any, **kwargs: Any,
    ) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
        """Return a no-op decorator."""

        def noop_decorator(func: Callable[..., Any]) -> Callable[..., Any]:
            return func

        return noop_decorator

    async def __call__(self, request: Request, *args: Any, **kwargs: Any) -> None:
        """Allow all requests."""


# Use NoOpLimiter in test mode, real limiter otherwise
limiter = NoOpLimiter() if is_testing else Limiter(key_func=get_remote_address)


def _parse_retry_after(detail: str) -> int:
    """Parse slowapi detail string to get retry-after seconds.

    Detail format: "X per Y <unit>" e.g., "5 per 1 minute"
    Returns integer seconds until retry is allowed.
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
    request: Request, exc: Exception,
) -> JSONResponse | HTMLResponse:
    """Handle rate limit exceeded errors with standardized error response.

    Returns JSON for API requests (REST clients) and HTML for HTMX requests
    (web UI forms). HTMX form submissions that hit the rate limit should not
    receive JSON, because the JS error handler may inadvertently swap it into
    the DOM target (see renderErrorInTarget in htmx-error-handler.js).
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
            status_code=429, content=_rate_limit_error_html(detail), headers=headers,
        )

    return JSONResponse(
        status_code=429,
        content=error_response_dict(ErrorCode.RATE_LIMIT_EXCEEDED, detail),
        headers=headers,
    )

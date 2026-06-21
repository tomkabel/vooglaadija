"""Rate limiting configuration using slowapi."""

import html
import os
import re

from slowapi import Limiter
from slowapi.errors import RateLimitExceeded
from slowapi.util import get_remote_address
from starlette.requests import Request
from starlette.responses import HTMLResponse, JSONResponse

from app.schemas.error import ErrorCode, error_response_dict

# Disable rate limiting in test mode
is_testing = os.environ.get("TESTING", "").lower() in ("1", "true", "yes", "on")


class NoOpLimiter:
    """A no-op limiter that doesn't enforce rate limits."""

    def limit(self, *args, **kwargs):
        """Return a no-op decorator."""

        def noop_decorator(func):
            return func

        return noop_decorator

    async def __call__(self, request, *args, **kwargs):
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
    limit, _window, unit = match.groups()
    limit = int(limit)
    unit = unit.lower()
    if unit.endswith("s"):
        unit = unit[:-1]  # "minutes" → "minute", "secs" → "sec"
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
    return limit * multiplier


async def rate_limit_exceeded_handler(
    request: Request, exc: Exception
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
        error_html = f"""<div class="error-box" role="alert" aria-live="assertive">
  <svg class="h-5 w-5 flex-shrink-0 mt-0.5" xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="2">
    <path stroke-linecap="round" stroke-linejoin="round" d="M12 9v3.75m9-.75a9 9 0 11-18 0 9 9 0 0118 0zm-9 3.75h.008v.008H12v-.008z" />
  </svg>
  <div>
    <strong>Rate limit exceeded</strong>
    <p class="text-sm mt-1 opacity-80">{html.escape(detail)}. Please wait before submitting another link.</p>
  </div>
</div>"""
        return HTMLResponse(status_code=429, content=error_html, headers=headers)

    return JSONResponse(
        status_code=429,
        content=error_response_dict(ErrorCode.RATE_LIMIT_EXCEEDED, detail),
        headers=headers,
    )

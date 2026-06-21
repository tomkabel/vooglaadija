"""Security header and CSP nonce middleware."""

import uuid
from typing import Any

from fastapi import Request


async def add_security_headers(request: Request, call_next: Any) -> Any:
    """Add Content-Security-Policy and other security headers to all responses."""
    nonce = uuid.uuid4().hex
    request.state.nonce = nonce

    response = await call_next(request)

    if "Content-Security-Policy" not in response.headers:
        response.headers["Content-Security-Policy"] = (
            f"default-src 'self'; "
            f"script-src 'self' 'nonce-{nonce}'; "
            f"style-src 'self' https://fonts.googleapis.com 'unsafe-inline'; "
            f"font-src 'self' https://fonts.gstatic.com; "
            f"img-src 'self' data: blob:; "
            f"connect-src 'self'; "
            f"frame-ancestors 'none'; "
            f"base-uri 'self'; "
            f"form-action 'self'"
        )
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
    response.headers["Permissions-Policy"] = "camera=(), microphone=(), geolocation=()"

    return response

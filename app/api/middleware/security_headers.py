"""Security header and CSP nonce middleware."""

import uuid
from typing import Any

from fastapi import Request

FONT_ONLOAD_HANDLER_HASH = "'sha256-MhtPZXr7+LpJUY5qtMutB+qWfQtMaPccfe7QXtCcEYc='"


async def add_security_headers(request: Request, call_next: Any) -> Any:
    """
    Add security headers, including a nonce-based Content Security Policy, to the response.
    
    Parameters:
    	request (Request): The incoming request whose state receives the generated CSP nonce.
    	call_next (Any): The next middleware or request handler to invoke.
    
    Returns:
    	Any: The response with security headers applied.
    """
    nonce = uuid.uuid4().hex
    request.state.nonce = nonce

    response = await call_next(request)

    if "Content-Security-Policy" not in response.headers:
        response.headers["Content-Security-Policy"] = (
            f"default-src 'self'; "
            f"script-src 'self' 'nonce-{nonce}' 'unsafe-hashes' "
            f"{FONT_ONLOAD_HANDLER_HASH}; "
            f"style-src 'self' 'nonce-{nonce}' https://fonts.googleapis.com; "
            f"font-src 'self' https://fonts.gstatic.com; "
            f"img-src 'self' data: blob:; "
            f"connect-src 'self'; "
            f"frame-ancestors 'none'; "
            f"base-uri 'self'; "
            f"form-action 'self'; "
            f"object-src 'none'"
        )
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
    response.headers["Permissions-Policy"] = "camera=(), microphone=(), geolocation=()"

    return response

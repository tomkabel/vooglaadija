"""Request correlation ID middleware."""

import uuid
from typing import Any

import structlog.contextvars
from fastapi import Request


async def add_request_id(request: Request, call_next: Any) -> Any:
    """Add a unique request ID to each request and bind it to structured logs."""
    request_id = str(uuid.uuid4())
    request.state.request_id = request_id
    structlog.contextvars.bind_contextvars(request_id=request_id)

    try:
        response = await call_next(request)
        response.headers["X-Request-ID"] = request_id
        return response
    finally:
        structlog.contextvars.unbind_contextvars("request_id")

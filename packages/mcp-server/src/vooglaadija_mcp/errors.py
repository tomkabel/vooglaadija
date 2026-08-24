"""Deterministic, self-healing error envelopes for agent/MCP consumption.

Agents calling Vooglaadija should never have to guess *why* a call failed or
whether retrying is worthwhile. Every error is normalized into a stable
``error_code``, a boolean ``retryable`` flag, and an actionable ``suggestion``.
This mirrors the contract described in RFC #154 (Native Agentic Support).
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from typing import Any


@dataclass
class ApiErrorEnvelope:
    """Structured, machine-readable error payload."""

    error_code: str
    retryable: bool
    suggestion: str | None = None
    http_status: int | None = None
    details: dict[str, Any] | None = None

    def to_dict(self) -> dict[str, Any]:
        return {k: v for k, v in asdict(self).items() if v is not None}


# Status-code -> envelope mapping. The suggestion text is written for an
# autonomous agent: it states the next concrete action to take.
_STATUS_MAP: dict[int, tuple[str, bool, str]] = {
    400: ("INVALID_REQUEST", False, "Check the request parameters and try again."),
    401: (
        "AUTHENTICATION_FAILED",
        False,
        "Provide a valid VOOGLAADIJA_API_KEY personal access token via the Authorization header.",
    ),
    403: (
        "INSUFFICIENT_SCOPE",
        False,
        "The API key lacks the required scope. Mint a key with a broader scope (e.g. downloads:write).",
    ),
    404: (
        "RESOURCE_NOT_FOUND",
        False,
        "Verify the resource id is correct and that it belongs to the authenticated user.",
    ),
    409: ("RESOURCE_CONFLICT", False, "The resource already exists or is in a conflicting state."),
    422: (
        "VALIDATION_ERROR",
        False,
        "Fix the input (for example, supply a supported media URL) and retry.",
    ),
    429: (
        "RATE_LIMITED",
        True,
        "Back off and retry after the Retry-After interval reported by the server.",
    ),
    500: ("UPSTREAM_ERROR", True, "The server encountered an internal error. Retry the request."),
    502: ("BAD_GATEWAY", True, "The upstream service is unreachable. Retry shortly."),
    503: ("SERVICE_UNAVAILABLE", True, "The service is temporarily unavailable. Retry with backoff."),
    504: ("GATEWAY_TIMEOUT", True, "The upstream timed out. Retry the request."),
}


def map_http_error(status: int, payload: Any = None) -> ApiErrorEnvelope:
    """Map an HTTP error status into a deterministic envelope."""
    code, retryable, suggestion = _STATUS_MAP.get(
        status,
        ("UNKNOWN_ERROR", False, "An unexpected error occurred. Inspect details and contact support."),
    )

    details: dict[str, Any] | None = None
    if isinstance(payload, dict):
        error_block = payload.get("error")
        if isinstance(error_block, dict):
            upstream_code = error_block.get("code")
            if upstream_code:
                code = str(upstream_code)
            message = error_block.get("message")
            if message:
                suggestion = f"{suggestion} ({message})"
        if payload.get("details"):
            details = payload["details"]

    return ApiErrorEnvelope(
        error_code=code,
        retryable=retryable,
        suggestion=suggestion,
        http_status=status,
        details=details,
    )


def map_network_error(reason: str) -> ApiErrorEnvelope:
    """Map a transport-level failure (DNS, TLS, connection reset) to an envelope."""
    return ApiErrorEnvelope(
        error_code="NETWORK_ERROR",
        retryable=True,
        suggestion="Check connectivity to VOOGLAADIJA_API_BASE_URL and retry with backoff.",
        details={"reason": reason},
    )


def error_text(envelope: ApiErrorEnvelope) -> str:
    """Serialize an envelope as the text payload of an MCP error result."""
    return json.dumps(envelope.to_dict(), separators=(",", ":"))


class VooglaadijaApiError(Exception):
    """Raised when the API returns a non-success response or a network failure."""

    def __init__(self, envelope: ApiErrorEnvelope) -> None:
        self.envelope = envelope
        super().__init__(envelope.error_code)

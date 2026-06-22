"""Small HTML fragment helpers for web routes and templates."""

import html
import json
import re

_STATUS_LABELS = {
    "pending": "Pending",
    "processing": "Processing",
    "completed": "Completed",
    "failed": "Failed",
    "deferred": "Deferred",
    "cancelled": "Cancelled",
    "unknown": "Unknown",
}
_STATUS_CLASS_PATTERN = re.compile(r"[^a-z0-9]+")


def _status_class_suffix(status: str | None) -> str:
    """Normalize a job status into a CSS-safe status class suffix."""
    raw_status = str(status or "unknown").strip().lower()
    suffix = _STATUS_CLASS_PATTERN.sub("-", raw_status).strip("-")
    return suffix or "unknown"


def _status_badge_html(status: str | None) -> str:
    """Render a safe status badge for a download job status."""
    raw_status = str(status or "unknown").strip() or "unknown"
    normalized_status = raw_status.lower()
    label = _STATUS_LABELS.get(normalized_status, raw_status)
    class_suffix = _status_class_suffix(raw_status)
    return f'<span class="status-badge status-{class_suffix}">{html.escape(label)}</span>'


def _status_badge_templates_json() -> str:
    """Return helper-generated badge labels for client-side row creation."""
    payload = {"known": {status: _STATUS_LABELS[status] for status in sorted(_STATUS_LABELS)}}
    serialized = json.dumps(payload, separators=(",", ":"))
    return (
        serialized.replace("&", "\\u0026")
        .replace("<", "\\u003c")
        .replace(">", "\\u003e")
        .replace("'", "\\u0027")
    )


def _error_html(message: str) -> str:
    """Render a standardized error HTML fragment."""
    return f"<div class='error-box' role='alert' aria-live='assertive'>{html.escape(message)}</div>"


def _success_html(message: str) -> str:
    """Render a standardized success HTML fragment."""
    return f"<div class='success-box' role='status' aria-live='polite'>{html.escape(message)}</div>"


def _rate_limit_error_html(detail: str) -> str:
    """Render the HTMX rate-limit error fragment."""
    return (
        '<div class="error-box" role="alert" aria-live="assertive">\n'
        '  <svg class="h-5 w-5 flex-shrink-0 mt-0.5" '
        'xmlns="http://www.w3.org/2000/svg" fill="none" '
        'viewBox="0 0 24 24" stroke="currentColor" stroke-width="2">\n'
        '    <path stroke-linecap="round" stroke-linejoin="round" '
        'd="M12 9v3.75m9-.75a9 9 0 11-18 0 9 9 0 '
        '0118 0zm-9 3.75h.008v.008H12v-.008z" />\n'
        "  </svg>\n"
        "  <div>\n"
        "    <strong>Rate limit exceeded</strong>\n"
        f'    <p class="text-sm mt-1 opacity-80">{html.escape(detail)}. '
        "Please wait before submitting another link.</p>\n"
        "  </div>\n"
        "</div>"
    )

"""Package-local helpers for web route modules."""

import os
import posixpath
import re
import secrets
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Protocol
from urllib.parse import quote, urlparse

from fastapi import Request, Response
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from app.api.routes.web_helpers import (
    _error_html,
    _status_badge_html,
    _status_badge_templates_json,
)
from app.auth import set_token_cookies
from core.config import settings
from core.logging_config import get_logger

logger = get_logger(__name__)
_TEMPLATE_DIR = Path(__file__).resolve().parents[3] / "templates"
templates = Jinja2Templates(directory=str(_TEMPLATE_DIR))
templates.env.globals["status_badge_html"] = _status_badge_html
templates.env.globals["status_badge_templates_json"] = _status_badge_templates_json
_ALLOWED_REDIRECT_HOSTS: tuple[str, ...] = ("/web/",)
_CSRF_TOKEN_PATTERN = re.compile(r"^[0-9a-f]{32}$", re.IGNORECASE)
_ErrorMap = dict[str, tuple[str, dict[str, str]]]


class _SettingsWithStoragePath(Protocol):
    storage_path: str


def _field_error(message: str, *fields: str) -> tuple[str, dict[str, str]]:
    return message, dict.fromkeys(fields, message)


_SETTINGS_ERRORS: _ErrorMap = {
    "username_too_short": _field_error("Username must be at least 3 characters", "username"),
    "bad_current_password": _field_error("Current password is incorrect", "current_password"),
    "password_mismatch": _field_error("New passwords do not match", "new_password_confirm"),
    "password_too_short": _field_error(
        "New password must be at least 8 characters",
        "new_password",
    ),
    "password_too_long": _field_error(
        "New password must be at most 128 characters",
        "new_password",
    ),
    "bad_password": _field_error("Password is incorrect", "delete_password"),
    "delete_confirmation": _field_error(
        "Please type DELETE to confirm account deletion",
        "confirm_text",
    ),
    "file_cleanup": ("Unable to remove all downloaded files. Account was not deleted.", {}),
    "csrf": ("Invalid CSRF token", {}),
}
_LOGIN_ERRORS: _ErrorMap = {
    "1": _field_error("Invalid email or password", "email", "password"),
    "invalid_credentials": _field_error("Invalid email or password", "email", "password"),
    "csrf": ("Invalid CSRF token", {}),
    "inactive": _field_error("Account is inactive", "email"),
}
_REGISTER_ERRORS: _ErrorMap = {
    "password_mismatch": _field_error("Passwords do not match", "password_confirm"),
    "password_too_short": _field_error("Password must be at least 8 characters", "password"),
    "password_too_long": _field_error("Password must be at most 128 characters", "password"),
    "email_exists": _field_error("Email already registered", "email"),
    "csrf": ("Invalid CSRF token", {}),
}


def _downloads_base_path(settings_override: _SettingsWithStoragePath | None = None) -> str:
    active_settings = settings if settings_override is None else settings_override
    return os.path.join(active_settings.storage_path, "downloads")


def _new_csrf_token() -> str:
    return uuid.uuid4().hex


def _validated_csrf_token(token: str | None) -> str | None:
    """Return a safe, locally-reconstructed CSRF token, or None if invalid.

    The token format is a UUID4 hex string (32 hex chars). Rather than echoing
    the caller-supplied string back after a regex check, the value is
    re-derived from the parsed integer. The result is byte-identical for every
    accepted input but is a string this function constructed, so no
    request-controlled bytes can reach `Set-Cookie` verbatim. The residual
    taint path (valid token echoed from the request cookie) is covered by an
    inline CodeQL suppression at the `set_cookie` call in
    `set_csrf_token_cookie`.
    """
    candidate = str(token or "")
    if not _CSRF_TOKEN_PATTERN.fullmatch(candidate):
        return None
    return format(int(candidate, 16), "032x")


def _validate_redirect_url(url: str | None, default: str) -> str:
    if not url:
        return default
    normalized = url.strip().replace("\\", "/")
    parsed = urlparse(normalized)
    if (
        parsed.scheme
        or parsed.netloc
        or normalized.startswith("//")
        or not normalized.startswith("/")
    ):
        return default
    had_trailing_slash = normalized.endswith("/")
    normalized = posixpath.normpath(normalized)
    if had_trailing_slash and normalized != "/":
        normalized += "/"
    return normalized if any(normalized.startswith(p) for p in _ALLOWED_REDIRECT_HOSTS) else default


def get_csrf_token(request: Request) -> str:
    token = _validated_csrf_token(request.cookies.get("csrf_token"))
    if token is not None:
        return token
    return _new_csrf_token()


def set_csrf_token_cookie(response: Response, token: str) -> str:
    safe_token = _validated_csrf_token(token) or _new_csrf_token()
    response.set_cookie(
        key="csrf_token",
        # codeql[py/cookie-injection] — safe by construction: `safe_token` is
        # either a fresh uuid4 hex value or a request token re-derived through
        # `_validated_csrf_token`, which requires an exact 32-hex-char match
        # and rebuilds the value from the parsed integer (see its docstring).
        # The client already holds this exact token in its own cookie, so no
        # attacker-controlled bytes can be introduced into Set-Cookie.
        value=quote(safe_token, safe=""),
        httponly=True,
        secure=settings.cookie_secure,
        samesite="strict",
        path="/",
        max_age=86400,
    )
    return safe_token


def rotate_csrf_token(response: Response) -> str:
    new_token = _new_csrf_token()
    set_csrf_token_cookie(response, new_token)
    return new_token


def render_csrf_page(
    request: Request,
    template_name: str,
    **extra_context: object,
) -> HTMLResponse:
    """Render a page with a CSRF form token and a matching cookie.

    A valid token from the request cookie is reused (stable across page
    views); otherwise a fresh server-generated token is minted. The rendered
    token always matches the cookie written to the response.
    """
    token = get_csrf_token(request)
    response = templates.TemplateResponse(
        request,
        template_name,
        get_template_context(request, csrf_token=token, **extra_context),
    )
    set_csrf_token_cookie(response, token)
    return response


def get_template_context(
    request: Request,
    csrf_token: str | None = None,
    **extra_context: object,
) -> dict[str, object]:
    """
    Build the common context values used to render a web template.

    Parameters:
        request (Request): The current web request.
        csrf_token (str | None): An optional CSRF token to include in the context.
        **extra_context (object): Additional values to include in the template context.

    Returns:
        dict[str, object]: The template context containing request metadata and extra values.
    """
    context: dict[str, object] = {
        "request": request,
        "current_year": datetime.now(UTC).year,
        "csrf_token": csrf_token if csrf_token is not None else get_csrf_token(request),
        "nonce": getattr(request.state, "nonce", ""),
    }
    context.update(extra_context)
    return context


def is_htmx_request(request: Request) -> bool:
    return str(request.headers.get("HX-Request", "")).lower() == "true"


async def validate_csrf_token(request: Request) -> bool:
    """
    Validate the CSRF token for a request.

    Parameters:
        request (Request): The incoming HTTP request.

    Returns:
        bool: `true` for safe HTTP methods or a matching CSRF token, `false` otherwise.
    """
    if request.method in ("GET", "HEAD", "OPTIONS"):
        return True
    cookie_token = request.cookies.get("csrf_token")
    if not cookie_token:
        return False
    header_token = request.headers.get("X-CSRF-Token")
    if header_token and secrets.compare_digest(header_token, cookie_token):
        return True
    if header_token:
        return False
    try:
        form_token = (await request.form()).get("csrf_token")
    except Exception:
        return False
    return bool(form_token and secrets.compare_digest(str(form_token), cookie_token))


def _resolve_error(error_code: str | None, mapping: _ErrorMap) -> tuple[str | None, dict[str, str]]:
    """Resolve an error code to its message and field-level errors.

    Parameters:
        error_code (str | None): The error code to resolve.
        mapping (_ErrorMap): The mapping of error codes to error details.

    Returns:
        tuple[str | None, dict[str, str]]: The associated message and field-level errors, or empty error details when the code is absent or unmapped.
    """
    return mapping.get(error_code, (None, {})) if error_code is not None else (None, {})


def _resolve_settings_errors(error_code: str | None) -> tuple[str | None, dict[str, str]]:
    return _resolve_error(error_code, _SETTINGS_ERRORS)


def _resolve_login_errors(error_code: str | None) -> tuple[str | None, dict[str, str]]:
    return _resolve_error(error_code, _LOGIN_ERRORS)


def _resolve_register_errors(error_code: str | None) -> tuple[str | None, dict[str, str]]:
    return _resolve_error(error_code, _REGISTER_ERRORS)


def _htmx_or_redirect(
    request: Request,
    htmx_status: int,
    htmx_content: str,
    redirect_url: str,
    redirect_status: int = 303,
) -> HTMLResponse | RedirectResponse:
    if is_htmx_request(request):
        return HTMLResponse(status_code=htmx_status, content=htmx_content)
    return RedirectResponse(url=redirect_url, status_code=redirect_status)


def _error_response(
    request: Request,
    status_code: int,
    message: str,
    redirect_url: str,
) -> HTMLResponse | RedirectResponse:
    """
    Create an error response for an HTMX request or redirect the client otherwise.

    Parameters:
        request (Request): The incoming web request.
        status_code (int): The HTTP status code for the response.
        message (str): The error message to display.
        redirect_url (str): The URL to use for non-HTMX requests.

    Returns:
        HTMLResponse | RedirectResponse: An HTML error response for HTMX requests or a redirect response otherwise.
    """
    return _htmx_or_redirect(request, status_code, _error_html(message), redirect_url)


def _auth_success_response(
    request: Request,
    access_token: str,
    refresh_token: str,
    redirect_url: str,
) -> HTMLResponse | RedirectResponse:
    response: HTMLResponse | RedirectResponse
    if is_htmx_request(request):
        response = HTMLResponse(status_code=200, content="")
        response.headers["HX-Redirect"] = redirect_url
    else:
        response = RedirectResponse(url=redirect_url, status_code=303)
    set_token_cookies(response, access_token, refresh_token)
    rotate_csrf_token(response)
    return response


def _login_success_response(
    request: Request,
    access_token: str,
    refresh_token: str,
    safe_redirect: str,
    _response: Response,
) -> HTMLResponse | RedirectResponse:
    return _auth_success_response(request, access_token, refresh_token, safe_redirect)


def _register_success_response(
    request: Request,
    access_token: str,
    refresh_token: str,
) -> HTMLResponse | RedirectResponse:
    """Complete registration by establishing the authenticated session and directing the user to the downloads page."""
    return _auth_success_response(request, access_token, refresh_token, "/web/downloads")


from app.api.routes.web.web_auth_helpers import (  # noqa: E402,F401
    _change_password_response,
    _demo_user_or_raise,
    _prime_demo_jobs,
    _register_user_or_error_response,
)

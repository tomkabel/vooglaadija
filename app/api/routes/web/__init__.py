import asyncio
import os
import posixpath
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Annotated
from urllib.parse import urlparse

from fastapi import APIRouter, Form, HTTPException, Query, Request, Response, status
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from app.api.dependencies import CurrentUserFromCookie, DbSession
from app.api.rate_limit_config import limiter
from app.api.routes.web_helpers import (
    _error_html,
    _status_badge_html,
    _status_badge_templates_json,
    _success_html,
)
from app.auth import clear_token_cookies, set_token_cookies
from app.services.auth_service import hash_password, verify_password
from app.utils.username import default_username_from_email as _default_username_from_email
from app.utils.validators import validate_password
from core.config import settings
from core.logging_config import get_logger
from core.models.download_job import DownloadJob
from core.models.user import User, not_deleted
from core.queue import enqueue_job
from core.redis_client import get_all_chaos_status, get_redis_client
from core.utils.security import validate_path

logger = get_logger(__name__)

router = APIRouter(prefix="/web", tags=["web"])

# Resolve templates relative to this file so it works regardless of CWD.
_TEMPLATE_DIR = Path(__file__).resolve().parents[3] / "templates"
templates = Jinja2Templates(directory=str(_TEMPLATE_DIR))
templates.env.globals["status_badge_html"] = _status_badge_html
templates.env.globals["status_badge_templates_json"] = _status_badge_templates_json

# Allowed redirect targets — only internal paths
_ALLOWED_REDIRECT_HOSTS: tuple[str, ...] = ("/web/",)


def _downloads_base_path() -> str:
    """Build the configured downloads directory path."""
    return os.path.join(settings.storage_path, "downloads")


def _validate_redirect_url(url: str | None, default: str) -> str:
    """Validate a redirect URL to prevent open redirect attacks.

    Only allows relative URLs starting with known safe prefixes.
    """
    if not url:
        return default

    # Normalize and strip whitespace / backslashes
    normalized = url.strip().replace("\\", "/")

    # Parse the URL to detect schemes and hosts robustly
    parsed = urlparse(normalized)

    # Reject any URL with a scheme or network location (host)
    if parsed.scheme or parsed.netloc:
        return default

    # Reject protocol-relative URLs like //example.com
    if normalized.startswith("//"):
        return default

    # Only allow absolute paths that start with known safe prefixes
    if not normalized.startswith("/"):
        return default

    # Normalize path to resolve . and .. components
    had_trailing_slash = normalized.endswith("/")
    normalized = posixpath.normpath(normalized)
    if had_trailing_slash and normalized != "/":
        normalized += "/"

    if any(normalized.startswith(prefix) for prefix in _ALLOWED_REDIRECT_HOSTS):
        return normalized

    return default


# ========================
# Helpers
# ========================


def get_csrf_token(request: Request) -> str:
    """Get or create CSRF token for the request."""
    token: str | None = request.cookies.get("csrf_token")
    if not token:
        token = uuid.uuid4().hex
    return token


def set_csrf_token_cookie(response: Response, token: str) -> None:
    """Set CSRF token in response cookie with security hardening.

    httponly=True prevents JavaScript access (XSS theft protection).
    secure=True ensures cookie only sent over HTTPS.
    samesite="Strict" prevents CSRF attacks via cross-site requests.
    max_age=86400 sets 24-hour expiry matching session lifetime.
    """
    response.set_cookie(
        key="csrf_token",
        value=token,
        httponly=True,
        secure=settings.cookie_secure,
        samesite="strict",
        path="/",
        max_age=86400,
    )


def rotate_csrf_token(response: Response) -> str:
    """Generate a new CSRF token and set it in the response cookie.

    Call this after every successful state-changing operation to rotate
    the CSRF token, limiting the window of exposure if a token is leaked.

    Returns the new token value.
    """
    new_token = uuid.uuid4().hex
    set_csrf_token_cookie(response, new_token)
    return new_token


def get_template_context(request: Request, csrf_token: str | None = None, **extra_context):
    """Get common template context including current year, CSRF token, and CSP nonce.

    Args:
        request: The FastAPI request object
        csrf_token: Optional pre-generated CSRF token. If not provided,
                    a new one will be generated via get_csrf_token(request).
                    Use this to ensure the same token is used for both
                    the cookie and the template context.
    """
    token = csrf_token if csrf_token is not None else get_csrf_token(request)
    # Get nonce from request.state (set by security headers middleware)
    nonce = getattr(request.state, "nonce", "")
    context = {
        "request": request,
        "current_year": datetime.now(UTC).year,
        "csrf_token": token,
        "nonce": nonce,
    }
    context.update(extra_context)
    return context


def is_htmx_request(request: Request) -> bool:
    """Check if request is from HTMX."""
    hx_request: str | None = request.headers.get("HX-Request")
    return hx_request == "true"


async def validate_csrf_token(request: Request) -> bool:
    """Validate CSRF token using the double-submit cookie pattern.

    Returns True if token is valid or if this is not a state-changing request.

    Validation strategies:
    1. Header token (X-CSRF-Token) matches cookie token (standard double-submit)
    2. Form token matches cookie token (fallback for non-HTMX forms)

    Both strategies require the cookie to be present and match the submitted
    token. A request without a CSRF cookie is always rejected for state-changing
    methods.
    """
    if request.method in ("GET", "HEAD", "OPTIONS"):
        return True

    cookie_token = request.cookies.get("csrf_token")
    if not cookie_token:
        return False

    header_token = request.headers.get("X-CSRF-Token")

    if header_token == cookie_token:
        return True

    if not header_token:
        try:
            form_data = await request.form()
            form_token = form_data.get("csrf_token")
            if form_token and str(form_token) == cookie_token:
                return True
        except Exception:
            pass

    return False


def _resolve_settings_errors(error_code: str | None) -> tuple[str | None, dict[str, str]]:
    """Map settings error code to summary and field-level errors."""
    error_map: dict[str, tuple[str, dict[str, str]]] = {
        "username_too_short": (
            "Username must be at least 3 characters",
            {"username": "Username must be at least 3 characters"},
        ),
        "bad_current_password": (
            "Current password is incorrect",
            {"current_password": "Current password is incorrect"},
        ),
        "password_mismatch": (
            "New passwords do not match",
            {"new_password_confirm": "New passwords do not match"},
        ),
        "password_too_short": (
            "New password must be at least 8 characters",
            {"new_password": "New password must be at least 8 characters"},
        ),
        "password_too_long": (
            "New password must be at most 128 characters",
            {"new_password": "New password must be at most 128 characters"},
        ),
        "bad_password": (
            "Password is incorrect",
            {"delete_password": "Password is incorrect"},
        ),
        "delete_confirmation": (
            "Please type DELETE to confirm account deletion",
            {"confirm_text": "Please type DELETE to confirm account deletion"},
        ),
        "file_cleanup": (
            "Unable to remove all downloaded files. Account was not deleted.",
            {},
        ),
        "csrf": ("Invalid CSRF token", {}),
    }
    if error_code is None:
        return None, {}
    result = error_map.get(error_code)
    if result is None:
        return None, {}
    message, field_errors = result
    return message, field_errors


def _htmx_or_redirect(
    request: Request,
    htmx_status: int,
    htmx_content: str,
    redirect_url: str,
    redirect_status: int = 303,
) -> HTMLResponse | RedirectResponse:
    """Return HTMX response or redirect based on request type."""
    if is_htmx_request(request):
        return HTMLResponse(status_code=htmx_status, content=htmx_content)
    return RedirectResponse(url=redirect_url, status_code=redirect_status)


def _resolve_login_errors(error_code: str | None) -> tuple[str | None, dict[str, str]]:
    """Map login error code to summary and field-level errors."""
    if error_code in {"1", "invalid_credentials"}:
        message = "Invalid email or password"
        return message, {"email": message, "password": message}
    if error_code == "csrf":
        return "Invalid CSRF token", {}
    if error_code == "inactive":
        message = "Account is inactive"
        return message, {"email": message}
    return None, {}


def _resolve_register_errors(error_code: str | None) -> tuple[str | None, dict[str, str]]:
    """Map register error code to summary and field-level errors."""
    if error_code == "password_mismatch":
        message = "Passwords do not match"
        return message, {"password_confirm": message}
    if error_code == "password_too_short":
        message = "Password must be at least 8 characters"
        return message, {"password": message}
    if error_code == "password_too_long":
        message = "Password must be at most 128 characters"
        return message, {"password": message}
    if error_code == "email_exists":
        message = "Email already registered"
        return message, {"email": message}
    if error_code == "csrf":
        return "Invalid CSRF token", {}
    return None, {}


def _login_success_response(
    request: Request,
    access_token: str,
    refresh_token: str,
    safe_redirect: str,
    response: Response,
) -> HTMLResponse | RedirectResponse:
    """Handle successful login response for both HTMX and regular requests."""
    if is_htmx_request(request):
        resp = HTMLResponse(status_code=200, content="")
        resp.headers["HX-Redirect"] = safe_redirect
        set_token_cookies(resp, access_token, refresh_token, secure=settings.cookie_secure)
        rotate_csrf_token(resp)
        return resp
    redirect = RedirectResponse(url=safe_redirect, status_code=303)
    set_token_cookies(redirect, access_token, refresh_token, secure=settings.cookie_secure)
    rotate_csrf_token(redirect)
    return redirect


def _register_success_response(
    request: Request,
    access_token: str,
    refresh_token: str,
) -> HTMLResponse | RedirectResponse:
    """Handle successful registration response for both HTMX and regular requests."""
    if is_htmx_request(request):
        resp = HTMLResponse(status_code=200, content="")
        resp.headers["HX-Redirect"] = "/web/downloads"
        set_token_cookies(resp, access_token, refresh_token, secure=settings.cookie_secure)
        rotate_csrf_token(resp)
        return resp
    redirect = RedirectResponse(url="/web/downloads", status_code=303)
    set_token_cookies(redirect, access_token, refresh_token, secure=settings.cookie_secure)
    rotate_csrf_token(redirect)
    return redirect


async def _prime_demo_jobs(user_id: uuid.UUID, db: DbSession) -> None:
    """Prime pending demo jobs for processing."""
    pending_result = await db.execute(
        select(DownloadJob).where(
            DownloadJob.user_id == user_id,
            DownloadJob.status == "pending",
        )
    )
    pending_jobs = pending_result.scalars().all()
    if not pending_jobs:
        return

    r = get_redis_client()
    already_primed = await r.exists("demo:jobs_primed")
    if already_primed:
        return

    for i, job in enumerate(pending_jobs):
        if i > 0:
            await asyncio.sleep(0.2)
        await enqueue_job(job.id)
    await r.setex("demo:jobs_primed", 30, "1")
    logger.info("demo_jobs_primed", count=len(pending_jobs))


async def _demo_user_or_raise(db: DbSession, demo_email: str) -> User:
    """Return the active demo user or raise the existing HTTP errors."""
    result = await db.execute(select(User).where(User.email == demo_email, not_deleted()))
    user = result.scalar_one_or_none()
    if user is None:
        logger.error("demo_user_not_found", email=demo_email)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Demo user not found. Run scripts/seed_demo_data.py to seed the demo account.",
        )
    if not user.is_active:
        logger.error("demo_user_inactive", email=demo_email)
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Demo user is inactive.")
    return user


async def _change_password_response(
    request: Request,
    current_password: str,
    new_password: str,
    new_password_confirm: str,
    current_user: CurrentUserFromCookie,
    db: DbSession,
) -> HTMLResponse | RedirectResponse:
    """Apply password-change validation and return the existing web response."""
    if not await validate_csrf_token(request):
        return _htmx_or_redirect(
            request, 403, _error_html("Invalid CSRF token"), "/web/settings?error=csrf"
        )
    if not await verify_password(current_password, current_user.password_hash):
        return _htmx_or_redirect(
            request,
            400,
            _error_html("Current password is incorrect"),
            "/web/settings?error=bad_current_password",
        )
    if new_password != new_password_confirm:
        return _htmx_or_redirect(
            request,
            400,
            _error_html("New passwords do not match"),
            "/web/settings?error=password_mismatch",
        )
    pw_error = validate_password(new_password)
    if pw_error:
        error_code = "password_too_short" if len(new_password) < 8 else "password_too_long"
        return _htmx_or_redirect(
            request, 400, _error_html(pw_error), f"/web/settings?error={error_code}"
        )

    current_user.password_hash = await hash_password(new_password)
    current_user.token_version += 1
    await db.commit()
    result = _htmx_or_redirect(
        request,
        200,
        _success_html("Password changed successfully"),
        "/web/settings?updated=password",
    )
    rotate_csrf_token(result)
    return result


async def _register_user_or_error_response(
    request: Request,
    email: str,
    password: str,
    password_confirm: str,
    db: DbSession,
) -> tuple[User | None, HTMLResponse | RedirectResponse | None]:
    """Validate registration input and create a user, or return the existing error response."""
    if not await validate_csrf_token(request):
        return None, _htmx_or_redirect(
            request, 403, _error_html("Invalid CSRF token"), "/web/register?error=csrf"
        )
    if password != password_confirm:
        return None, _htmx_or_redirect(
            request,
            400,
            _error_html("Passwords do not match"),
            "/web/register?error=password_mismatch",
        )
    pw_error = validate_password(password)
    if pw_error:
        error_code = "password_too_short" if len(password) < 8 else "password_too_long"
        return None, _htmx_or_redirect(
            request, 400, _error_html(pw_error), f"/web/register?error={error_code}"
        )

    result = await db.execute(select(User).where(User.email == email, not_deleted()))
    if result.scalar_one_or_none():
        return None, _htmx_or_redirect(
            request,
            409,
            _error_html("Email already registered"),
            "/web/register?error=email_exists",
        )

    user = User(
        id=uuid.uuid4(),
        username=_default_username_from_email(email),
        email=email,
        password_hash=await hash_password(password),
    )
    db.add(user)
    try:
        await db.commit()
    except IntegrityError:
        await db.rollback()
        return None, _htmx_or_redirect(
            request,
            409,
            _error_html("Email already registered"),
            "/web/register?error=email_exists",
        )
    await db.refresh(user)
    return user, None


def _include_auth_router() -> None:
    import app.api.routes.web.web_auth as web_auth_module

    globals()["web_auth"] = web_auth_module
    router.include_router(web_auth_module.router)


_include_auth_router()


def _include_downloads_router() -> None:
    import app.api.routes.web.web_downloads as web_downloads_module

    globals()["web_downloads"] = web_downloads_module
    router.include_router(web_downloads_module.router)


_include_downloads_router()


# ========================
# CHAOS LAB (gated by FEATURE_CHAOS_API_ENABLED)
# ========================


@router.get("/chaos-lab")
async def chaos_lab_page(request: Request):
    """Render the chaos engineering lab page for live demo."""
    if not settings.feature_chaos_api_enabled:
        raise HTTPException(status_code=404, detail="Not Found")

    token = get_csrf_token(request)
    response = templates.TemplateResponse(
        request,
        "chaos-lab.html",
        get_template_context(request, csrf_token=token),
    )
    set_csrf_token_cookie(response, token)
    return response


@router.get("/chaos-lab/status")
async def chaos_lab_status(request: Request):
    """HTMX partial: return current chaos flag status for polling."""
    if not settings.feature_chaos_api_enabled:
        raise HTTPException(status_code=404, detail="Not Found")

    status_data = await get_all_chaos_status()

    return templates.TemplateResponse(
        request,
        "partials/_chaos_status.html",
        get_template_context(request, status=status_data),
    )


# ========================
# PRESENTATION SLIDES
# ========================


@router.get("/slides")
async def presentation_slides(request: Request):
    """Render the TOP1 demo presentation slides (3-slide deck).

    Standalone HTML with CSS animations, inline SVG architecture diagram,
    and keyboard/click navigation. Used during the live demo presentation.
    """
    return templates.TemplateResponse(
        request,
        "slides/presentation.html",
        {},
    )


# ========================
# PROTECTED ROUTES
# ========================


@router.get("/settings")
async def settings_page(
    request: Request,
    current_user: CurrentUserFromCookie,
    error: Annotated[str | None, Query(max_length=100)] = None,
):
    """Render settings page for the current user."""
    token = get_csrf_token(request)
    username = current_user.username or _default_username_from_email(current_user.email)
    error_message, field_errors = _resolve_settings_errors(error)
    response = templates.TemplateResponse(
        request,
        "settings.html",
        get_template_context(
            request,
            csrf_token=token,
            current_user=current_user,
            username=username,
            error=error_message,
            field_errors=field_errors,
        ),
    )
    set_csrf_token_cookie(response, token)
    return response


@router.post("/settings/username")
@limiter.limit("10/minute")
async def update_username(
    request: Request,
    username: Annotated[str, Form(max_length=64)],
    current_user: CurrentUserFromCookie,
    db: DbSession,
):
    """Update current user's username."""
    if not await validate_csrf_token(request):
        return _htmx_or_redirect(
            request, 403, _error_html("Invalid CSRF token"), "/web/settings?error=csrf"
        )

    clean_username = username.strip()
    if len(clean_username) < 3:
        return _htmx_or_redirect(
            request,
            400,
            _error_html("Username must be at least 3 characters"),
            "/web/settings?error=username_too_short",
        )

    current_user.username = clean_username
    await db.commit()

    return _htmx_or_redirect(
        request,
        200,
        _success_html("Username updated successfully"),
        "/web/settings?updated=username",
    )


def _cleanup_job_files(jobs: list, logger) -> tuple[bool, list[str]]:
    """Clean up files for a list of download jobs. Returns (all_cleaned, failures)."""
    file_cleanup_failures: list[str] = []
    for job in jobs:
        if not job.file_path:
            continue
        try:
            safe_path = validate_path(_downloads_base_path(), job.file_path)
            if os.path.isfile(safe_path):
                os.remove(safe_path)
        except (ValueError, PermissionError):
            logger.warning(
                "Account deletion aborted: invalid download file path for job %s: %s",
                job.id,
                job.file_path,
            )
            file_cleanup_failures.append(job.file_path)
        except OSError as e:
            logger.warning(
                "Account deletion aborted: failed to remove file for job %s (%s): %s",
                job.id,
                job.file_path,
                e,
            )
            file_cleanup_failures.append(job.file_path)
        except Exception:
            logger.exception(
                "Account deletion aborted: unexpected error cleaning file for job %s (%s)",
                job.id,
                job.file_path,
            )
            file_cleanup_failures.append(job.file_path)
    return (not file_cleanup_failures, file_cleanup_failures)


@router.post("/settings/delete-account")
@limiter.limit("3/minute")
async def delete_account(
    request: Request,
    password: Annotated[str, Form(max_length=255)],
    confirm_text: Annotated[str, Form(max_length=16)],
    current_user: CurrentUserFromCookie,
    db: DbSession,
):
    """Delete current user's account and associated downloads."""
    if not await validate_csrf_token(request):
        return _htmx_or_redirect(
            request, 403, _error_html("Invalid CSRF token"), "/web/settings?error=csrf"
        )

    if confirm_text.strip().upper() != "DELETE":
        return _htmx_or_redirect(
            request,
            400,
            _error_html("Please type DELETE to confirm account deletion"),
            "/web/settings?error=delete_confirmation",
        )

    if not await verify_password(password, current_user.password_hash):
        return _htmx_or_redirect(
            request,
            400,
            _error_html("Password is incorrect"),
            "/web/settings?error=bad_password",
        )

    result = await db.execute(select(DownloadJob).where(DownloadJob.user_id == current_user.id))
    jobs = result.scalars().all()

    all_cleaned, _file_cleanup_failures = _cleanup_job_files(list(jobs), logger)

    if not all_cleaned:
        return _htmx_or_redirect(
            request,
            500,
            _error_html(
                "Could not remove all downloaded files. Your account was not deleted. "
                "Please try again or contact support."
            ),
            "/web/settings?error=file_cleanup",
        )

    for job in jobs:
        await db.delete(job)

    await db.delete(current_user)
    await db.commit()

    if is_htmx_request(request):
        resp = HTMLResponse(status_code=200, content="")
        resp.headers["HX-Redirect"] = "/web/login?account_deleted=1"
        clear_token_cookies(resp)
        return resp

    redirect = RedirectResponse(url="/web/login?account_deleted=1", status_code=303)
    clear_token_cookies(redirect)
    return redirect

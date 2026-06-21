import asyncio
import html
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
from sqlalchemy import delete, select
from sqlalchemy.exc import IntegrityError

from app.api.dependencies import CurrentUserFromCookie, DbSession
from app.api.rate_limit_config import limiter
from app.auth import (
    clear_token_cookies,
    create_access_token,
    create_refresh_token,
    set_token_cookies,
)
from app.config import settings
from app.logging_config import get_logger
from app.models.download_job import DownloadJob
from app.models.outbox import Outbox
from app.models.user import User, not_deleted
from app.services.auth_service import hash_password, verify_password
from app.services.outbox_service import write_job_to_outbox
from app.services.redis_client import get_all_chaos_status, get_redis_client
from app.services.yt_dlp_service import resolve_video_title
from app.utils.security import validate_file_path as _validate_file_path
from app.utils.username import default_username_from_email as _default_username_from_email
from app.utils.validators import is_supported_url, validate_password
from worker.queue import enqueue_job

logger = get_logger(__name__)

router = APIRouter(prefix="/web", tags=["web"])

# Resolve templates relative to this file so it works regardless of CWD.
# web.py -> app/api/routes/web.py, so parent^3 = app/
_TEMPLATE_DIR = Path(__file__).resolve().parent.parent.parent / "templates"
templates = Jinja2Templates(directory=str(_TEMPLATE_DIR))

# Allowed redirect targets — only internal paths
_ALLOWED_REDIRECT_HOSTS: tuple[str, ...] = ("/web/",)


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


def _error_html(message: str) -> str:
    """Render a standardized error HTML fragment."""
    return f"<div class='error-box' role='alert' aria-live='assertive'>{html.escape(message)}</div>"


def _success_html(message: str) -> str:
    """Render a standardized success HTML fragment."""
    return f"<div class='success-box' role='status' aria-live='polite'>{html.escape(message)}</div>"


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


def _login_success_response(
    request: Request,
    access_token: str,
    refresh_token: str,
    safe_redirect: str,
    response: Response,
) -> HTMLResponse | RedirectResponse:
    """Handle successful login response for both HTMX and regular requests.

    Rotates the CSRF token to prevent reuse of the pre-authentication token.
    """
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
    """Handle successful registration response for both HTMX and regular requests.

    Rotates the CSRF token to prevent reuse of the pre-registration token.
    """
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


# ========================
# PUBLIC ROUTES (no auth)
# ========================


@router.get("/login")
async def login_page(
    request: Request,
    return_url: str = "/web/downloads",
    error: Annotated[str | None, Query(max_length=100)] = None,
):
    """Render login page."""
    token = get_csrf_token(request)
    error_message, field_errors = _resolve_login_errors(error)
    response = templates.TemplateResponse(
        request,
        "login.html",
        get_template_context(
            request,
            csrf_token=token,
            return_url=return_url,
            error=error_message,
            field_errors=field_errors,
        ),
    )
    set_csrf_token_cookie(response, token)
    return response


@router.post("/login")
@limiter.limit("5/minute")
async def login_form(
    request: Request,
    response: Response,
    db: DbSession,
    email: Annotated[str, Form(max_length=255)],
    password: Annotated[str, Form(max_length=255)],
    return_url: Annotated[str | None, Form(max_length=500)] = None,
):
    """Handle login form submission via HTMX or regular POST."""
    # CSRF validation
    if not await validate_csrf_token(request):
        return _htmx_or_redirect(
            request, 403, _error_html("Invalid CSRF token"), "/web/login?error=csrf"
        )

    result = await db.execute(select(User).where(User.email == email, not_deleted()))
    user = result.scalar_one_or_none()

    if user is None or not await verify_password(password, user.password_hash):
        return _htmx_or_redirect(
            request, 401, _error_html("Invalid email or password"), "/web/login?error=1"
        )

    if not user.is_active:
        return _htmx_or_redirect(
            request, 401, _error_html("Account is inactive"), "/web/login?error=inactive"
        )

    access_token = create_access_token(user.id, token_version=user.token_version)
    refresh_token = create_refresh_token(user.id, token_version=user.token_version)

    safe_redirect = _validate_redirect_url(return_url, "/web/downloads")

    return _login_success_response(request, access_token, refresh_token, safe_redirect, response)


@router.get("/register")
async def register_page(
    request: Request,
    error: Annotated[str | None, Query(max_length=100)] = None,
):
    """Render register page."""
    token = get_csrf_token(request)
    error_message, field_errors = _resolve_register_errors(error)
    response = templates.TemplateResponse(
        request,
        "register.html",
        get_template_context(
            request,
            csrf_token=token,
            error=error_message,
            field_errors=field_errors,
        ),
    )
    set_csrf_token_cookie(response, token)
    return response


@router.post("/register")
@limiter.limit("5/minute")
async def register_form(
    request: Request,
    email: Annotated[str, Form(max_length=255)],
    password: Annotated[str, Form(max_length=255)],
    password_confirm: Annotated[str, Form(max_length=255)],
    db: DbSession,
):
    """Handle registration form submission via HTMX or regular POST."""
    if not await validate_csrf_token(request):
        return _htmx_or_redirect(
            request, 403, _error_html("Invalid CSRF token"), "/web/register?error=csrf"
        )

    if password != password_confirm:
        return _htmx_or_redirect(
            request,
            400,
            _error_html("Passwords do not match"),
            "/web/register?error=password_mismatch",
        )

    pw_error = validate_password(password)
    if pw_error:
        error_code = "password_too_short" if len(password) < 8 else "password_too_long"
        return _htmx_or_redirect(
            request, 400, _error_html(pw_error), f"/web/register?error={error_code}"
        )

    result = await db.execute(select(User).where(User.email == email, not_deleted()))
    existing = result.scalar_one_or_none()
    if existing:
        return _htmx_or_redirect(
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
        return _htmx_or_redirect(
            request,
            409,
            _error_html("Email already registered"),
            "/web/register?error=email_exists",
        )

    await db.refresh(user)

    access_token = create_access_token(user.id, token_version=user.token_version)
    refresh_token = create_refresh_token(user.id, token_version=user.token_version)

    return _register_success_response(request, access_token, refresh_token)


DEMO_EMAIL = "demo@vooglaadija.io"


@router.get("/demo-login")
@limiter.limit("3/minute")
async def demo_login(
    request: Request,
    response: Response,
    db: DbSession,
):
    """One-click demo login — authenticates as pre-seeded demo user.

    Sets JWT cookies and redirects to /web/downloads.
    Returns 500 if demo user does not exist (seed script not run).
    Rate-limited to 3/minute to prevent abuse.
    """
    result = await db.execute(select(User).where(User.email == DEMO_EMAIL, not_deleted()))
    user = result.scalar_one_or_none()

    if user is None:
        logger.error("demo_user_not_found", email=DEMO_EMAIL)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Demo user not found. Run scripts/seed_demo_data.py to seed the demo account.",
        )

    if not user.is_active:
        logger.error("demo_user_inactive", email=DEMO_EMAIL)
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Demo user is inactive.",
        )

    access_token = create_access_token(user.id, email=user.email, token_version=user.token_version)
    refresh_token = create_refresh_token(user.id, token_version=user.token_version)

    # Prime demo jobs for processing so the UI shows live progress
    try:
        pending_result = await db.execute(
            select(DownloadJob).where(
                DownloadJob.user_id == user.id,
                DownloadJob.status == "pending",
            )
        )
        pending_jobs = pending_result.scalars().all()

        if pending_jobs:
            r = get_redis_client()
            already_primed = await r.exists("demo:jobs_primed")
            if not already_primed:
                for i, job in enumerate(pending_jobs):
                    if i > 0:
                        await asyncio.sleep(0.2)
                    await enqueue_job(job.id)
                await r.setex("demo:jobs_primed", 30, "1")
                logger.info("demo_jobs_primed", count=len(pending_jobs))
    except Exception as e:
        logger.warning("demo_jobs_prime_failed", error=str(e))
        # Non-fatal — demo still works

    redirect = RedirectResponse(url="/web/downloads", status_code=303)
    set_token_cookies(redirect, access_token, refresh_token, secure=settings.cookie_secure)
    rotate_csrf_token(redirect)
    return redirect


@router.post("/logout")
async def logout(request: Request):
    """Clear auth cookies and redirect to login."""
    # CSRF validation - logout should be protected against CSRF
    if not await validate_csrf_token(request):
        return _htmx_or_redirect(
            request, 403, _error_html("Invalid CSRF token"), "/web/downloads?error=csrf"
        )
    redirect = RedirectResponse(url="/web/login?logged_out=1", status_code=303)
    clear_token_cookies(redirect)
    return redirect


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


@router.get("/downloads")
async def dashboard_page(
    request: Request,
    current_user: CurrentUserFromCookie,
    db: DbSession,
):
    """Render main dashboard page with download list."""
    result = await db.execute(
        select(DownloadJob)
        .where(DownloadJob.user_id == current_user.id)
        .order_by(DownloadJob.created_at.desc())
        .limit(50)
    )
    jobs = result.scalars().all()

    token = get_csrf_token(request)
    response = templates.TemplateResponse(
        request,
        "dashboard.html",
        get_template_context(request, csrf_token=token, current_user=current_user, jobs=jobs),
    )
    set_csrf_token_cookie(response, token)
    return response


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


@router.post("/settings/password")
@limiter.limit("10/minute")
async def change_password(
    request: Request,
    current_password: Annotated[str, Form(max_length=255)],
    new_password: Annotated[str, Form(max_length=255)],
    new_password_confirm: Annotated[str, Form(max_length=255)],
    current_user: CurrentUserFromCookie,
    db: DbSession,
):
    """Change current user's password.

    On success, increments the user's token_version to invalidate all
    existing sessions. Rotates the CSRF token.
    """
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


def _cleanup_job_files(jobs: list, logger) -> tuple[bool, list[str]]:
    """Clean up files for a list of download jobs. Returns (all_cleaned, failures)."""
    file_cleanup_failures: list[str] = []
    for job in jobs:
        if not job.file_path:
            continue
        try:
            safe_path = _validate_file_path(job.file_path)
            if os.path.isfile(safe_path):
                os.remove(safe_path)
        except HTTPException:
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


@router.post("/downloads")
@limiter.limit("10/minute")
async def create_download_form(
    request: Request,
    url: Annotated[str, Form(max_length=2000)],
    current_user: CurrentUserFromCookie,
    db: DbSession,
):
    """HTMX endpoint for form submissions. Returns HTML fragment."""
    # CSRF validation
    if not await validate_csrf_token(request):
        return HTMLResponse(status_code=403, content=_error_html("Invalid CSRF token"))

    # Validate URL
    if not is_supported_url(url):
        return HTMLResponse(status_code=422, content=_error_html("Invalid supported URL"))

    # Create job with transactional outbox pattern (same as REST API)
    job_id = uuid.uuid4()

    # Pre-resolve video title so the HTMX partial renders the actual title immediately.
    # Fast (~0.5-3s) because yt-dlp runs with download=False.
    # Falls back gracefully — worker resolves title if this fails.
    title = await resolve_video_title(url)

    job = DownloadJob(id=job_id, user_id=current_user.id, url=url, status="pending", title=title)
    db.add(job)
    try:
        await write_job_to_outbox(db, job_id)
        await db.commit()
        await db.refresh(job)
    except Exception:
        await db.rollback()
        logger.exception("failed_to_create_download_job")
        return HTMLResponse(status_code=500, content=_error_html("Failed to create download"))

    # Enqueue job for processing (best-effort; outbox handles recovery)
    try:
        await enqueue_job(job_id)
        await db.execute(
            delete(Outbox).where(Outbox.job_id == job_id, Outbox.status == "pending")
        )
        await db.commit()
    except Exception:
        logger.warning("failed_to_enqueue_job_outbox_recovery", job_id=str(job_id))

    # Return HTML fragment for HTMX swap with rotated CSRF token
    resp = templates.TemplateResponse(
        request, "partials/_download_item.html", get_template_context(request, job=job)
    )
    rotate_csrf_token(resp)
    return resp


@router.post("/downloads/full")
@limiter.limit("10/minute")
async def create_download_full_page(
    request: Request,
    url: Annotated[str, Form(max_length=2000)],
    current_user: CurrentUserFromCookie,
    db: DbSession,
):
    """Full-page handler for form submissions (non-HTMX fallback)."""
    # CSRF validation
    if not await validate_csrf_token(request):
        return _htmx_or_redirect(
            request, 403, _error_html("Invalid CSRF token"), "/web/downloads?error=csrf"
        )

    # Validate URL
    if not is_supported_url(url):
        return _htmx_or_redirect(
            request, 422, _error_html("Invalid supported URL"), "/web/downloads?error=invalid_url"
        )

    # Create job with transactional outbox pattern (same as REST API)
    job_id = uuid.uuid4()
    job = DownloadJob(id=job_id, user_id=current_user.id, url=url, status="pending")
    db.add(job)
    try:
        await write_job_to_outbox(db, job_id)
        await db.commit()
        await db.refresh(job)
    except Exception:
        await db.rollback()
        logger.exception("failed_to_create_download_job_full_page")
        return _htmx_or_redirect(
            request,
            500,
            _error_html("Failed to create download"),
            "/web/downloads?error=creation_failed",
        )

    # Enqueue job for processing (best-effort; outbox handles recovery)
    try:
        await enqueue_job(job_id)
        await db.execute(
            delete(Outbox).where(Outbox.job_id == job_id, Outbox.status == "pending")
        )
        await db.commit()
    except Exception:
        logger.warning("failed_to_enqueue_job_outbox_recovery", job_id=str(job_id))

    # Redirect to dashboard for full-page non-HTMX fallback
    return RedirectResponse(url="/web/downloads", status_code=303)


@router.delete("/downloads/{job_id}")
@limiter.limit("30/minute")
async def delete_download_form(
    request: Request,
    job_id: str,
    current_user: CurrentUserFromCookie,
    db: DbSession,
):
    """HTMX endpoint for deleting a download."""
    # CSRF validation
    if not await validate_csrf_token(request):
        return HTMLResponse(status_code=403, content=_error_html("Invalid CSRF token"))

    # Validate job_id is a valid UUID
    try:
        job_uuid = uuid.UUID(job_id)
    except ValueError:
        return HTMLResponse(status_code=400, content=_error_html("Invalid job ID"))

    result = await db.execute(
        select(DownloadJob).where(
            DownloadJob.id == job_uuid,
            DownloadJob.user_id == current_user.id,
        )
    )
    job = result.scalar_one_or_none()

    if job is None:
        return HTMLResponse(status_code=404, content="")

    # Only allow deletion of terminal jobs (completed, failed, cancelled)
    # Reject deletes for jobs that are still processing to avoid races with worker
    if job.status not in ("completed", "failed", "cancelled"):
        return HTMLResponse(
            status_code=409,
            content=_error_html(
                f"Cannot delete job with status '{job.status}'. Only completed, failed, or cancelled jobs can be deleted."
            ),
        )

    # Delete file from disk before removing DB record
    if job.file_path:
        try:
            safe_path = _validate_file_path(job.file_path)
            if os.path.isfile(safe_path):
                os.remove(safe_path)
                logger.info("file_deleted", file_path=safe_path)
        except HTTPException:
            raise
        except OSError as e:
            logger.warning("failed_to_delete_file", file_path=job.file_path, error=str(e))

    await db.delete(job)
    await db.commit()

    # Return empty response for hx-swap="outerHTML" (removes element) with rotated CSRF token
    resp = HTMLResponse(content="")
    rotate_csrf_token(resp)
    return resp


@router.get("/downloads/{job_id}/file")
async def download_file(
    request: Request,
    job_id: str,
    current_user: CurrentUserFromCookie,
    db: DbSession,
):
    """Download the file for a completed job using cookie authentication.

    This endpoint mirrors the API endpoint /api/v1/downloads/{job_id}/file
    but uses cookie-based authentication instead of bearer tokens.
    """
    from fastapi.responses import FileResponse

    # Validate job_id is a valid UUID
    try:
        job_uuid = uuid.UUID(job_id)
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid job ID format",
        ) from None

    result = await db.execute(
        select(DownloadJob).where(
            DownloadJob.id == job_uuid,
            DownloadJob.user_id == current_user.id,
        )
    )
    job = result.scalar_one_or_none()
    if job is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Download job not found",
        )

    if job.status != "completed":
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Job is not completed. Current status: {job.status}",
        )

    if not job.file_path:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="File not found",
        )

    # Check if download has expired
    if job.expires_at:
        # Normalize both timestamps to UTC for comparison
        now_utc = datetime.now(UTC)
        expires_at = job.expires_at
        # Ensure expires_at is timezone-aware (convert naive to UTC)
        if expires_at.tzinfo is None:
            expires_at = expires_at.replace(tzinfo=UTC)
        else:
            expires_at = expires_at.astimezone(UTC)
        if expires_at < now_utc:
            raise HTTPException(
                status_code=status.HTTP_410_GONE,
                detail="Download link has expired",
            )

    # Validate path is within storage directory (prevents path traversal)
    safe_path = _validate_file_path(job.file_path)

    # Check file exists on disk
    if not os.path.isfile(safe_path):
        safe_job_id = str(job_id).replace("\r", "").replace("\n", "")
        logger.error("file_missing_from_disk", job_id=safe_job_id, file_path=safe_path)
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="File not found on disk",
        )

    return FileResponse(
        path=safe_path,
        filename=job.file_name,
        media_type="application/octet-stream",
    )

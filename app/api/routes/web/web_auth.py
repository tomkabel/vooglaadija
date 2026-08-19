"""Auth-related web routes."""

from datetime import UTC, datetime
from typing import Annotated

from fastapi import APIRouter, Form, Query, Request, Response
from fastapi.responses import RedirectResponse
from sqlalchemy import select

from app.api.dependencies import CurrentUserFromCookie, DbSession
from app.api.rate_limit_config import limiter
from app.api.routes.web.web_helpers import (
    _change_password_response,
    _demo_user_or_raise,
    _htmx_or_redirect,
    _login_success_response,
    _prime_demo_jobs,
    _register_success_response,
    _register_user_or_error_response,
    _resolve_login_errors,
    _resolve_register_errors,
    _validate_redirect_url,
    get_csrf_token,
    get_template_context,
    logger,
    rotate_csrf_token,
    set_csrf_token_cookie,
    templates,
    validate_csrf_token,
)
from app.api.routes.web_helpers import _error_html
from app.auth import (
    clear_token_cookies,
    create_access_token,
    create_refresh_token,
    set_token_cookies,
    verify_token,
)
from app.services.auth_service import verify_password
from core.config import settings
from core.models.user import User, not_deleted

router = APIRouter(tags=["web"])
DEMO_EMAIL = "demo@vooglaadija.io"


async def _blacklist_token_cookie(token_str: str | None) -> None:
    """Blacklist a cookie token for the remainder of its lifetime."""
    if not token_str:
        return

    payload = verify_token(token_str)
    if not payload:
        return

    jti = payload.get("jti")
    if not jti:
        return

    from app.services.token_blacklist import blacklist_token

    remaining = max(int(payload.get("exp", 0)) - int(datetime.now(UTC).timestamp()), 60)
    await blacklist_token(jti, ttl_seconds=remaining)


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
    *,
    db: DbSession,
    email: Annotated[str, Form(max_length=255)],
    password: Annotated[str, Form(max_length=255)],
    return_url: Annotated[str | None, Form(max_length=500)] = None,
):
    """Handle login form submission via HTMX or regular POST."""
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
            request, csrf_token=token, error=error_message, field_errors=field_errors
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
    user, error_response = await _register_user_or_error_response(
        request, email, password, password_confirm, db
    )
    if error_response is not None:
        return error_response
    assert user is not None
    access_token = create_access_token(user.id, token_version=user.token_version)
    refresh_token = create_refresh_token(user.id, token_version=user.token_version)
    return _register_success_response(request, access_token, refresh_token)


@router.post("/demo-login")
@limiter.limit("3/minute")
async def demo_login(request: Request, db: DbSession):
    """Authenticate as the pre-seeded demo user and redirect to downloads."""
    if not await validate_csrf_token(request):
        return _htmx_or_redirect(
            request, 403, _error_html("Invalid CSRF token"), "/web/login?error=csrf"
        )
    user = await _demo_user_or_raise(db, DEMO_EMAIL)
    access_token = create_access_token(user.id, email=user.email, token_version=user.token_version)
    refresh_token = create_refresh_token(user.id, token_version=user.token_version)
    try:
        await _prime_demo_jobs(user.id, db)
    except Exception as e:
        logger.warning("demo_jobs_prime_failed", error=str(e))
    redirect = RedirectResponse(url="/web/downloads", status_code=303)
    set_token_cookies(redirect, access_token, refresh_token, secure=settings.cookie_secure)
    rotate_csrf_token(redirect)
    return redirect


@router.post("/logout")
async def logout(request: Request):
    """Clear auth cookies and redirect to login."""
    if not await validate_csrf_token(request):
        return _htmx_or_redirect(
            request, 403, _error_html("Invalid CSRF token"), "/web/downloads?error=csrf"
        )
    await _blacklist_token_cookie(request.cookies.get("access_token"))
    await _blacklist_token_cookie(request.cookies.get("refresh_token"))
    redirect = RedirectResponse(url="/web/login?logged_out=1", status_code=303)
    clear_token_cookies(redirect)
    return redirect


@router.post("/settings/password")
@limiter.limit("10/minute")
async def change_password(
    request: Request,
    *,
    current_password: Annotated[str, Form(max_length=255)],
    new_password: Annotated[str, Form(max_length=255)],
    new_password_confirm: Annotated[str, Form(max_length=255)],
    current_user: CurrentUserFromCookie,
    db: DbSession,
):
    """Change current user's password and rotate CSRF after success."""
    return await _change_password_response(
        request=request,
        current_password=current_password,
        new_password=new_password,
        new_password_confirm=new_password_confirm,
        current_user=current_user,
        db=db,
    )

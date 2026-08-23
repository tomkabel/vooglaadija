"""Auth-related web routes."""

from datetime import UTC, datetime
from typing import Annotated

from fastapi import APIRouter, Depends, Form, Query, Request, Response
from fastapi.responses import HTMLResponse, RedirectResponse
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
) -> HTMLResponse:
    """Render the login page with CSRF data, the requested return URL, and resolved error messages.

    Parameters:
        return_url (str): URL to return to after successful login.
        error (str | None): Error identifier used to populate the page's error messages.

    Returns:
        HTMLResponse: The rendered login page response.
    """
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


class LoginForm:
    """Bound login form fields with validation (capped return_url)."""

    def __init__(self, email: str, password: str, return_url: str | None) -> None:
        """Store the validated login form fields.

        Parameters:
            email (str): Submitted email address.
            password (str): Submitted plaintext password.
            return_url (str | None): Optional post-login redirect target.
        """
        self.email = email
        self.password = password
        self.return_url = return_url


async def parse_login_form(
    email: Annotated[str, Form(max_length=255)],
    password: Annotated[str, Form(max_length=255)],
    return_url: Annotated[str | None, Form(max_length=500)] = None,
) -> LoginForm:
    """Dependency that validates the login form fields (incl. return_url)."""
    return LoginForm(email=email, password=password, return_url=return_url)


@router.post("/login", response_model=None)
@limiter.limit("5/minute")
async def login_form(
    request: Request,
    response: Response,
    *,
    db: DbSession,
    form: Annotated[LoginForm, Depends(parse_login_form)],
) -> HTMLResponse | RedirectResponse:
    """Handle login form submission and authenticate the user.

    Parameters:
        form (LoginForm): Validated login form fields, including the optional
            return URL to redirect to after a successful login.

    Returns:
        HTMLResponse | RedirectResponse: An HTMX response or redirect indicating the login result.
    """
    if not await validate_csrf_token(request):
        return _htmx_or_redirect(
            request,
            403,
            _error_html("Invalid CSRF token"),
            "/web/login?error=csrf",
        )
    result = await db.execute(select(User).where(User.email == form.email, not_deleted()))
    user = result.scalar_one_or_none()
    if user is None or not await verify_password(form.password, user.password_hash):
        return _htmx_or_redirect(
            request,
            401,
            _error_html("Invalid email or password"),
            "/web/login?error=1",
        )
    if not user.is_active:
        return _htmx_or_redirect(
            request,
            401,
            _error_html("Account is inactive"),
            "/web/login?error=inactive",
        )
    access_token = create_access_token(user.id, token_version=user.token_version)
    refresh_token = create_refresh_token(user.id, token_version=user.token_version)
    safe_redirect = _validate_redirect_url(form.return_url, "/web/downloads")
    return _login_success_response(request, access_token, refresh_token, safe_redirect, response)


@router.get("/register")
async def register_page(
    request: Request,
    error: Annotated[str | None, Query(max_length=100)] = None,
) -> HTMLResponse:
    """Render the user registration page with CSRF data and resolved validation errors.

    Parameters:
        error (str | None): Optional registration error code used to populate the page's error messages.

    Returns:
        HTMLResponse: The rendered registration page.
    """
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


@router.post("/register", response_model=None)
@limiter.limit("5/minute")
async def register_form(
    request: Request,
    email: Annotated[str, Form(max_length=255)],
    password: Annotated[str, Form(max_length=255)],
    password_confirm: Annotated[str, Form(max_length=255)],
    db: DbSession,
) -> HTMLResponse | RedirectResponse:
    """
    Process a registration form submission and authenticate successfully registered users.

    Parameters:
        password_confirm (str): Confirmation of the submitted password.

    Returns:
        HTMLResponse | RedirectResponse: An error response for invalid registration data, or a response containing authentication cookies for a successful registration.
    """
    user, error_response = await _register_user_or_error_response(
        request,
        email,
        password,
        password_confirm,
        db,
    )
    if error_response is not None:
        return error_response
    assert user is not None  # noqa: S101
    access_token = create_access_token(user.id, token_version=user.token_version)
    refresh_token = create_refresh_token(user.id, token_version=user.token_version)
    return _register_success_response(request, access_token, refresh_token)


@router.post("/demo-login", response_model=None)
@limiter.limit("3/minute")
async def demo_login(request: Request, db: DbSession) -> HTMLResponse | RedirectResponse:
    """Authenticate as the pre-seeded demo user and redirect to downloads."""
    if not await validate_csrf_token(request):
        return _htmx_or_redirect(
            request,
            403,
            _error_html("Invalid CSRF token"),
            "/web/login?error=csrf",
        )
    user = await _demo_user_or_raise(db, DEMO_EMAIL)
    access_token = create_access_token(user.id, token_version=user.token_version)
    refresh_token = create_refresh_token(user.id, token_version=user.token_version)
    try:
        await _prime_demo_jobs(user.id, db)
    except Exception as e:
        logger.warning("demo_jobs_prime_failed", error=str(e))
    redirect = RedirectResponse(url="/web/downloads", status_code=303)
    set_token_cookies(redirect, access_token, refresh_token)
    rotate_csrf_token(redirect)
    return redirect


@router.post("/logout", response_model=None)
async def logout(request: Request) -> HTMLResponse | RedirectResponse:
    """
    Log out the current user and redirect to the login page.

    Returns:
        HTMLResponse | RedirectResponse: An error response for an invalid CSRF token;
        otherwise, a redirect to the login page with authentication cookies cleared.
    """
    if not await validate_csrf_token(request):
        return _htmx_or_redirect(
            request,
            403,
            _error_html("Invalid CSRF token"),
            "/web/downloads?error=csrf",
        )
    await _blacklist_token_cookie(request.cookies.get("__Host-access_token"))
    await _blacklist_token_cookie(request.cookies.get("__Host-refresh_token"))
    redirect = RedirectResponse(url="/web/login?logged_out=1", status_code=303)
    clear_token_cookies(redirect)
    return redirect


@router.post("/settings/password", response_model=None)
@limiter.limit("10/minute")
async def change_password(
    request: Request,
    *,
    current_password: Annotated[str, Form(max_length=255)],
    new_password: Annotated[str, Form(max_length=255)],
    new_password_confirm: Annotated[str, Form(max_length=255)],
    current_user: CurrentUserFromCookie,
    db: DbSession,
) -> HTMLResponse | RedirectResponse:
    """Change current user's password and rotate CSRF after success."""
    return await _change_password_response(
        request,
        current_password=current_password,
        new_password=new_password,
        new_password_confirm=new_password_confirm,
        current_user=current_user,
        db=db,
    )

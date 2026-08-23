"""Settings and account-management web routes."""

from typing import Annotated

from fastapi import APIRouter, Form, Query, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from app.api.dependencies import CurrentUserFromCookie, DbSession
from app.api.rate_limit_config import limiter
from app.api.routes.web.web_helpers import (
    _htmx_or_redirect,
    _resolve_settings_errors,
    get_csrf_token,
    get_template_context,
    is_htmx_request,
    set_csrf_token_cookie,
    templates,
    validate_csrf_token,
)
from app.api.routes.web_helpers import _error_html, _success_html
from app.auth import clear_token_cookies
from app.services.user_service import (
    AccountFileCleanupError,
    DeleteConfirmationError,
    InvalidCurrentPasswordError,
    InvalidUsernameError,
    UserService,
)
from app.utils.username import default_username_from_email as _default_username_from_email
from core.logging_config import get_logger

logger = get_logger(__name__)
router = APIRouter(tags=["web"])


@router.get("/settings")
async def settings_page(
    request: Request,
    current_user: CurrentUserFromCookie,
    error: Annotated[str | None, Query(max_length=100)] = None,
) -> HTMLResponse:
    """
    Render the settings page for the current user.

    Parameters:
        error (str | None): Optional error code used to display validation feedback.

    Returns:
        HTMLResponse: The rendered settings page.
    """
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


@router.post("/settings/username", response_model=None)
@limiter.limit("10/minute")
async def update_username(
    request: Request,
    username: Annotated[str, Form(max_length=64)],
    current_user: CurrentUserFromCookie,
    db: DbSession,
) -> HTMLResponse | RedirectResponse:
    """
    Update the current user's username.

    Parameters:
        username (str): The new username.

    Returns:
        HTMLResponse | RedirectResponse: A success response, or an error response for an invalid CSRF token or username.
    """
    if not await validate_csrf_token(request):
        return _htmx_or_redirect(
            request,
            403,
            _error_html("Invalid CSRF token"),
            "/web/settings?error=csrf",
        )

    try:
        await UserService(db=db, user=current_user).update_username(username)
    except InvalidUsernameError:
        return _htmx_or_redirect(
            request,
            400,
            _error_html("Username must be at least 3 characters"),
            "/web/settings?error=username_too_short",
        )

    return _htmx_or_redirect(
        request,
        200,
        _success_html("Username updated successfully"),
        "/web/settings?updated=username",
    )


@router.post("/settings/delete-account", response_model=None)
@limiter.limit("3/minute")
async def delete_account(
    request: Request,
    password: Annotated[str, Form(max_length=255)],
    confirm_text: Annotated[str, Form(max_length=16)],
    current_user: CurrentUserFromCookie,
    db: DbSession,
) -> HTMLResponse | RedirectResponse:
    """
    Delete the current user's account and associated downloads after validating the deletion request.

    Parameters:
        confirm_text (str): Confirmation text required to authorize account deletion.

    Returns:
        HTMLResponse | RedirectResponse: An empty HTMX response with a login redirect header, or a redirect to the login page.
    """
    if not await validate_csrf_token(request):
        return _htmx_or_redirect(
            request,
            403,
            _error_html("Invalid CSRF token"),
            "/web/settings?error=csrf",
        )

    try:
        await UserService(db=db, user=current_user).delete_account(
            password=password,
            confirm_text=confirm_text,
        )
    except DeleteConfirmationError:
        return _htmx_or_redirect(
            request,
            400,
            _error_html("Please type DELETE to confirm account deletion"),
            "/web/settings?error=delete_confirmation",
        )
    except InvalidCurrentPasswordError:
        return _htmx_or_redirect(
            request,
            400,
            _error_html("Password is incorrect"),
            "/web/settings?error=bad_password",
        )
    except AccountFileCleanupError:
        return _htmx_or_redirect(
            request,
            500,
            _error_html(
                "Could not remove all downloaded files. Your account was not deleted. "
                "Please try again or contact support.",
            ),
            "/web/settings?error=file_cleanup",
        )

    if is_htmx_request(request):
        resp = HTMLResponse(status_code=200, content="")
        resp.headers["HX-Redirect"] = "/web/login?account_deleted=1"
        clear_token_cookies(resp)
        return resp

    redirect = RedirectResponse(url="/web/login?account_deleted=1", status_code=303)
    clear_token_cookies(redirect)
    return redirect

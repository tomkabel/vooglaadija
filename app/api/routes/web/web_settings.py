"""Settings and account-management web routes."""

import os
from typing import Annotated

from fastapi import APIRouter, Form, Query, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import select

from app.api.dependencies import CurrentUserFromCookie, DbSession
from app.api.rate_limit_config import limiter
from app.api.routes.web.web_helpers import (
    _downloads_base_path,
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
from app.services.auth_service import verify_password
from app.utils.username import default_username_from_email as _default_username_from_email
from core.config import settings
from core.logging_config import get_logger
from core.models.download_job import DownloadJob
from core.utils.security import validate_path

logger = get_logger(__name__)
router = APIRouter(tags=["web"])


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
            safe_path = validate_path(_downloads_base_path(settings), job.file_path)
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

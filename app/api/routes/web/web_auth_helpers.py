"""Auth route support helpers re-exported by web_helpers."""

import asyncio
import uuid
from typing import cast

from fastapi import HTTPException, Request, status
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import select

from app.api.dependencies import CurrentUserFromCookie, DbSession
from app.api.routes.web.web_helpers import (
    _error_response,
    _htmx_or_redirect,
    _resolve_register_errors,
    _resolve_settings_errors,
    logger,
    rotate_csrf_token,
    validate_csrf_token,
)
from app.api.routes.web_helpers import _success_html
from app.services.user_service import (
    DuplicateEmailError,
    InvalidCurrentPasswordError,
    InvalidPasswordError,
    PasswordMismatchError,
    UserService,
)
from core.models.download_job import DownloadJob
from core.models.user import User, not_deleted
from core.queue import enqueue_job
from core.redis_client import get_redis_client


async def _prime_demo_jobs(user_id: uuid.UUID, db: DbSession) -> None:
    """Prime pending demo jobs for processing."""
    pending_result = await db.execute(
        select(DownloadJob).where(DownloadJob.user_id == user_id, DownloadJob.status == "pending")
    )
    pending_jobs = pending_result.scalars().all()
    if not pending_jobs:
        return
    r = get_redis_client()
    if await r.exists("demo:jobs_primed"):
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
    return cast(User, user)


async def _change_password_response(
    request: Request,
    current_password: str,
    new_password: str,
    new_password_confirm: str,
    current_user: CurrentUserFromCookie,
    db: DbSession,
) -> HTMLResponse | RedirectResponse:
    error: tuple[int, str, str] | None = None
    if not await validate_csrf_token(request):
        error = (403, "Invalid CSRF token", "csrf")
    if error is not None:
        status_code, error_message, error_code = error
        return _error_response(
            request, status_code, error_message, f"/web/settings?error={error_code}"
        )

    try:
        await UserService(db=db, user=current_user).change_password(
            current_password,
            new_password,
            new_password_confirm,
        )
    except InvalidCurrentPasswordError:
        return _error_response(
            request,
            400,
            "Current password is incorrect",
            "/web/settings?error=bad_current_password",
        )
    except PasswordMismatchError:
        return _error_response(
            request,
            400,
            "New passwords do not match",
            "/web/settings?error=password_mismatch",
        )
    except InvalidPasswordError as exc:
        resolved_message, _ = _resolve_settings_errors(exc.code)
        return _error_response(
            request,
            400,
            resolved_message or "Password does not meet the project requirements",
            f"/web/settings?error={exc.code}",
        )

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
    error: tuple[int, str, str] | None = None
    if not await validate_csrf_token(request):
        error = (403, "Invalid CSRF token", "csrf")
    elif password != password_confirm:
        error = (400, "Passwords do not match", "password_mismatch")
    if error is not None:
        status_code, error_message, error_code = error
        return None, _error_response(
            request, status_code, error_message, f"/web/register?error={error_code}"
        )

    try:
        user = await UserService(db=db).register(email, password)
    except DuplicateEmailError:
        return None, _error_response(
            request, 409, "Email already registered", "/web/register?error=email_exists"
        )
    except InvalidPasswordError as exc:
        resolved_message, _ = _resolve_register_errors(exc.code)
        return None, _error_response(
            request,
            400,
            resolved_message or "Password does not meet the project requirements",
            f"/web/register?error={exc.code}",
        )
    return user, None

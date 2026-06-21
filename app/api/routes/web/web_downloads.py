"""Download-related web routes."""

import os
import uuid
from datetime import UTC, datetime
from typing import Annotated

from fastapi import APIRouter, Form, HTTPException, Request, status
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse
from sqlalchemy import delete, select

from app.api.dependencies import CurrentUserFromCookie, DbSession
from app.api.rate_limit_config import limiter
from app.api.routes.web.web_helpers import (
    _downloads_base_path,
    _htmx_or_redirect,
    get_csrf_token,
    get_template_context,
    logger,
    rotate_csrf_token,
    set_csrf_token_cookie,
    templates,
    validate_csrf_token,
)
from app.api.routes.web_helpers import _error_html
from app.services.outbox_service import write_job_to_outbox
from app.services.yt_dlp_service import resolve_video_title
from app.utils.validators import is_supported_url
from core.models.download_job import DownloadJob
from core.models.outbox import Outbox
from core.queue import enqueue_job
from core.utils.security import validate_path

router = APIRouter(tags=["web"])


async def _create_pending_download_job(
    db: DbSession,
    current_user: CurrentUserFromCookie,
    url: str,
    failure_log: str,
    title: str | None = None,
) -> DownloadJob | None:
    job_id = uuid.uuid4()
    job = DownloadJob(id=job_id, user_id=current_user.id, url=url, status="pending", title=title)
    db.add(job)
    try:
        await write_job_to_outbox(db, job_id)
        await db.commit()
        await db.refresh(job)
    except Exception:
        await db.rollback()
        logger.exception(failure_log)
        return None
    return job


async def _best_effort_enqueue(db: DbSession, job_id: uuid.UUID) -> None:
    try:
        await enqueue_job(job_id)
        await db.execute(delete(Outbox).where(Outbox.job_id == job_id, Outbox.status == "pending"))
        await db.commit()
    except Exception:
        logger.warning("failed_to_enqueue_job_outbox_recovery", job_id=str(job_id))


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


@router.post("/downloads")
@limiter.limit("10/minute")
async def create_download_form(
    request: Request,
    url: Annotated[str, Form(max_length=2000)],
    current_user: CurrentUserFromCookie,
    db: DbSession,
):
    """HTMX endpoint for form submissions. Returns HTML fragment."""
    if not await validate_csrf_token(request):
        return HTMLResponse(status_code=403, content=_error_html("Invalid CSRF token"))

    if not is_supported_url(url):
        return HTMLResponse(status_code=422, content=_error_html("Invalid supported URL"))

    title = await resolve_video_title(url)
    job = await _create_pending_download_job(
        db, current_user, url, "failed_to_create_download_job", title=title
    )
    if job is None:
        return HTMLResponse(status_code=500, content=_error_html("Failed to create download"))

    await _best_effort_enqueue(db, job.id)

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
    if not await validate_csrf_token(request):
        return _htmx_or_redirect(
            request, 403, _error_html("Invalid CSRF token"), "/web/downloads?error=csrf"
        )

    if not is_supported_url(url):
        return _htmx_or_redirect(
            request, 422, _error_html("Invalid supported URL"), "/web/downloads?error=invalid_url"
        )

    job = await _create_pending_download_job(
        db, current_user, url, "failed_to_create_download_job_full_page"
    )
    if job is None:
        return _htmx_or_redirect(
            request,
            500,
            _error_html("Failed to create download"),
            "/web/downloads?error=creation_failed",
        )

    await _best_effort_enqueue(db, job.id)

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
    if not await validate_csrf_token(request):
        return HTMLResponse(status_code=403, content=_error_html("Invalid CSRF token"))

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

    if job.status not in ("completed", "failed", "cancelled"):
        return HTMLResponse(
            status_code=409,
            content=_error_html(
                f"Cannot delete job with status '{job.status}'. Only completed, failed, or "
                "cancelled jobs can be deleted."
            ),
        )

    if job.file_path:
        try:
            safe_path = validate_path(_downloads_base_path(), job.file_path)
            if os.path.isfile(safe_path):
                os.remove(safe_path)
                logger.info("file_deleted", file_path=safe_path)
        except (ValueError, PermissionError) as e:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Access denied: invalid file path",
            ) from e
        except OSError as e:
            logger.warning("failed_to_delete_file", file_path=job.file_path, error=str(e))

    await db.delete(job)
    await db.commit()

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
    """Download the file for a completed job using cookie authentication."""
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

    if job.expires_at:
        expires_at = job.expires_at
        if expires_at.tzinfo is None:
            expires_at = expires_at.replace(tzinfo=UTC)
        else:
            expires_at = expires_at.astimezone(UTC)
        if expires_at < datetime.now(UTC):
            raise HTTPException(
                status_code=status.HTTP_410_GONE,
                detail="Download link has expired",
            )

    try:
        safe_path = validate_path(_downloads_base_path(), job.file_path)
    except (ValueError, PermissionError) as e:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Access denied: invalid file path",
        ) from e

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

"""Download-related web routes."""

import re
from typing import Annotated

from fastapi import APIRouter, Form, HTTPException, Request, status
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse
from starlette.templating import _TemplateResponse as TemplateResponse

from app.api.dependencies import CurrentUserFromCookie, DbSession
from app.api.rate_limit_config import limiter
from app.api.routes.web.web_helpers import (
    _htmx_or_redirect,
    get_csrf_token,
    get_template_context,
    logger,
    set_csrf_token_cookie,
    templates,
    validate_csrf_token,
)
from app.api.routes.web_helpers import _error_html
from app.services.download_service import (
    DownloadFileExpiredError,
    DownloadFileMissingError,
    DownloadNotFoundError,
    DownloadService,
    InvalidDownloadIdError,
    InvalidDownloadStatusError,
    UnsafeDownloadPathError,
)
from app.utils.validators import is_supported_url

router = APIRouter(tags=["web"])

_SAFE_STATUS_PATTERN = re.compile(r"[^a-z0-9_-]+", re.IGNORECASE)


def _safe_status_label(status: str | None) -> str:
    normalized = _SAFE_STATUS_PATTERN.sub(" ", str(status or "").strip().lower()).strip()
    return normalized or "unknown"


def _delete_status_conflict_message(status: str | None) -> str:
    safe_status = _safe_status_label(status)
    return (
        f"Cannot delete job with status '{safe_status}'. "
        "Only completed, failed, or cancelled jobs can be deleted."
    )


def _download_file_status_message(status: str | None) -> str:
    safe_status = _safe_status_label(status)
    return f"Job is not completed. Current status: {safe_status}"


def _download_file_missing_message(exc: DownloadFileMissingError) -> str:
    if exc.code == "missing_on_disk":
        return "File not found on disk"
    return "File not found"


@router.get("/downloads")
async def dashboard_page(
    request: Request,
    current_user: CurrentUserFromCookie,
    db: DbSession,
) -> TemplateResponse:
    """Render main dashboard page with download list."""
    result = await DownloadService(db, current_user.id).list(page=1, per_page=50)
    token = get_csrf_token(request)
    response = templates.TemplateResponse(
        request,
        "dashboard.html",
        get_template_context(
            request,
            csrf_token=token,
            current_user=current_user,
            jobs=result.jobs,
        ),
    )
    set_csrf_token_cookie(response, token)
    return response


@router.post("/downloads", response_model=None)
@limiter.limit("10/minute")
async def create_download_form(
    request: Request,
    url: Annotated[str, Form(max_length=2000)],
    current_user: CurrentUserFromCookie,
    db: DbSession,
) -> HTMLResponse | TemplateResponse:
    """HTMX endpoint for form submissions. Returns HTML fragment."""
    if not await validate_csrf_token(request):
        return HTMLResponse(status_code=403, content=_error_html("Invalid CSRF token"))

    if not is_supported_url(url):
        return HTMLResponse(status_code=422, content=_error_html("Invalid supported URL"))

    service = DownloadService(db, current_user.id)
    try:
        job = await service.create(url, resolve_title=True)
    except Exception:
        logger.exception("failed_to_create_download_job")
        return HTMLResponse(status_code=500, content=_error_html("Failed to create download"))

    await service.best_effort_enqueue(job.id)

    resp = templates.TemplateResponse(
        request, "partials/_download_item.html", get_template_context(request, job=job)
    )
    return resp


@router.post("/downloads/full", response_model=None)
@limiter.limit("10/minute")
async def create_download_full_page(
    request: Request,
    url: Annotated[str, Form(max_length=2000)],
    current_user: CurrentUserFromCookie,
    db: DbSession,
) -> HTMLResponse | RedirectResponse:
    """Full-page handler for form submissions (non-HTMX fallback)."""
    if not await validate_csrf_token(request):
        return _htmx_or_redirect(
            request, 403, _error_html("Invalid CSRF token"), "/web/downloads?error=csrf"
        )

    if not is_supported_url(url):
        return _htmx_or_redirect(
            request, 422, _error_html("Invalid supported URL"), "/web/downloads?error=invalid_url"
        )

    service = DownloadService(db, current_user.id)
    try:
        job = await service.create(url, resolve_title=False)
    except Exception:
        logger.exception("failed_to_create_download_job_full_page")
        return _htmx_or_redirect(
            request,
            500,
            _error_html("Failed to create download"),
            "/web/downloads?error=creation_failed",
        )

    await service.best_effort_enqueue(job.id)

    return RedirectResponse(url="/web/downloads", status_code=303)


@router.delete("/downloads/{job_id}")
@limiter.limit("30/minute")
async def delete_download_form(
    request: Request,
    job_id: str,
    current_user: CurrentUserFromCookie,
    db: DbSession,
) -> HTMLResponse:
    """HTMX endpoint for deleting a download."""
    if not await validate_csrf_token(request):
        return HTMLResponse(status_code=403, content=_error_html("Invalid CSRF token"))

    try:
        await DownloadService(db, current_user.id).delete(
            job_id,
            allowed_statuses={"completed", "failed", "cancelled"},
            fail_on_file_delete=False,
        )
    except InvalidDownloadIdError:
        return HTMLResponse(status_code=400, content=_error_html("Invalid job ID"))
    except DownloadNotFoundError:
        return HTMLResponse(status_code=404, content="")
    except InvalidDownloadStatusError as exc:
        return HTMLResponse(
            status_code=409,
            content=_error_html(_delete_status_conflict_message(exc.status)),
        )
    except UnsafeDownloadPathError:
        return HTMLResponse(
            status_code=status.HTTP_403_FORBIDDEN,
            content=_error_html("Access denied: invalid file path"),
        )

    resp = HTMLResponse(content="")
    return resp


@router.get("/downloads/{job_id}/file")
async def download_file(
    request: Request,
    job_id: str,
    current_user: CurrentUserFromCookie,
    db: DbSession,
) -> FileResponse:
    """Download the file for a completed job using cookie authentication."""
    try:
        file_result = await DownloadService(db, current_user.id).get_file_path(job_id)
    except InvalidDownloadIdError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid job ID format",
        ) from exc
    except DownloadNotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Download job not found",
        ) from exc
    except InvalidDownloadStatusError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=_download_file_status_message(exc.status),
        ) from exc
    except DownloadFileExpiredError as exc:
        raise HTTPException(
            status_code=status.HTTP_410_GONE,
            detail="Download link has expired",
        ) from exc
    except DownloadFileMissingError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=_download_file_missing_message(exc),
        ) from exc
    except UnsafeDownloadPathError as exc:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Access denied: invalid file path",
        ) from exc

    return FileResponse(
        path=file_result.path,
        filename=file_result.filename,
        media_type="application/octet-stream",
    )

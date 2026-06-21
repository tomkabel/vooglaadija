"""Download-related web routes."""

from typing import Annotated

from fastapi import APIRouter, Form, HTTPException, Request, status
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse

from app.api.dependencies import CurrentUserFromCookie, DbSession
from app.api.rate_limit_config import limiter
from app.api.routes.web.web_helpers import (
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


@router.get("/downloads")
async def dashboard_page(
    request: Request,
    current_user: CurrentUserFromCookie,
    db: DbSession,
):
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
):
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
        return HTMLResponse(status_code=409, content=_error_html(str(exc)))
    except UnsafeDownloadPathError as exc:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=str(exc),
        ) from exc

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
        file_result = await DownloadService(db, current_user.id).get_file_path(job_id)
    except InvalidDownloadIdError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(exc),
        ) from exc
    except DownloadNotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(exc),
        ) from exc
    except InvalidDownloadStatusError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(exc),
        ) from exc
    except DownloadFileExpiredError as exc:
        raise HTTPException(
            status_code=status.HTTP_410_GONE,
            detail=str(exc),
        ) from exc
    except DownloadFileMissingError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(exc),
        ) from exc
    except UnsafeDownloadPathError as exc:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=str(exc),
        ) from exc

    return FileResponse(
        path=file_result.path,
        filename=file_result.filename,
        media_type="application/octet-stream",
    )

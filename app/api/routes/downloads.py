"""Download job CRUD endpoints with DLQ replay capabilities."""

from fastapi import APIRouter, HTTPException, Query, Request, status
from fastapi.responses import FileResponse

from app.api.dependencies import CurrentUser, DbSession
from app.api.rate_limit_config import limiter
from app.schemas.download import (
    DownloadCreate,
    DownloadListResponse,
    DownloadResponse,
    FailedJobListResponse,
    FailedJobResponse,
    PaginationInfo,
)
from app.schemas.error import (
    ErrorCode,
    build_error_example,
    error_response_doc,
    success_response_doc,
)
from app.services.download_service import (
    DownloadFileDeleteFailedError,
    DownloadFileExpiredError,
    DownloadFileMissingError,
    DownloadNotFoundError,
    DownloadService,
    FailedJobNotFoundError,
    InvalidDownloadIdError,
    InvalidDownloadStatusError,
    UnsafeDownloadPathError,
)
from core.models.download_job import DownloadJob

router = APIRouter(prefix="/downloads", tags=["downloads"])


def _job_to_response(job: DownloadJob) -> DownloadResponse:
    """Convert a DownloadJob ORM model to a DownloadResponse schema."""
    return DownloadResponse.model_validate(job)


def _map_download_service_error(exc: Exception) -> HTTPException:
    """Map download domain errors to REST HTTP exceptions."""
    if isinstance(exc, InvalidDownloadIdError):
        return HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc))
    if isinstance(exc, DownloadNotFoundError | FailedJobNotFoundError):
        return HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc))
    if isinstance(exc, InvalidDownloadStatusError):
        return HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc))
    if isinstance(exc, UnsafeDownloadPathError):
        return HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(exc))
    if isinstance(exc, DownloadFileExpiredError):
        return HTTPException(status_code=status.HTTP_410_GONE, detail=str(exc))
    if isinstance(exc, DownloadFileMissingError):
        return HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc))
    if isinstance(exc, DownloadFileDeleteFailedError):
        return HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=str(exc))
    return HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Download error")


@router.post(
    "",
    response_model=DownloadResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Create download job",
    description="Queue a new download job for the authenticated user.",
    responses={
        201: success_response_doc(
            "Download job created",
            {
                "id": "550e8400-e29b-41d4-a716-446655440000",
                "url": "https://www.youtube.com/watch?v=dQw4w9WgXcQ",
                "status": "pending",
                "file_name": None,
                "error": None,
                "created_at": "2026-04-07T12:00:00Z",
                "completed_at": None,
                "expires_at": None,
            },
        ),
        401: error_response_doc(
            "Unauthorized", ErrorCode.UNAUTHORIZED, "Could not validate credentials",
        ),
        422: {
            "description": "Validation error",
            "content": {
                "application/json": {
                    "schema": {"$ref": "#/components/schemas/ErrorResponse"},
                    "example": build_error_example(
                        ErrorCode.VALIDATION_ERROR,
                        "Request validation failed",
                        details={
                            "validation_errors": [
                                {
                                    "field": "url",
                                    "message": "Value error, Must be a valid supported URL",
                                    "type": "value_error",
                                },
                            ],
                        },
                    ),
                },
            },
        },
    },
)
@limiter.limit("10/minute")
async def create_download(
    request: Request,
    data: DownloadCreate,
    current_user: CurrentUser,
    db: DbSession,
) -> DownloadResponse:
    """Create a new download job for the authenticated user."""
    service = DownloadService(db, current_user.id)
    job = await service.create(data.url)
    await service.best_effort_enqueue(job.id)
    return _job_to_response(job)


@router.get(
    "",
    response_model=DownloadListResponse,
    summary="List download jobs",
    description="Return paginated download jobs belonging to the authenticated user.",
    responses={
        200: success_response_doc(
            "List of download jobs",
            {
                "downloads": [
                    {
                        "id": "550e8400-e29b-41d4-a716-446655440000",
                        "url": "https://www.youtube.com/watch?v=dQw4w9WgXcQ",
                        "status": "completed",
                        "file_name": "video.mp4",
                        "error": None,
                        "created_at": "2026-04-07T12:00:00Z",
                        "completed_at": "2026-04-07T12:01:00Z",
                        "expires_at": "2026-04-08T12:01:00Z",
                    },
                ],
                "pagination": {"page": 1, "per_page": 20, "total": 1},
            },
        ),
        401: error_response_doc(
            "Unauthorized", ErrorCode.UNAUTHORIZED, "Could not validate credentials",
        ),
        422: {
            "description": "Validation error",
            "content": {
                "application/json": {
                    "schema": {"$ref": "#/components/schemas/ErrorResponse"},
                    "example": build_error_example(
                        ErrorCode.VALIDATION_ERROR,
                        "Request validation failed",
                        details={
                            "validation_errors": [
                                {
                                    "field": "query.page",
                                    "message": "Input should be greater than or equal to 1",
                                    "type": "greater_than_equal",
                                },
                            ],
                        },
                    ),
                },
            },
        },
    },
)
async def list_downloads(
    current_user: CurrentUser,
    db: DbSession,
    page: int = Query(default=1, ge=1, description="Page number"),
    per_page: int = Query(default=20, ge=1, le=100, description="Items per page"),
) -> DownloadListResponse:
    """List all download jobs for the authenticated user with pagination."""
    result = await DownloadService(db, current_user.id).list(page, per_page)
    return DownloadListResponse(
        downloads=[_job_to_response(job) for job in result.jobs],
        pagination=PaginationInfo(page=result.page, per_page=result.per_page, total=result.total),
    )


@router.get(
    "/{job_id}",
    response_model=DownloadResponse,
    summary="Get download job",
    description="Return a specific download job by id if it belongs to the current user.",
    responses={
        200: success_response_doc(
            "Download job details",
            {
                "id": "550e8400-e29b-41d4-a716-446655440000",
                "url": "https://www.youtube.com/watch?v=dQw4w9WgXcQ",
                "status": "processing",
                "file_name": None,
                "error": None,
                "created_at": "2026-04-07T12:00:00Z",
                "completed_at": None,
                "expires_at": None,
            },
        ),
        401: error_response_doc(
            "Unauthorized", ErrorCode.UNAUTHORIZED, "Could not validate credentials",
        ),
        404: error_response_doc(
            "Download job not found",
            ErrorCode.NOT_FOUND,
            "Download job not found",
            details={"job_id": "unknown-id"},
        ),
    },
)
async def get_download(
    job_id: str,
    current_user: CurrentUser,
    db: DbSession,
) -> DownloadResponse:
    """Get a specific download job by ID."""
    try:
        job = await DownloadService(db, current_user.id).get(job_id)
    except Exception as exc:
        raise _map_download_service_error(exc) from exc
    return _job_to_response(job)


@router.get(
    "/{job_id}/file",
    summary="Download output file",
    description="Download the processed file for a completed, non-expired download job.",
    responses={
        200: {"description": "Binary file stream"},
        400: error_response_doc(
            "Job not completed",
            ErrorCode.VALIDATION_ERROR,
            "Job is not completed. Current status: processing",
        ),
        401: error_response_doc(
            "Unauthorized", ErrorCode.UNAUTHORIZED, "Could not validate credentials",
        ),
        403: error_response_doc(
            "Invalid file path", ErrorCode.FORBIDDEN, "Access denied: invalid file path",
        ),
        404: error_response_doc(
            "Job or file not found",
            ErrorCode.NOT_FOUND,
            "File not found",
            details={"job_id": "550e8400-e29b-41d4-a716-446655440000"},
        ),
        410: error_response_doc(
            "Download link expired", ErrorCode.VALIDATION_ERROR, "Download link has expired",
        ),
    },
)
async def get_download_file(
    job_id: str,
    current_user: CurrentUser,
    db: DbSession,
) -> FileResponse:
    """Download the file for a completed job."""
    try:
        file_result = await DownloadService(db, current_user.id).get_file_path(job_id)
    except Exception as exc:
        raise _map_download_service_error(exc) from exc
    return FileResponse(
        path=file_result.path,
        filename=file_result.filename,
        media_type="application/octet-stream",
    )


@router.post("/{job_id}/retry", response_model=DownloadResponse)
@limiter.limit("10/minute")
async def retry_download(
    request: Request,
    job_id: str,
    current_user: CurrentUser,
    db: DbSession,
) -> DownloadResponse:
    """Retry a failed download job."""
    try:
        job = await DownloadService(db, current_user.id).retry(job_id)
    except Exception as exc:
        raise _map_download_service_error(exc) from exc
    return _job_to_response(job)


@router.delete(
    "/{job_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Delete download job",
    description="Delete a download job and remove its file from storage when present.",
    responses={
        204: {"description": "Download job deleted"},
        401: error_response_doc(
            "Unauthorized", ErrorCode.UNAUTHORIZED, "Could not validate credentials",
        ),
        404: error_response_doc(
            "Download job not found",
            ErrorCode.NOT_FOUND,
            "Download job not found",
            details={"job_id": "unknown-id"},
        ),
    },
)
@limiter.limit("30/minute")
async def delete_download(
    request: Request,
    job_id: str,
    current_user: CurrentUser,
    db: DbSession,
) -> None:
    """Delete a download job and its associated file."""
    try:
        await DownloadService(db, current_user.id).delete(job_id)
    except Exception as exc:
        raise _map_download_service_error(exc) from exc


@router.get(
    "/failed",
    response_model=FailedJobListResponse,
    summary="List failed jobs (DLQ)",
    description="Return paginated failed jobs from the dead letter queue for the authenticated user.",
)
async def list_failed_jobs(
    current_user: CurrentUser,
    db: DbSession,
    page: int = Query(default=1, ge=1, description="Page number"),
    per_page: int = Query(default=20, ge=1, le=100, description="Items per page"),
    category: str | None = Query(default=None, description="Filter by error category"),
) -> FailedJobListResponse:
    """List failed jobs for the authenticated user."""
    result = await DownloadService(db, current_user.id).resolve_errors(page, per_page, category)
    return FailedJobListResponse(
        failed_jobs=[FailedJobResponse.model_validate(job) for job in result.failed_jobs],
        pagination=PaginationInfo(page=result.page, per_page=result.per_page, total=result.total),
    )


@router.post(
    "/failed/{failed_job_id}/replay",
    response_model=DownloadResponse,
    summary="Replay a failed job from DLQ",
    description="Move a failed job back to the download queue for reprocessing.",
)
@limiter.limit("10/minute")
async def replay_failed_job(
    request: Request,
    failed_job_id: str,
    current_user: CurrentUser,
    db: DbSession,
) -> DownloadResponse:
    """Replay a failed DLQ row."""
    try:
        job = await DownloadService(db, current_user.id).replay_failed(failed_job_id)
    except Exception as exc:
        raise _map_download_service_error(exc) from exc
    return _job_to_response(job)


@router.post(
    "/failed/replay-all",
    summary="Replay all failed jobs",
    description="Replay all failed jobs for the authenticated user, optionally filtered by category.",
)
@limiter.limit("5/minute")
async def replay_all_failed_jobs(
    request: Request,
    current_user: CurrentUser,
    db: DbSession,
    category: str | None = Query(default=None, description="Filter by error category"),
) -> dict:
    """Replay failed DLQ rows in one bounded batch."""
    result = await DownloadService(db, current_user.id).replay_all_failed(category=category)
    return {"replayed": result.replayed, "total": result.total}


def _prioritize_static_dlq_routes() -> None:
    """Ensure static DLQ routes are matched before generic job-id routes."""
    dlq_routes = []
    retained_routes = []
    for route in router.routes:
        path = getattr(route, "path", "")
        if path.startswith(("/failed", "/downloads/failed")):
            dlq_routes.append(route)
        else:
            retained_routes.append(route)

    if not dlq_routes:
        return

    insert_at = next(
        (
            index
            for index, route in enumerate(retained_routes)
            if getattr(route, "path", "").startswith(("/{job_id}", "/downloads/{job_id}"))
        ),
        len(retained_routes),
    )
    router.routes[:] = retained_routes[:insert_at] + dlq_routes + retained_routes[insert_at:]


_prioritize_static_dlq_routes()

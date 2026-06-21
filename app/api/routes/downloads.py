"""Download job CRUD endpoints with DLQ replay capabilities."""

import os
import uuid
from datetime import UTC, datetime

from fastapi import APIRouter, HTTPException, Query, Request, status
from fastapi.responses import FileResponse
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

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
from app.services.outbox_service import write_job_to_outbox
from app.services.yt_dlp_service import resolve_video_title
from core.config import settings
from core.logging_config import get_logger
from core.models.download_job import DownloadJob
from core.models.failed_job import FailedJob
from core.utils.security import validate_path

logger = get_logger(__name__)

router = APIRouter(prefix="/downloads", tags=["downloads"])


def _downloads_base_path() -> str:
    """Build the configured downloads directory path."""
    return os.path.join(settings.storage_path, "downloads")


def _job_to_response(job: DownloadJob) -> DownloadResponse:
    """Convert a DownloadJob ORM model to a DownloadResponse schema.

    Uses Pydantic's model_validate to avoid manual field mapping.
    """
    return DownloadResponse.model_validate(job)


async def _update_user_dlq_depth(db: AsyncSession, user_id: uuid.UUID) -> None:
    """Update the DLQ depth metric after user-scoped replay operations."""
    try:
        from core.metrics import DLQ_DEPTH

        count_result = await db.execute(select(func.count()).where(FailedJob.user_id == user_id))
        DLQ_DEPTH.set(float(count_result.scalar() or 0))
    except Exception:
        pass


async def _get_user_job(db: DbSession, user_id: uuid.UUID, job_id: str) -> DownloadJob:
    """Fetch a download job belonging to the specified user.

    Raises HTTPException(404) if not found.
    """
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
            DownloadJob.user_id == user_id,
        )
    )
    job: DownloadJob | None = result.scalars().one_or_none()
    if job is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Download job not found",
        )
    return job


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
            "Unauthorized", ErrorCode.UNAUTHORIZED, "Could not validate credentials"
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
    job_id = uuid.uuid4()

    # Pre-resolve video title so it appears immediately in the UI instead of the URL.
    # This is fast (~0.5-3s) because yt-dlp runs with download=False.
    # If resolution fails, title stays None and the worker resolves it later.
    title = await resolve_video_title(data.url)

    job = DownloadJob(
        id=job_id,
        user_id=current_user.id,
        url=data.url,
        status="pending",
        title=title,
    )

    db.add(job)
    await write_job_to_outbox(db, job_id)
    await db.commit()
    await db.refresh(job)

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
            "Unauthorized", ErrorCode.UNAUTHORIZED, "Could not validate credentials"
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

    # Get total count
    count_result = await db.execute(
        select(func.count()).where(DownloadJob.user_id == current_user.id)
    )
    total = count_result.scalar_one()

    # Get paginated results
    result = await db.execute(
        select(DownloadJob)
        .where(DownloadJob.user_id == current_user.id)
        .order_by(DownloadJob.created_at.desc())
        .offset((page - 1) * per_page)
        .limit(per_page)
    )
    jobs = result.scalars().all()

    return DownloadListResponse(
        downloads=[_job_to_response(job) for job in jobs],
        pagination=PaginationInfo(
            page=page,
            per_page=per_page,
            total=total,
        ),
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
            "Unauthorized", ErrorCode.UNAUTHORIZED, "Could not validate credentials"
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
    job = await _get_user_job(db, current_user.id, job_id)
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
            "Unauthorized", ErrorCode.UNAUTHORIZED, "Could not validate credentials"
        ),
        403: error_response_doc(
            "Invalid file path", ErrorCode.FORBIDDEN, "Access denied: invalid file path"
        ),
        404: error_response_doc(
            "Job or file not found",
            ErrorCode.NOT_FOUND,
            "File not found",
            details={"job_id": "550e8400-e29b-41d4-a716-446655440000"},
        ),
        410: error_response_doc(
            "Download link expired", ErrorCode.VALIDATION_ERROR, "Download link has expired"
        ),
    },
)
async def get_download_file(
    job_id: str,
    current_user: CurrentUser,
    db: DbSession,
) -> FileResponse:
    """Download the file for a completed job."""
    job = await _get_user_job(db, current_user.id, job_id)

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
        # Normalize both timestamps to UTC for comparison.
        # SQLite returns naive datetimes even for timezone-aware columns,
        # while PostgreSQL returns proper timezone-aware values.
        now_utc = datetime.now(UTC)
        expires_at = job.expires_at
        if expires_at.tzinfo is None:
            expires_at = expires_at.replace(tzinfo=UTC)
        else:
            expires_at = expires_at.astimezone(UTC)
        if expires_at < now_utc:
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


@router.post("/{job_id}/retry", response_model=DownloadResponse)
@limiter.limit("10/minute")
async def retry_download(
    request: Request,
    job_id: str,
    current_user: CurrentUser,
    db: DbSession,
) -> DownloadResponse:
    """Retry a failed download job."""
    job = await _get_user_job(db, current_user.id, job_id)

    if job.status not in ("failed", "deferred"):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Only failed or deferred jobs can be retried",
        )

    job.status = "pending"
    job.retry_count = 0
    job.next_retry_at = None
    job.error = None
    job.error_category = None
    job.completed_at = None

    await write_job_to_outbox(db, job.id)
    await db.commit()
    await db.refresh(job)

    return _job_to_response(job)


@router.delete(
    "/{job_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Delete download job",
    description="Delete a download job and remove its file from storage when present.",
    responses={
        204: {"description": "Download job deleted"},
        401: error_response_doc(
            "Unauthorized", ErrorCode.UNAUTHORIZED, "Could not validate credentials"
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
    job = await _get_user_job(db, current_user.id, job_id)

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
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Failed to delete file from disk",
            ) from e

    await db.delete(job)
    await db.commit()


# -- Failed Job (DLQ) endpoints --------------------------------------------


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
    query = select(FailedJob).where(FailedJob.user_id == current_user.id)
    count_query = select(func.count()).where(FailedJob.user_id == current_user.id)

    if category:
        query = query.where(FailedJob.error_category == category)
        count_query = count_query.where(FailedJob.error_category == category)

    count_result = await db.execute(count_query)
    total = count_result.scalar_one()

    result = await db.execute(
        query.order_by(FailedJob.failed_at.desc()).offset((page - 1) * per_page).limit(per_page)
    )
    jobs = result.scalars().all()

    return FailedJobListResponse(
        failed_jobs=[FailedJobResponse.model_validate(j) for j in jobs],
        pagination=PaginationInfo(page=page, per_page=per_page, total=total),
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
    try:
        f_id = uuid.UUID(failed_job_id)
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid failed job ID format",
        ) from None

    result = await db.execute(
        select(FailedJob).where(
            FailedJob.id == f_id,
            FailedJob.user_id == current_user.id,
        )
    )
    failed_job = result.scalars().one_or_none()
    if failed_job is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Failed job not found",
        )

    if failed_job.original_job_id:
        result = await db.execute(
            select(DownloadJob).where(
                DownloadJob.id == failed_job.original_job_id,
                DownloadJob.user_id == current_user.id,
            )
        )
        original = result.scalars().one_or_none()
        if original:
            # Delete failed_job first, then update original, then single commit.
            # Order matters: deleting the FK reference before updating prevents
            # inconsistency if the commit fails partway through.
            await db.delete(failed_job)
            original.status = "pending"
            original.retry_count = 0
            original.next_retry_at = None
            original.error = None
            original.error_category = None  # type: ignore[assignment]
            original.completed_at = None
            await write_job_to_outbox(db, original.id)
            await db.commit()
            await db.refresh(original)

            await _update_user_dlq_depth(db, current_user.id)

            return DownloadResponse.model_validate(original)

    new_job_id = uuid.uuid4()
    job = DownloadJob(
        id=new_job_id,
        user_id=current_user.id,
        url=failed_job.url,
        status="pending",
    )
    db.add(job)
    await write_job_to_outbox(db, new_job_id)
    await db.delete(failed_job)
    await db.commit()
    await db.refresh(job)
    await _update_user_dlq_depth(db, current_user.id)

    return DownloadResponse.model_validate(job)


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
    _max_batch = 500

    query = select(FailedJob).where(FailedJob.user_id == current_user.id)
    if category:
        query = query.where(FailedJob.error_category == category)
    query = query.limit(_max_batch)

    result = await db.execute(query)
    failed_jobs = result.scalars().all()

    # Batch-load all original DownloadJobs in a single query to avoid N+1
    original_ids = [fj.original_job_id for fj in failed_jobs if fj.original_job_id]
    originals_by_id: dict[uuid.UUID, DownloadJob] = {}
    if original_ids:
        orig_result = await db.execute(
            select(DownloadJob).where(
                DownloadJob.id.in_(original_ids),
                DownloadJob.user_id == current_user.id,
            )
        )
        for o in orig_result.scalars().all():
            originals_by_id[o.id] = o

    replayed = 0
    for failed_job in failed_jobs:
        if failed_job.original_job_id:
            original = originals_by_id.get(failed_job.original_job_id)
            if original:
                original.status = "pending"
                original.retry_count = 0
                original.next_retry_at = None
                original.error = None
                original.error_category = None
                original.completed_at = None
                await write_job_to_outbox(db, original.id)
                await db.delete(failed_job)
                replayed += 1
                continue

        new_job_id = uuid.uuid4()
        job = DownloadJob(
            id=new_job_id,
            user_id=current_user.id,
            url=failed_job.url,
            status="pending",
        )
        db.add(job)
        await write_job_to_outbox(db, new_job_id)
        await db.delete(failed_job)
        replayed += 1

    await db.commit()

    await _update_user_dlq_depth(db, current_user.id)

    return {"replayed": replayed, "total": len(failed_jobs)}


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

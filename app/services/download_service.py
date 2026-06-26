"""Shared download-domain service for REST and Web route layers."""

import os
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import delete as sqlalchemy_delete
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.services.outbox_service import write_job_to_outbox
from app.services.yt_dlp_service import resolve_video_title
from core.config import settings
from core.logging_config import get_logger
from core.models.download_job import DownloadJob
from core.models.failed_job import FailedJob
from core.models.outbox import Outbox
from core.queue import enqueue_job
from core.utils.security import validate_path

logger = get_logger(__name__)


@dataclass(frozen=True)
class DownloadPage:
    """Paginated download jobs for the current user."""

    jobs: list[DownloadJob]
    page: int
    per_page: int
    total: int


@dataclass(frozen=True)
class FailedJobPage:
    """Paginated failed jobs for the current user."""

    failed_jobs: list[FailedJob]
    page: int
    per_page: int
    total: int


@dataclass(frozen=True)
class DownloadFilePath:
    """Validated file path data needed by route response builders."""

    path: str
    filename: str | None


@dataclass(frozen=True)
class DeleteOutcome:
    """Result of deleting a download and optionally its file."""

    file_deleted: bool


@dataclass(frozen=True)
class ReplayAllResult:
    """Replay-all DLQ result counts."""

    replayed: int
    total: int


class DownloadServiceError(Exception):
    """Base class for download service domain errors."""


class InvalidDownloadIdError(DownloadServiceError):
    """Raised when a download or failed-job ID is not a UUID."""

    def __init__(self, message: str = "Invalid job ID format") -> None:
        super().__init__(message)


class DownloadNotFoundError(DownloadServiceError):
    """Raised when a user-owned download job is not found."""

    def __init__(self, message: str = "Download job not found") -> None:
        super().__init__(message)


class FailedJobNotFoundError(DownloadServiceError):
    """Raised when a user-owned failed job is not found."""

    def __init__(self, message: str = "Failed job not found") -> None:
        super().__init__(message)


class InvalidDownloadStatusError(DownloadServiceError):
    """Raised when the current job status is not valid for an operation."""

    def __init__(self, status: str, message: str) -> None:
        self.status = status
        super().__init__(message)


class UnsafeDownloadPathError(DownloadServiceError):
    """Raised when a stored file path escapes the downloads directory."""

    def __init__(self, message: str = "Access denied: invalid file path") -> None:
        super().__init__(message)


class DownloadFileExpiredError(DownloadServiceError):
    """Raised when a completed job's download link is expired."""

    def __init__(self, message: str = "Download link has expired") -> None:
        super().__init__(message)


class DownloadFileMissingError(DownloadServiceError):
    """Raised when a completed job has no accessible file."""

    def __init__(self, message: str = "File not found", code: str = "missing_file") -> None:
        self.code = code
        super().__init__(message)


class DownloadFileDeleteFailedError(DownloadServiceError):
    """Raised when a route policy requires file deletion to fail the operation."""

    def __init__(self, message: str = "Failed to delete file from disk") -> None:
        super().__init__(message)


class DownloadService:
    """Download business logic shared by API and Web routes."""

    def __init__(self, db: AsyncSession, user_id: uuid.UUID) -> None:
        self.db = db
        self.user_id = user_id

    async def create(self, url: str, *, resolve_title: bool = True) -> DownloadJob:
        """Create a pending download and transactional outbox row."""
        job_id = uuid.uuid4()
        title = await resolve_video_title(url) if resolve_title else None
        job = DownloadJob(
            id=job_id,
            user_id=self.user_id,
            url=url,
            status="pending",
            title=title,
        )
        self.db.add(job)
        try:
            await write_job_to_outbox(self.db, job_id)
            await self.db.commit()
            await self.db.refresh(job)
        except Exception:
            await self.db.rollback()
            raise
        return job

    async def list(self, page: int, per_page: int) -> DownloadPage:
        """Return user-owned downloads ordered newest first."""
        count_result = await self.db.execute(
            select(func.count()).where(DownloadJob.user_id == self.user_id),
        )
        total = count_result.scalar_one()
        result = await self.db.execute(
            select(DownloadJob)
            .where(DownloadJob.user_id == self.user_id)
            .order_by(DownloadJob.created_at.desc())
            .offset((page - 1) * per_page)
            .limit(per_page),
        )
        return DownloadPage(
            jobs=list(result.scalars().all()),
            page=page,
            per_page=per_page,
            total=total,
        )

    async def get(self, job_id: str | uuid.UUID) -> DownloadJob:
        """Return a user-owned download by ID."""
        job_uuid = self._parse_uuid(job_id)
        result = await self.db.execute(
            select(DownloadJob).where(
                DownloadJob.id == job_uuid,
                DownloadJob.user_id == self.user_id,
            ),
        )
        job: DownloadJob | None = result.scalars().one_or_none()
        if job is None:
            raise DownloadNotFoundError
        return job

    async def retry(self, job_id: str | uuid.UUID) -> DownloadJob:
        """Reset a failed or deferred job and write an enqueue outbox row."""
        job = await self.get(job_id)
        if job.status not in ("failed", "deferred"):
            raise InvalidDownloadStatusError(
                job.status,
                "Only failed or deferred jobs can be retried",
            )

        self._reset_job_for_replay(job)
        await write_job_to_outbox(self.db, job.id)
        await self.db.commit()
        await self.db.refresh(job)
        return job

    async def get_file_path(self, job_id: str | uuid.UUID) -> DownloadFilePath:
        """Validate and return the disk file path for a completed download."""
        job = await self.get(job_id)
        if job.status != "completed":
            raise InvalidDownloadStatusError(
                job.status,
                f"Job is not completed. Current status: {job.status}",
            )
        if not job.file_path:
            raise DownloadFileMissingError("File not found", code="missing_file_path")

        safe_path = self._validate_download_path(job.file_path)
        if not os.path.isfile(safe_path):
            safe_job_id = str(job_id).replace("\r", "").replace("\n", "")
            logger.error("file_missing_from_disk", job_id=safe_job_id, file_path=safe_path)
            raise DownloadFileMissingError("File not found on disk", code="missing_on_disk")

        if job.expires_at and self._as_utc(job.expires_at) < datetime.now(UTC):
            raise DownloadFileExpiredError

        return DownloadFilePath(path=safe_path, filename=job.file_name)

    async def delete(
        self,
        job_id: str | uuid.UUID,
        *,
        allowed_statuses: set[str] | None = None,
        fail_on_file_delete: bool = True,
    ) -> DeleteOutcome:
        """Delete a user-owned job and apply route-specific file cleanup policy."""
        job = await self.get(job_id)
        if allowed_statuses is not None and job.status not in allowed_statuses:
            raise InvalidDownloadStatusError(
                job.status,
                (
                    f"Cannot delete job with status '{job.status}'. Only completed, failed, or "
                    "cancelled jobs can be deleted."
                ),
            )

        file_deleted = False
        if job.file_path:
            safe_path = self._validate_download_path(job.file_path)
            if os.path.isfile(safe_path):
                try:
                    os.remove(safe_path)
                    file_deleted = True
                    logger.info("file_deleted", file_path=safe_path)
                except OSError as exc:
                    logger.warning("failed_to_delete_file", file_path=job.file_path, error=str(exc))
                    if fail_on_file_delete:
                        raise DownloadFileDeleteFailedError from exc

        await self.db.delete(job)
        await self.db.commit()
        return DeleteOutcome(file_deleted=file_deleted)

    async def resolve_errors(
        self, page: int, per_page: int, category: str | None = None,
    ) -> FailedJobPage:
        """Return paginated user-owned failed jobs."""
        query = select(FailedJob).where(FailedJob.user_id == self.user_id)
        count_query = select(func.count()).where(FailedJob.user_id == self.user_id)
        if category:
            query = query.where(FailedJob.error_category == category)
            count_query = count_query.where(FailedJob.error_category == category)

        count_result = await self.db.execute(count_query)
        total = count_result.scalar_one()
        result = await self.db.execute(
            query.order_by(FailedJob.failed_at.desc()).offset((page - 1) * per_page).limit(per_page),
        )
        return FailedJobPage(
            failed_jobs=list(result.scalars().all()),
            page=page,
            per_page=per_page,
            total=total,
        )

    async def list_failed(
        self, page: int, per_page: int, category: str | None = None,
    ) -> FailedJobPage:
        """Alias for failed-job listing."""
        return await self.resolve_errors(page, per_page, category)

    async def replay_failed(self, failed_job_id: str | uuid.UUID) -> DownloadJob:
        """Replay a single failed job, preserving atomic DLQ cleanup and outbox write."""
        failed_job = await self._get_failed_job(failed_job_id)
        if failed_job.original_job_id:
            original = await self._get_original_for_failed_job(failed_job.original_job_id)
            if original is not None:
                await self.db.delete(failed_job)
                self._reset_job_for_replay(original)
                await write_job_to_outbox(self.db, original.id)
                await self.db.commit()
                await self.db.refresh(original)
                await self._update_user_dlq_depth()
                return original

        job = DownloadJob(
            id=uuid.uuid4(),
            user_id=self.user_id,
            url=failed_job.url,
            status="pending",
        )
        self.db.add(job)
        await write_job_to_outbox(self.db, job.id)
        await self.db.delete(failed_job)
        await self.db.commit()
        await self.db.refresh(job)
        await self._update_user_dlq_depth()
        return job

    async def replay_all_failed(
        self,
        *,
        category: str | None = None,
        max_batch: int = 500,
    ) -> ReplayAllResult:
        """Replay a batch of failed jobs without per-row original-job lookups."""
        query = select(FailedJob).where(FailedJob.user_id == self.user_id)
        if category:
            query = query.where(FailedJob.error_category == category)
        query = query.limit(max_batch)

        result = await self.db.execute(query)
        failed_jobs = list(result.scalars().all())

        original_ids = [failed.original_job_id for failed in failed_jobs if failed.original_job_id]
        originals_by_id: dict[uuid.UUID, DownloadJob] = {}
        if original_ids:
            original_result = await self.db.execute(
                select(DownloadJob).where(
                    DownloadJob.id.in_(original_ids),
                    DownloadJob.user_id == self.user_id,
                ),
            )
            for original in original_result.scalars().all():
                originals_by_id[original.id] = original

        replayed = 0
        for failed_job in failed_jobs:
            if failed_job.original_job_id:
                replay_original = originals_by_id.get(failed_job.original_job_id)
                if replay_original is not None:
                    self._reset_job_for_replay(replay_original)
                    await write_job_to_outbox(self.db, replay_original.id)
                    await self.db.delete(failed_job)
                    replayed += 1
                    continue

            job = DownloadJob(
                id=uuid.uuid4(),
                user_id=self.user_id,
                url=failed_job.url,
                status="pending",
            )
            self.db.add(job)
            await write_job_to_outbox(self.db, job.id)
            await self.db.delete(failed_job)
            replayed += 1

        await self.db.commit()
        await self._update_user_dlq_depth()
        return ReplayAllResult(replayed=replayed, total=len(failed_jobs))

    async def best_effort_enqueue(self, job_id: uuid.UUID) -> None:
        """Try immediate queueing and leave outbox recovery intact on failure."""
        cleanup_started = False
        try:
            await enqueue_job(job_id)
            cleanup_started = True
            await self.db.execute(
                sqlalchemy_delete(Outbox).where(
                    Outbox.job_id == job_id,
                    Outbox.status == "pending",
                ),
            )
            await self.db.commit()
        except Exception:
            if cleanup_started:
                await self.db.rollback()
            logger.warning("failed_to_enqueue_job_outbox_recovery", job_id=str(job_id))

    async def _get_failed_job(self, failed_job_id: str | uuid.UUID) -> FailedJob:
        failed_uuid = self._parse_uuid(failed_job_id, message="Invalid failed job ID format")
        result = await self.db.execute(
            select(FailedJob).where(
                FailedJob.id == failed_uuid,
                FailedJob.user_id == self.user_id,
            ),
        )
        failed_job: FailedJob | None = result.scalars().one_or_none()
        if failed_job is None:
            raise FailedJobNotFoundError
        return failed_job

    async def _get_original_for_failed_job(self, original_job_id: uuid.UUID) -> DownloadJob | None:
        result = await self.db.execute(
            select(DownloadJob).where(
                DownloadJob.id == original_job_id,
                DownloadJob.user_id == self.user_id,
            ),
        )
        original_job: DownloadJob | None = result.scalars().one_or_none()
        return original_job

    async def _update_user_dlq_depth(self) -> None:
        try:
            from core.metrics import DLQ_DEPTH

            count_result = await self.db.execute(
                select(func.count()).where(FailedJob.user_id == self.user_id),
            )
            DLQ_DEPTH.set(float(count_result.scalar() or 0))
        except Exception:
            pass

    def _validate_download_path(self, file_path: str) -> str:
        try:
            return validate_path(self._downloads_base_path(), file_path)
        except (ValueError, PermissionError) as exc:
            raise UnsafeDownloadPathError from exc

    @staticmethod
    def _downloads_base_path() -> str:
        return os.path.join(settings.storage_path, "downloads")

    @staticmethod
    def _as_utc(value: datetime) -> datetime:
        if value.tzinfo is None:
            return value.replace(tzinfo=UTC)
        return value.astimezone(UTC)

    @staticmethod
    def _parse_uuid(value: str | uuid.UUID, message: str = "Invalid job ID format") -> uuid.UUID:
        if isinstance(value, uuid.UUID):
            return value
        try:
            return uuid.UUID(value)
        except ValueError:
            raise InvalidDownloadIdError(message) from None

    @staticmethod
    def _reset_job_for_replay(job: DownloadJob) -> None:
        job.status = "pending"
        job.retry_count = 0
        job.next_retry_at = None
        job.error = None
        job.last_error = None
        job.error_category = None
        job.completed_at = None

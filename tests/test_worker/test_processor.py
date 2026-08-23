"""Tests for worker processor module."""

from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, patch
from uuid import UUID

import pytest
from sqlalchemy import select

from app.services.error_classifier import ErrorCategory
from core.models.download_job import DownloadJob


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


class TestProcessNextJob:
    """Tests for process_next_job function."""

    @pytest.fixture
    def mock_redis_client(self):
        """Create a mock Redis client."""
        mock = AsyncMock()
        mock.rpop = AsyncMock(return_value=None)
        mock.zadd = AsyncMock(return_value=1)
        mock.lpush = AsyncMock(return_value=1)
        mock.brpop = AsyncMock(return_value=None)
        mock.exists = AsyncMock(return_value=0)
        return mock

    @pytest.mark.unit
    async def test_process_next_job_empty_queue(self, db_session, mock_redis_client):
        """Test that processing an empty queue returns early."""
        from worker.processor import process_next_job

        with patch("worker.job_claimer.redis_client", mock_redis_client):
            mock_redis_client.rpop = AsyncMock(return_value=None)

            # Should return without error
            await process_next_job()

        # Verify no jobs were processed
        mock_redis_client.rpop.assert_called_once_with("download_queue")

    @pytest.mark.unit
    async def test_process_next_job_not_found(self, db_session, mock_redis_client):
        """Test processing a job that doesn't exist in database."""
        from worker.processor import process_next_job

        with patch("worker.job_claimer.redis_client", mock_redis_client):
            mock_redis_client.rpop = AsyncMock(return_value="550e8400-e29b-41d4-a716-446655440099")

            # Should log warning and return
            await process_next_job()

        mock_redis_client.rpop.assert_called_once()

    @pytest.mark.unit
    async def test_process_next_job_completes_success(self, db_session, mock_redis_client):
        """Test successful job completion."""
        import asyncio

        from core.database import get_async_session_factory
        from worker.processor import process_next_job

        # Create a pending job in the database
        job = DownloadJob(
            id=UUID("550e8400-e29b-41d4-a716-446655440000"),
            user_id=UUID("550e8400-e29b-41d4-a716-446655440005"),
            url="https://www.youtube.com/watch?v=test",
            status="pending",
        )
        db_session.add(job)
        await db_session.commit()

        # Mock shutdown_event to ensure it's not set during test
        mock_shutdown_event = asyncio.Event()

        with (
            patch("worker.job_claimer.redis_client", mock_redis_client),
            patch("worker.job_executor.redis_client", mock_redis_client),
            patch(
                "worker.job_executor.extract_media_with_circuit_breaker",
                new_callable=AsyncMock,
            ) as mock_extract,
            patch("worker.job_executor.publish_job_status", new_callable=AsyncMock),
            patch("worker.job_executor.get_risk_score", new_callable=AsyncMock, return_value=0.0),
            patch("worker.main.shutdown_event", mock_shutdown_event),
            patch("worker.state.shutdown_event", mock_shutdown_event),
        ):
            mock_extract.return_value = ("/storage/test.mp4", "test.mp4", "Test Video")
            mock_redis_client.rpop = AsyncMock(return_value="550e8400-e29b-41d4-a716-446655440000")

            await process_next_job()

        # Use a fresh session to read the job (bypass session cache)
        session_factory = get_async_session_factory()
        async with session_factory() as new_session:
            result = await new_session.execute(
                select(DownloadJob).where(
                    DownloadJob.id == UUID("550e8400-e29b-41d4-a716-446655440000")
                ),
            )
            completed_job = result.scalar_one()
            assert completed_job.status == "completed"
            assert completed_job.file_path == "/storage/test.mp4"

    @pytest.mark.unit
    @pytest.mark.asyncio
    async def test_retry_scheduling_delegates_delay_calculation(
        self,
        db_session,
        mock_redis_client,
    ):
        """Test retry scheduling uses worker.retry_scheduler.calculate_delay for next_retry_at."""
        import asyncio

        from app.services.error_classifier import ErrorCategory
        from core.database import get_async_session_factory
        from worker.processor import process_next_job

        job_id = UUID("550e8400-e29b-41d4-a716-446655440010")
        job = DownloadJob(
            id=job_id,
            user_id=UUID("550e8400-e29b-41d4-a716-446655440005"),
            url="https://www.youtube.com/watch?v=retry",
            status="pending",
            retry_count=0,
            max_retries=5,
        )
        db_session.add(job)
        await db_session.commit()

        sentinel_delay = 123.0
        retry_queue_calls: list[tuple[UUID, float]] = []

        async def record_retry_queue(job_id_arg: UUID, retry_timestamp: float) -> bool:
            retry_queue_calls.append((job_id_arg, retry_timestamp))
            return True

        mock_shutdown_event = asyncio.Event()

        with (
            patch("worker.job_executor.redis_client", mock_redis_client),
            patch(
                "worker.job_executor.extract_media_with_circuit_breaker",
                new_callable=AsyncMock,
                side_effect=RuntimeError("HTTP Error 429 Too Many Requests Retry-After: 90"),
            ),
            patch("worker.job_executor.publish_job_status", new_callable=AsyncMock),
            patch("worker.job_executor.get_risk_score", new_callable=AsyncMock, return_value=0.0),
            patch(
                "worker.retry_scheduler.calculate_delay", return_value=sentinel_delay
            ) as delay_mock,
            patch(
                "worker.retry_scheduler.push_to_retry_queue",
                new_callable=AsyncMock,
                side_effect=record_retry_queue,
            ),
            patch("worker.main.shutdown_event", mock_shutdown_event),
            patch("worker.state.shutdown_event", mock_shutdown_event),
        ):
            started_at = datetime.now(UTC)
            await process_next_job(job_id)
            finished_at = datetime.now(UTC)

        delay_mock.assert_called_once_with(
            category=ErrorCategory.RATE_LIMITED,
            attempt=0,
            prev_delay=None,
            retry_after=90,
        )

        session_factory = get_async_session_factory()
        async with session_factory() as new_session:
            result = await new_session.execute(select(DownloadJob).where(DownloadJob.id == job_id))
            retried_job = result.scalar_one()

        assert retried_job.status == "pending"
        assert retried_job.retry_count == 1
        assert retried_job.error_category == ErrorCategory.RATE_LIMITED.value
        assert retried_job.next_retry_at is not None

        scheduled_at = _as_utc(retried_job.next_retry_at)
        assert started_at + timedelta(seconds=sentinel_delay - 1) <= scheduled_at
        assert scheduled_at <= finished_at + timedelta(seconds=sentinel_delay + 1)

        assert len(retry_queue_calls) == 1
        queued_job_id, retry_timestamp = retry_queue_calls[0]
        assert queued_job_id == job_id
        assert retry_timestamp == pytest.approx(scheduled_at.timestamp(), abs=0.01)

    @pytest.mark.unit
    async def test_move_to_dlq_populates_final_error_category(self, db_session):
        """Test failed job retention writes all non-null DLQ columns."""
        from app.services.error_classifier import ErrorCategory
        from core.models.failed_job import FailedJob
        from worker.dlq_manager import move_to_dlq
        from worker.retry_scheduler import RetryDecision

        job = DownloadJob(
            id=UUID("550e8400-e29b-41d4-a716-446655440006"),
            user_id=UUID("550e8400-e29b-41d4-a716-446655440005"),
            url="https://www.youtube.com/watch?v=missing",
            status="failed",
            error="attempt 1 failed",
            error_category=ErrorCategory.NOT_FOUND.value,
            retry_count=0,
            max_retries=3,
            title="Missing Video",
        )
        db_session.add(job)
        await db_session.commit()

        retry_history = "attempt 1 failed -> Non-retryable error"
        final_error = "Non-retryable error (not_found): video unavailable"

        decision = RetryDecision(
            is_final=True,
            delay_seconds=None,
            category=ErrorCategory.NOT_FOUND,
            effective_max_retries=0,
            final_error=final_error,
            retry_after=None,
            retry_count=0,
            accumulated_error=retry_history,
        )

        await move_to_dlq(
            db_session,
            job,
            decision,
            retry_history=retry_history,
        )
        await db_session.commit()

        result = await db_session.execute(
            select(FailedJob).where(FailedJob.original_job_id == job.id)
        )
        failed_job = result.scalar_one()

        assert failed_job.error_category == ErrorCategory.NOT_FOUND.value
        assert failed_job.final_error_category == ErrorCategory.NOT_FOUND.value
        assert failed_job.final_error == final_error
        assert failed_job.retry_history == retry_history
        assert failed_job.max_retries_at_failure == 3

    @pytest.mark.unit
    async def test_reset_stuck_jobs_ignores_recent_processing(self, db_session):
        """Test that recently started processing jobs are not reset."""
        from worker.dlq_manager import reset_stuck_jobs

        # Create a job in processing state but only 5 minutes old
        recent_time = datetime.now(UTC) - timedelta(minutes=5)
        job = DownloadJob(
            id=UUID("550e8400-e29b-41d4-a716-446655440003"),
            user_id=UUID("550e8400-e29b-41d4-a716-446655440005"),
            url="https://www.youtube.com/watch?v=test",
            status="processing",
            updated_at=recent_time,
        )
        db_session.add(job)
        await db_session.commit()

        # Reset with 10 minute timeout - should not affect recent job
        count = await reset_stuck_jobs(timeout_minutes=10)
        assert count == 0

        # Verify job still has processing status
        result = await db_session.execute(
            select(DownloadJob).where(
                DownloadJob.id == UUID("550e8400-e29b-41d4-a716-446655440003")
            )
        )
        still_processing = result.scalar_one()
        assert still_processing.status == "processing"

    @pytest.mark.unit
    async def test_reset_stuck_jobs_ignores_completed(self, db_session):
        """Test that completed jobs are not reset."""
        from worker.dlq_manager import reset_stuck_jobs

        # Create a completed job from 15 minutes ago
        old_time = datetime.now(UTC) - timedelta(minutes=15)
        job = DownloadJob(
            id=UUID("550e8400-e29b-41d4-a716-446655440004"),
            user_id=UUID("550e8400-e29b-41d4-a716-446655440005"),
            url="https://www.youtube.com/watch?v=test",
            status="completed",
            updated_at=old_time,
        )
        db_session.add(job)
        await db_session.commit()

        # Reset with 10 minute timeout - should not affect completed job
        count = await reset_stuck_jobs(timeout_minutes=10)
        assert count == 0

        # Verify job still has completed status
        result = await db_session.execute(
            select(DownloadJob).where(
                DownloadJob.id == UUID("550e8400-e29b-41d4-a716-446655440004")
            ),
        )
        still_completed = result.scalar_one()
        assert still_completed.status == "completed"


class TestRetrySchedulerTypedCategory:
    """retry_scheduler.evaluate must honour typed error categories.

    Regression: evaluate() re-derived the category from str(error); the
    BrowserExecutorError STORAGE marker matched no classifier pattern, so
    storage failures were demoted to UNKNOWN (2 retries / 30 s jitter
    instead of 1 retry / 5 m fixed).
    """

    @staticmethod
    def _job() -> DownloadJob:
        return DownloadJob(
            id=UUID("550e8400-e29b-41d4-a716-446655440099"),
            user_id=UUID("550e8400-e29b-41d4-a716-446655440098"),
            url="https://www.tiktok.com/@u/v/1",
            status="processing",
            retry_count=0,
        )

    @pytest.mark.unit
    def test_storage_typed_error_keeps_storage_category(self) -> None:
        from worker.browser_executor import BrowserExecutorError
        from worker.retry_scheduler import evaluate

        decision = evaluate(
            self._job(),
            BrowserExecutorError(category=ErrorCategory.STORAGE, signal="storage_error"),
        )
        assert decision.category == ErrorCategory.STORAGE
        assert not decision.is_final  # STORAGE allows 1 retry

    @pytest.mark.unit
    def test_blocked_typed_error_is_terminal(self) -> None:
        from worker.browser_executor import BrowserExecutorError
        from worker.retry_scheduler import evaluate

        decision = evaluate(
            self._job(),
            BrowserExecutorError(category=ErrorCategory.BLOCKED, signal="drm_detected"),
        )
        assert decision.category == ErrorCategory.BLOCKED
        assert decision.is_final

    @pytest.mark.unit
    def test_untyped_error_still_uses_string_classifier(self) -> None:
        from worker.retry_scheduler import evaluate

        # Unrecognized text → UNKNOWN via the string classifier (the typed
        # path is not involved for a plain RuntimeError).
        decision = evaluate(self._job(), RuntimeError("boom"))
        assert decision.category == ErrorCategory.UNKNOWN

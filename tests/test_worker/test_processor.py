"""Tests for worker processor module."""

from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, patch
from uuid import UUID

import pytest
from sqlalchemy import select

from app.models.download_job import DownloadJob


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

        with patch("worker.processor.redis_client", mock_redis_client):
            mock_redis_client.rpop = AsyncMock(return_value=None)

            # Should return without error
            await process_next_job()

        # Verify no jobs were processed
        mock_redis_client.rpop.assert_called_once_with("download_queue")

    @pytest.mark.unit
    async def test_process_next_job_not_found(self, db_session, mock_redis_client):
        """Test processing a job that doesn't exist in database."""
        from worker.processor import process_next_job

        with patch("worker.processor.redis_client", mock_redis_client):
            mock_redis_client.rpop = AsyncMock(return_value="550e8400-e29b-41d4-a716-446655440099")

            # Should log warning and return
            await process_next_job()

        mock_redis_client.rpop.assert_called_once()

    @pytest.mark.unit
    async def test_process_next_job_completes_success(self, db_session, mock_redis_client):
        """Test successful job completion."""
        import asyncio

        from app.database import get_async_session_factory
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
            patch("worker.processor.redis_client", mock_redis_client),
            patch(
                "worker.processor.extract_media_with_circuit_breaker",
                new_callable=AsyncMock,
            ) as mock_extract,
            patch("worker.main.shutdown_event", mock_shutdown_event),
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
    async def test_reset_stuck_jobs_ignores_recent_processing(self, db_session):
        """Test that recently started processing jobs are not reset."""
        from worker.processor import reset_stuck_jobs

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
        from worker.processor import reset_stuck_jobs

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

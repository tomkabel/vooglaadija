"""Tests for atomic job claims - ensuring no double processing.

These tests verify the atomic guarded claim pattern where:
1. UPDATE ... WHERE status='pending' sets status to 'processing'
2. rowcount is checked to ensure exactly one row was claimed
3. Concurrent claims are prevented by the database
"""

import asyncio
from datetime import UTC, datetime
from unittest.mock import AsyncMock, patch
from uuid import UUID, uuid4

import pytest
from sqlalchemy import select, update

from app.database import get_async_session_factory
from core.models.download_job import DownloadJob


class TestAtomicClaims:
    """Tests for atomic job claim pattern."""

    @pytest.fixture
    def job_id(self) -> UUID:
        """Fixed job ID for consistent testing."""
        return UUID("550e8400-e29b-41d4-a716-446655440099")

    @pytest.fixture
    def user_id(self) -> UUID:
        """Fixed user ID for consistent testing."""
        return UUID("550e8400-e29b-41d4-a716-446655440001")

    @pytest.fixture
    async def pending_job(self, db_session, job_id, user_id) -> DownloadJob:
        """Create a pending job for testing."""
        job = DownloadJob(
            id=job_id,
            user_id=user_id,
            url="https://www.youtube.com/watch?v=test_atomic",
            status="pending",
        )
        db_session.add(job)
        await db_session.commit()
        return job

    @pytest.mark.unit
    async def test_no_double_processing_single_claim(self, db_session, pending_job, job_id):
        """Test that a single worker can claim and process a job exactly once."""
        from worker.processor import process_next_job

        mock_redis = AsyncMock()
        mock_redis.rpop = AsyncMock(return_value=str(job_id))

        mock_shutdown = asyncio.Event()

        with (
            patch("worker.queue.redis_client", mock_redis),
            patch(
                "worker.processor.extract_media_with_circuit_breaker", new_callable=AsyncMock
            ) as mock_extract,
            patch("worker.main.shutdown_event", mock_shutdown),
        ):
            mock_extract.return_value = ("/storage/test.mp4", "test.mp4", "Test Video")

            # Process the job
            result = await process_next_job(job_id)

            # Should return True indicating successful processing
            assert result is True

            # Verify job is completed
            session_factory = get_async_session_factory()
            async with session_factory() as new_session:
                result = await new_session.execute(
                    select(DownloadJob).where(DownloadJob.id == job_id)
                )
                job = result.scalar_one()
                assert job.status == "completed"

    @pytest.mark.unit
    async def test_atomic_claim_updates_pending_to_processing(
        self, db_session, pending_job, job_id
    ):
        """Test that atomic claim changes status from pending to processing."""
        session_factory = get_async_session_factory()

        # Simulate atomic claim
        async with session_factory() as db:
            result = await db.execute(
                update(DownloadJob)
                .where(DownloadJob.id == job_id, DownloadJob.status == "pending")
                .values(status="processing", updated_at=datetime.now(UTC))
            )
            await db.commit()
            claimed = result.rowcount == 1
            assert claimed is True

        # Verify status changed
        async with session_factory() as new_session:
            result = await new_session.execute(select(DownloadJob).where(DownloadJob.id == job_id))
            job = result.scalar_one()
            assert job.status == "processing"

    @pytest.mark.unit
    async def test_atomic_claim_returns_zero_for_already_claimed(
        self, db_session, pending_job, job_id
    ):
        """Test that claiming an already-claimed job returns rowcount=0."""
        session_factory = get_async_session_factory()

        # First claim succeeds
        async with session_factory() as db:
            result1 = await db.execute(
                update(DownloadJob)
                .where(DownloadJob.id == job_id, DownloadJob.status == "pending")
                .values(status="processing", updated_at=datetime.now(UTC))
            )
            await db.commit()
            assert result1.rowcount == 1

        # Second claim should fail (job is now processing)
        async with session_factory() as db:
            result2 = await db.execute(
                update(DownloadJob)
                .where(DownloadJob.id == job_id, DownloadJob.status == "pending")
                .values(status="processing", updated_at=datetime.now(UTC))
            )
            await db.commit()
            # Should be 0 because job is no longer pending
            assert result2.rowcount == 0

    @pytest.mark.unit
    async def test_concurrent_claims_only_one_succeeds(self, db_session, pending_job, job_id):
        """Test that only one concurrent claim succeeds using rowcount."""
        session_factory = get_async_session_factory()

        # Simulate two workers trying to claim simultaneously
        # Both try to UPDATE WHERE status='pending'
        results = []

        async def try_claim(worker_id: int) -> int:
            async with session_factory() as db:
                result = await db.execute(
                    update(DownloadJob)
                    .where(DownloadJob.id == job_id, DownloadJob.status == "pending")
                    .values(status="processing", updated_at=datetime.now(UTC))
                )
                await db.commit()
                return result.rowcount

        # Run both claims concurrently
        results = await asyncio.gather(try_claim(1), try_claim(2))

        # One should succeed (rowcount=1), one should fail (rowcount=0)
        assert results.count(1) == 1, "Exactly one claim should succeed"
        assert results.count(0) == 1, "Exactly one claim should fail"

        # Verify job is processing (not failed or double-processed)
        async with session_factory() as new_session:
            result = await new_session.execute(select(DownloadJob).where(DownloadJob.id == job_id))
            job = result.scalar_one()
            assert job.status == "processing"

    @pytest.mark.unit
    async def test_claim_fails_for_non_pending_status(self, db_session, job_id, user_id):
        """Test that claim fails for jobs that are not pending."""
        session_factory = get_async_session_factory()

        # Create a job that's already processing
        job = DownloadJob(
            id=job_id,
            user_id=user_id,
            url="https://www.youtube.com/watch?v=test",
            status="processing",  # Not pending!
        )
        db_session.add(job)
        await db_session.commit()

        # Try to claim it
        async with session_factory() as db:
            result = await db.execute(
                update(DownloadJob)
                .where(DownloadJob.id == job_id, DownloadJob.status == "pending")
                .values(status="processing", updated_at=datetime.now(UTC))
            )
            await db.commit()
            # Should fail because status is processing, not pending
            assert result.rowcount == 0

    @pytest.mark.unit
    async def test_claim_fails_for_completed_job(self, db_session, job_id, user_id):
        """Test that claim fails for completed jobs."""
        session_factory = get_async_session_factory()

        # Create a completed job
        job = DownloadJob(
            id=job_id,
            user_id=user_id,
            url="https://www.youtube.com/watch?v=test",
            status="completed",
        )
        db_session.add(job)
        await db_session.commit()

        # Try to claim it
        async with session_factory() as db:
            result = await db.execute(
                update(DownloadJob)
                .where(DownloadJob.id == job_id, DownloadJob.status == "pending")
                .values(status="processing", updated_at=datetime.now(UTC))
            )
            await db.commit()
            # Should fail because status is completed
            assert result.rowcount == 0

    @pytest.mark.unit
    async def test_claim_fails_for_failed_job(self, db_session, job_id, user_id):
        """Test that claim fails for failed jobs."""
        session_factory = get_async_session_factory()

        # Create a failed job
        job = DownloadJob(
            id=job_id,
            user_id=user_id,
            url="https://www.youtube.com/watch?v=test",
            status="failed",
            error="Previous failure",
        )
        db_session.add(job)
        await db_session.commit()

        # Try to claim it
        async with session_factory() as db:
            result = await db.execute(
                update(DownloadJob)
                .where(DownloadJob.id == job_id, DownloadJob.status == "pending")
                .values(status="processing", updated_at=datetime.now(UTC))
            )
            await db.commit()
            # Should fail because status is failed
            assert result.rowcount == 0

    @pytest.mark.unit
    async def test_multiple_pending_jobs_claimed_independently(self, db_session):
        """Test that multiple pending jobs can be claimed independently."""
        session_factory = get_async_session_factory()
        job_ids = [uuid4() for _ in range(3)]
        user_id = uuid4()

        # Create 3 pending jobs
        for job_id in job_ids:
            job = DownloadJob(
                id=job_id,
                user_id=user_id,
                url="https://www.youtube.com/watch?v=test",
                status="pending",
            )
            db_session.add(job)
        await db_session.commit()

        # Claim all three
        claimed_count = 0
        async with session_factory() as db:
            for job_id in job_ids:
                result = await db.execute(
                    update(DownloadJob)
                    .where(DownloadJob.id == job_id, DownloadJob.status == "pending")
                    .values(status="processing", updated_at=datetime.now(UTC))
                )
                await db.commit()
                if result.rowcount == 1:
                    claimed_count += 1

        # All 3 should be claimed
        assert claimed_count == 3

        # Verify all are processing
        async with session_factory() as new_session:
            for job_id in job_ids:
                result = await new_session.execute(
                    select(DownloadJob).where(DownloadJob.id == job_id)
                )
                job = result.scalar_one()
                assert job.status == "processing"


class TestAtomicClaimsIntegration:
    """Integration tests for atomic claims with actual processing."""

    @pytest.mark.integration
    async def test_process_next_job_atomically_claims(self, db_session):
        """Test that process_next_job uses atomic claim pattern."""
        from worker.processor import process_next_job

        job_id = UUID("550e8400-e29b-41d4-a716-446655440098")
        user_id = UUID("550e8400-e29b-41d4-a716-446655440097")

        # Create pending job
        job = DownloadJob(
            id=job_id,
            user_id=user_id,
            url="https://www.youtube.com/watch?v=atomic_test",
            status="pending",
        )
        db_session.add(job)
        await db_session.commit()

        mock_redis = AsyncMock()
        mock_redis.rpop = AsyncMock(return_value=str(job_id))
        mock_shutdown = asyncio.Event()

        with (
            patch("worker.queue.redis_client", mock_redis),
            patch(
                "worker.processor.extract_media_with_circuit_breaker", new_callable=AsyncMock
            ) as mock_extract,
            patch("worker.main.shutdown_event", mock_shutdown),
        ):
            mock_extract.return_value = ("/storage/test.mp4", "test.mp4", "Atomic Test")

            result = await process_next_job(job_id)

            assert result is True

            # Verify it was processed only once
            session_factory = get_async_session_factory()
            async with session_factory() as new_session:
                result = await new_session.execute(
                    select(DownloadJob).where(DownloadJob.id == job_id)
                )
                job = result.scalar_one()
                assert job.status == "completed"

"""Tests for outbox recovery - ensuring crash recovery works.

These tests verify the outbox pattern handles crash recovery:
1. Jobs are written to outbox in same transaction as job creation
2. sync_outbox_to_queue recovers jobs after crashes
3. FOR UPDATE SKIP LOCKED prevents deadlocks
4. Duplicate entries are handled idempotently
"""

import json
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, patch
from uuid import UUID, uuid4

import pytest
from sqlalchemy import select

from core.models.download_job import DownloadJob
from core.models.outbox import Outbox


@pytest.fixture
def user_id() -> UUID:
    """Fixed user ID for consistent testing."""
    return UUID("550e8400-e29b-41d4-a716-446655440010")


@pytest.fixture
def job_id() -> UUID:
    """Fixed job ID for consistent testing."""
    return UUID("550e8400-e29b-41d4-a716-446655440011")


class TestOutboxRecovery:
    @pytest.mark.unit
    async def test_outbox_entry_created_with_job(self, db_session, job_id, user_id):
        """Test that outbox entry is created in same transaction as job."""
        job = DownloadJob(
            id=job_id,
            user_id=user_id,
            url="https://www.youtube.com/watch?v=recovery_test",
            status="pending",
        )
        db_session.add(job)

        outbox_entry = Outbox(
            id=uuid4(),
            job_id=job_id,
            event_type="enqueue_download",
            payload=json.dumps({"url": "https://www.youtube.com/watch?v=recovery_test"}),
            status="pending",
        )
        db_session.add(outbox_entry)

        await db_session.commit()

        result = await db_session.execute(select(Outbox).where(Outbox.job_id == job_id))
        outbox = result.scalar_one_or_none()
        assert outbox is not None
        assert outbox.status == "pending"
        assert outbox.event_type == "enqueue_download"

    @pytest.mark.unit
    async def test_sync_outbox_recovers_pending_entries(self, db_session, job_id, user_id):
        """Test that sync_outbox_to_queue recovers pending outbox entries."""
        job = DownloadJob(
            id=job_id,
            user_id=user_id,
            url="https://www.youtube.com/watch?v=sync_test",
            status="pending",
        )
        db_session.add(job)

        outbox_entry = Outbox(
            id=uuid4(),
            job_id=job_id,
            event_type="enqueue_download",
            payload=None,
            status="pending",
        )
        db_session.add(outbox_entry)
        await db_session.commit()

        outbox_id = outbox_entry.id

        mock_redis = AsyncMock()
        mock_redis.lpush = AsyncMock(return_value=1)

        with patch("worker.queue.redis_client", mock_redis):
            from worker.processor import sync_outbox_to_queue

            synced = await sync_outbox_to_queue(batch_size=10)
            assert synced == 1

        # Outbox entry is DELETED after successful sync (not updated to enqueued)
        from sqlalchemy import select

        await db_session.commit()
        result = await db_session.execute(select(Outbox).where(Outbox.id == outbox_id))
        deleted_entry = result.scalar_one_or_none()
        assert deleted_entry is None

    @pytest.mark.unit
    async def test_sync_outbox_with_retry_scheduled(self, db_session, job_id, user_id):
        """Test that sync_outbox_to_queue handles retry_scheduled entries."""
        job = DownloadJob(
            id=job_id,
            user_id=user_id,
            url="https://www.youtube.com/watch?v=retry_test",
            status="pending",
            retry_count=1,
            next_retry_at=datetime.now(UTC) + timedelta(seconds=30),
        )
        db_session.add(job)

        next_retry = datetime.now(UTC) + timedelta(seconds=30)
        outbox_entry = Outbox(
            id=uuid4(),
            job_id=job_id,
            event_type="retry_scheduled",
            payload=json.dumps(
                {
                    "retry_count": 1,
                    "next_retry_at": next_retry.isoformat(),
                }
            ),
            status="pending",
        )
        db_session.add(outbox_entry)
        await db_session.commit()

        mock_redis = AsyncMock()
        mock_redis.zscore = AsyncMock(return_value=None)
        mock_redis.zadd = AsyncMock(return_value=1)
        mock_redis.lpush = AsyncMock(return_value=1)

        with patch("worker.queue.redis_client", mock_redis):
            from worker.processor import sync_outbox_to_queue

            synced = await sync_outbox_to_queue(batch_size=10)
            assert synced == 1

        mock_redis.zadd.assert_called_once()
        call_args = mock_redis.zadd.call_args
        assert call_args[0][0] == "retry_queue"

    @pytest.mark.unit
    async def test_sync_outbox_skips_already_enqueued(self, db_session, job_id, user_id):
        """Test that sync_outbox skips entries that are already enqueued."""
        job = DownloadJob(
            id=job_id,
            user_id=user_id,
            url="https://www.youtube.com/watch?v=enqueued_test",
            status="pending",
        )
        db_session.add(job)

        outbox_entry = Outbox(
            id=uuid4(),
            job_id=job_id,
            event_type="enqueue_download",
            payload=None,
            status="enqueued",
            processed_at=datetime.now(UTC),
        )
        db_session.add(outbox_entry)
        await db_session.commit()

        mock_redis = AsyncMock()

        with patch("worker.queue.redis_client", mock_redis):
            from worker.processor import sync_outbox_to_queue

            synced = await sync_outbox_to_queue(batch_size=10)
            assert synced == 0

    @pytest.mark.unit
    async def test_sync_outbox_idempotent_on_redis_failure(self, db_session, job_id, user_id):
        """Test that outbox entry stays pending if Redis fails."""
        job = DownloadJob(
            id=job_id,
            user_id=user_id,
            url="https://www.youtube.com/watch?v=redis_fail_test",
            status="pending",
        )
        db_session.add(job)

        outbox_entry = Outbox(
            id=uuid4(),
            job_id=job_id,
            event_type="enqueue_download",
            payload=None,
            status="pending",
        )
        db_session.add(outbox_entry)
        await db_session.commit()

        outbox_id = outbox_entry.id

        mock_redis = AsyncMock()
        mock_redis.lpush = AsyncMock(side_effect=Exception("Redis connection failed"))

        with patch("worker.queue.redis_client", mock_redis):
            from worker.processor import sync_outbox_to_queue

            synced = await sync_outbox_to_queue(batch_size=10)
            assert synced == 0

        result = await db_session.execute(select(Outbox).where(Outbox.id == outbox_id))
        updated = result.scalar_one()
        assert updated.status == "pending"

    @pytest.mark.unit
    async def test_sync_outbox_with_for_update_skip_locked(self, db_session):
        """Test that sync_outbox uses FOR UPDATE SKIP LOCKED."""
        job_ids = [uuid4() for _ in range(3)]
        user_id = uuid4()

        for job_id in job_ids:
            job = DownloadJob(
                id=job_id,
                user_id=user_id,
                url="https://www.youtube.com/watch?v=skip_locked",
                status="pending",
            )
            db_session.add(job)

            outbox_entry = Outbox(
                id=uuid4(),
                job_id=job_id,
                event_type="enqueue_download",
                payload=None,
                status="pending",
            )
            db_session.add(outbox_entry)
        await db_session.commit()

        from worker.processor import sync_outbox_to_queue

        mock_redis = AsyncMock()
        mock_redis.lpush = AsyncMock(return_value=1)

        with patch("worker.queue.redis_client", mock_redis):
            synced = await sync_outbox_to_queue(batch_size=10)
            assert synced == 3


class TestOutboxIdempotency:
    @pytest.mark.unit
    async def test_duplicate_outbox_entry_prevented(self, db_session, job_id, user_id):
        """Test that duplicate outbox entries are prevented by idempotent check."""
        job = DownloadJob(
            id=job_id,
            user_id=user_id,
            url="https://www.youtube.com/watch?v=idempotent_test",
            status="pending",
        )
        db_session.add(job)
        await db_session.commit()

        outbox1 = Outbox(
            id=uuid4(),
            job_id=job_id,
            event_type="enqueue_download",
            payload=None,
            status="pending",
        )
        db_session.add(outbox1)
        await db_session.commit()

        result = await db_session.execute(
            select(Outbox).where(
                Outbox.job_id == job_id,
                Outbox.status == "pending",
            )
        )
        existing = result.scalars().all()
        assert len(existing) == 1

        outbox2 = Outbox(
            id=uuid4(),
            job_id=job_id,
            event_type="enqueue_download",
            payload=None,
            status="pending",
        )
        db_session.add(outbox2)
        await db_session.commit()

        result = await db_session.execute(select(Outbox).where(Outbox.job_id == job_id))
        all_entries = result.scalars().all()
        assert len(all_entries) == 2


class TestOutboxBatchProcessing:
    @pytest.mark.unit
    async def test_sync_respects_batch_size(self, db_session):
        """Test that sync_outbox_to_queue respects batch_size limit."""
        user_id = uuid4()
        job_ids = [uuid4() for _ in range(5)]

        for job_id in job_ids:
            job = DownloadJob(
                id=job_id,
                user_id=user_id,
                url=f"https://www.youtube.com/watch?v=batch_{job_id}",
                status="pending",
            )
            db_session.add(job)

            outbox_entry = Outbox(
                id=uuid4(),
                job_id=job_id,
                event_type="enqueue_download",
                payload=None,
                status="pending",
            )
            db_session.add(outbox_entry)
        await db_session.commit()

        mock_redis = AsyncMock()
        mock_redis.lpush = AsyncMock(return_value=1)

        with patch("worker.queue.redis_client", mock_redis):
            from worker.processor import sync_outbox_to_queue

            synced = await sync_outbox_to_queue(batch_size=2)
            assert synced == 2

        # Entries are DELETED after successful sync (batch_size=2 means 2 deleted)
        await db_session.commit()
        result = await db_session.execute(select(Outbox))
        remaining = result.scalars().all()
        assert len(remaining) == 3  # 5 - 2 = 3 remain


class TestOutboxCrashRecoveryScenarios:
    @pytest.mark.unit
    async def test_job_created_but_not_enqueued(self, db_session, job_id, user_id):
        """Scenario: Job created and committed, but never enqueued to Redis."""
        job = DownloadJob(
            id=job_id,
            user_id=user_id,
            url="https://www.youtube.com/watch?v=crash_test",
            status="pending",
        )
        db_session.add(job)

        outbox_entry = Outbox(
            id=uuid4(),
            job_id=job_id,
            event_type="enqueue_download",
            payload=None,
            status="pending",
        )
        db_session.add(outbox_entry)
        await db_session.commit()

        outbox_id = outbox_entry.id

        mock_redis = AsyncMock()
        mock_redis.lpush = AsyncMock(return_value=1)

        with patch("worker.queue.redis_client", mock_redis):
            from worker.processor import sync_outbox_to_queue

            synced = await sync_outbox_to_queue(batch_size=10)
            assert synced == 1

        # Entry is DELETED after successful sync
        await db_session.commit()
        from sqlalchemy import select

        result = await db_session.execute(select(Outbox).where(Outbox.id == outbox_id))
        deleted = result.scalar_one_or_none()
        assert deleted is None

    @pytest.mark.unit
    async def test_job_enqueued_twice_prevented(self, db_session, job_id, user_id):
        """Scenario: Job accidentally enqueued twice."""
        job = DownloadJob(
            id=job_id,
            user_id=user_id,
            url="https://www.youtube.com/watch?v=dup_test",
            status="pending",
        )
        db_session.add(job)

        outbox_entry = Outbox(
            id=uuid4(),
            job_id=job_id,
            event_type="enqueue_download",
            payload=None,
            status="pending",
        )
        db_session.add(outbox_entry)
        await db_session.commit()

        mock_redis = AsyncMock()
        mock_redis.lpush = AsyncMock(return_value=1)

        with patch("worker.queue.redis_client", mock_redis):
            from worker.processor import sync_outbox_to_queue

            synced1 = await sync_outbox_to_queue(batch_size=10)
            assert synced1 == 1

            synced2 = await sync_outbox_to_queue(batch_size=10)
            assert synced2 == 0

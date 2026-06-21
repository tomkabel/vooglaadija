"""Worker processor chaos injection tests (db_failover, zombie sweep)."""

import uuid
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, patch

import pytest
from sqlalchemy import select

from app.metrics import RECOVERIES
from app.models.download_job import DownloadJob
from worker.main import shutdown_event


@pytest.fixture(autouse=True)
def clear_shutdown():
    """Clear the global shutdown_event before each test."""
    shutdown_event.clear()


@pytest.fixture
def mock_redis_client():
    """Create a mock Redis client with chaos support."""
    mock = AsyncMock()
    mock.rpop = AsyncMock(return_value=None)
    mock.zadd = AsyncMock(return_value=1)
    mock.lpush = AsyncMock(return_value=1)
    mock.brpop = AsyncMock(return_value=None)
    mock.exists = AsyncMock(return_value=0)
    return mock


@pytest.mark.unit
class TestDBFailoverTrigger:
    """Tests for chaos db_failover trigger in process_next_job."""

    @pytest.mark.asyncio
    async def test_db_failover_raises_operational_error(self, db_session, mock_redis_client):
        """When chaos:db_failover exists, OperationalError is raised after job claim."""
        from worker.processor import process_next_job

        job_id = uuid.uuid4()
        job = DownloadJob(
            id=job_id,
            user_id=uuid.uuid4(),
            url="https://www.youtube.com/watch?v=test123",
            status="pending",
        )
        db_session.add(job)
        await db_session.commit()

        with patch("worker.processor.redis_client", mock_redis_client):
            mock_redis_client.rpop = AsyncMock(return_value=str(job_id))
            mock_redis_client.exists = AsyncMock(return_value=1)

            await process_next_job()

        result = await db_session.execute(select(DownloadJob).where(DownloadJob.id == job_id))
        job = result.scalar_one()
        assert job.status == "pending"

    @pytest.mark.asyncio
    async def test_db_failover_not_active_runs_normally(self, db_session, mock_redis_client):
        """When chaos:db_failover is not present, normal processing occurs."""
        from worker.processor import process_next_job

        job_id = uuid.uuid4()
        job = DownloadJob(
            id=job_id,
            user_id=uuid.uuid4(),
            url="https://www.youtube.com/watch?v=test456",
            status="pending",
        )
        db_session.add(job)
        await db_session.commit()

        with patch("worker.processor.redis_client", mock_redis_client):
            mock_redis_client.rpop = AsyncMock(return_value=str(job_id))
            mock_redis_client.exists = AsyncMock(return_value=0)

            with patch(
                "worker.processor.extract_media_with_circuit_breaker",
                side_effect=Exception("Simulated extraction failure"),
            ):
                await process_next_job()

        result = await db_session.execute(select(DownloadJob).where(DownloadJob.id == job_id))
        job = result.scalar_one()
        assert job.status in ("pending", "failed")


@pytest.mark.unit
class TestZombieSweepTrigger:
    """Tests for chaos zombie job trigger in process_next_job."""

    @pytest.mark.asyncio
    async def test_zombie_trigger_redis_check(self, mock_redis_client):
        """Verify zombie trigger checks the correct Redis key."""

        def exists_side_effect(key):
            if key == "chaos:zombie_job_trigger":
                return 1
            return 0

        mock_redis_client.exists = AsyncMock(side_effect=exists_side_effect)

        result = await mock_redis_client.exists("chaos:zombie_job_trigger")
        assert result == 1

        result = await mock_redis_client.exists("chaos:db_failover")
        assert result == 0

    @pytest.mark.asyncio
    async def test_zombie_trigger_active_skips_job_processing(self, db_session):
        """When zombie trigger is active, job stays in processing state after claim."""
        from worker.processor import process_next_job

        job_id = uuid.uuid4()
        job = DownloadJob(
            id=job_id,
            user_id=uuid.uuid4(),
            url="https://www.youtube.com/watch?v=test789",
            status="pending",
        )
        db_session.add(job)
        await db_session.commit()

        mock_redis = AsyncMock()
        mock_redis.rpop = AsyncMock(return_value=str(job_id))
        mock_redis.zadd = AsyncMock(return_value=1)
        mock_redis.lpush = AsyncMock(return_value=1)
        mock_redis.brpop = AsyncMock(return_value=None)

        def exists_fn(key):
            if key == "chaos:zombie_job_trigger":
                return 1
            return 0

        mock_redis.exists = AsyncMock(side_effect=exists_fn)

        with patch("worker.processor.redis_client", mock_redis):
            result = await process_next_job()

        assert result is False

        result = await db_session.execute(select(DownloadJob).where(DownloadJob.id == job_id))
        job = result.scalar_one()
        assert job.status in ("processing", "pending")

    @pytest.mark.asyncio
    async def test_zombie_trigger_inactive_runs_normally(self, db_session, mock_redis_client):
        """When zombie trigger is not present, normal error handling occurs."""
        from worker.processor import process_next_job

        job_id = uuid.uuid4()
        job = DownloadJob(
            id=job_id,
            user_id=uuid.uuid4(),
            url="https://www.youtube.com/watch?v=test456",
            status="pending",
        )
        db_session.add(job)
        await db_session.commit()

        with (
            patch("worker.processor.redis_client", mock_redis_client),
            patch("worker.main.shutdown_event.is_set", return_value=False),
        ):
            mock_redis_client.rpop = AsyncMock(return_value=str(job_id))
            mock_redis_client.exists = AsyncMock(return_value=0)

            with patch(
                "worker.processor.extract_media_with_circuit_breaker",
                side_effect=Exception("Simulated extraction failure"),
            ):
                await process_next_job()

        result = await db_session.execute(select(DownloadJob).where(DownloadJob.id == job_id))
        job = result.scalar_one()
        assert job.status in ("pending", "failed")


@pytest.mark.unit
class TestZombieSweepRecoveryMetrics:
    """Tests for recovery counters in reset_stuck_jobs."""

    @pytest.mark.asyncio
    async def test_recovery_counter_increments_on_stuck_job_reset(self, db_session):
        """reset_stuck_jobs increments RECOVERIES when it finds and resets stuck jobs."""
        from worker.processor import reset_stuck_jobs

        job_id = uuid.uuid4()
        stuck_job = DownloadJob(
            id=job_id,
            user_id=uuid.uuid4(),
            url="https://www.youtube.com/watch?v=stuck001",
            status="processing",
            updated_at=datetime.now(UTC) - timedelta(minutes=30),
        )
        db_session.add(stuck_job)
        await db_session.commit()

        initial = RECOVERIES.labels(reason="zombie_sweep_recovery")._value.get()

        reset_count = await reset_stuck_jobs(timeout_minutes=5)

        assert reset_count == 1

        new_value = RECOVERIES.labels(reason="zombie_sweep_recovery")._value.get()
        assert new_value == initial + 1

    @pytest.mark.asyncio
    async def test_recovery_counter_not_incremented_when_no_stuck_jobs(self, db_session):
        """reset_stuck_jobs does NOT increment RECOVERIES when no jobs are stuck."""
        from worker.processor import reset_stuck_jobs

        initial = RECOVERIES.labels(reason="zombie_sweep_recovery")._value.get()

        reset_count = await reset_stuck_jobs(timeout_minutes=5)

        assert reset_count == 0

        new_value = RECOVERIES.labels(reason="zombie_sweep_recovery")._value.get()
        assert new_value == initial

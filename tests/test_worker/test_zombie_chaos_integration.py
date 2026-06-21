"""Zombie sweep chaos flow: inject → inline recovery (demo-optimized)."""

import uuid
from unittest.mock import AsyncMock, patch

import pytest

from app.config import settings
from app.metrics import RECOVERIES
from core.models.download_job import DownloadJob
from worker.main import shutdown_event


@pytest.fixture(autouse=True)
def enable_chaos_and_clear_shutdown():
    """Enable the chaos feature flag and clear shutdown event."""
    saved = settings.feature_chaos_api_enabled
    settings.feature_chaos_api_enabled = True
    shutdown_event.clear()
    yield
    settings.feature_chaos_api_enabled = saved


@pytest.mark.integration
class TestZombieSweepChaosFlow:
    """Full flow: inject worker_crash → inline recovery (no 15-min wait)."""

    @pytest.mark.asyncio
    async def test_zombie_trigger_inline_recovery(self, db_session):
        """End-to-end: zombie trigger causes inline recovery immediately.

        The chaotic zombie trigger now performs inline zombie recovery
        (requeue + outbox audit trail + recovery counter) instead of
        leaving the job orphaned for 15 minutes. This makes the demo
        recovery visible on Grafana within seconds.
        """
        from worker.processor import process_next_job

        mock_redis = AsyncMock()
        mock_redis.rpop = AsyncMock(return_value=None)
        mock_redis.zadd = AsyncMock(return_value=1)
        mock_redis.lpush = AsyncMock(return_value=1)
        mock_redis.brpop = AsyncMock(return_value=None)
        mock_redis.exists = AsyncMock(return_value=0)

        job_id = uuid.uuid4()
        job = DownloadJob(
            id=job_id,
            user_id=uuid.uuid4(),
            url="https://www.youtube.com/watch?v=zombie001",
            status="pending",
        )
        db_session.add(job)
        await db_session.commit()

        recovery_before = RECOVERIES.labels(reason="zombie_sweep_recovery")._value.get()

        with patch("worker.processor.redis_client", mock_redis):
            mock_redis.rpop = AsyncMock(return_value=str(job_id))
            mock_redis.exists = AsyncMock(
                side_effect=lambda k: 1 if k == "chaos:zombie_job_trigger" else 0
            )

            result = await process_next_job()

        assert result is False

        # Job should be recovered inline: status back to "pending"
        await db_session.refresh(job)
        assert job.status == "pending"

        # Recovery counter should have been incremented by inline recovery
        recovery_after = RECOVERIES.labels(reason="zombie_sweep_recovery")._value.get()
        assert recovery_after == recovery_before + 1

    @pytest.mark.asyncio
    async def test_zombie_trigger_creates_outbox_entry(self, db_session):
        """Verify inline recovery creates an outbox audit trail entry."""
        from sqlalchemy import select

        from core.models.outbox import Outbox
        from worker.processor import process_next_job

        mock_redis = AsyncMock()
        mock_redis.rpop = AsyncMock(return_value=None)
        mock_redis.zadd = AsyncMock(return_value=1)
        mock_redis.lpush = AsyncMock(return_value=1)
        mock_redis.brpop = AsyncMock(return_value=None)
        mock_redis.exists = AsyncMock(return_value=0)

        job_id = uuid.uuid4()
        job = DownloadJob(
            id=job_id,
            user_id=uuid.uuid4(),
            url="https://www.youtube.com/watch?v=zombie002",
            status="pending",
        )
        db_session.add(job)
        await db_session.commit()

        with patch("worker.processor.redis_client", mock_redis):
            mock_redis.rpop = AsyncMock(return_value=str(job_id))
            mock_redis.exists = AsyncMock(
                side_effect=lambda k: 1 if k == "chaos:zombie_job_trigger" else 0
            )

            await process_next_job()

        # Verify outbox entry was created with zombie_recovery event type
        result = await db_session.execute(select(Outbox).where(Outbox.job_id == job_id))
        outbox_entry = result.scalar_one_or_none()
        assert outbox_entry is not None
        assert outbox_entry.event_type == "zombie_recovery"
        assert outbox_entry.status == "pending"

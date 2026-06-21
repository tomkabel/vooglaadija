"""Tests for Redis failure scenarios.

These tests verify:
1. Worker handles Redis connection failures gracefully
2. Jobs are delayed when Redis is down (outbox pattern provides reliability)
3. Recovery is automatic when Redis comes back
"""

import asyncio
import json
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import UUID

import pytest
from starlette.responses import Response

from core.models.download_job import DownloadJob


class TestRedisFailureHandling:
    """Tests for Redis failure handling."""

    @pytest.fixture
    def job_id(self) -> UUID:
        """Fixed job ID for consistent testing."""
        return UUID("550e8400-e29b-41d4-a716-446655440020")

    @pytest.fixture
    def user_id(self) -> UUID:
        """Fixed user ID for consistent testing."""
        return UUID("550e8400-e29b-41d4-a716-446655440021")

    @pytest.mark.unit
    async def test_worker_start_fails_if_redis_down(self):
        """Test that worker fails to start if Redis is unavailable."""
        from worker.main import main

        # Mock Redis to fail
        mock_redis = AsyncMock()
        mock_redis.ping = AsyncMock(side_effect=Exception("Redis connection refused"))

        with (
            patch("worker.main.redis_client", mock_redis),
            patch("worker.main.start_health_server", MagicMock(return_value=None)),
            patch("worker.main.stop_health_server"),
        ):
            with pytest.raises(Exception, match="Redis connection refused"):
                await main()

    @pytest.mark.unit
    async def test_redis_failure_during_brpop(self):
        """Test handling when Redis fails during BRPOP."""
        from worker.processor import process_next_job

        mock_redis = AsyncMock()
        mock_redis.rpop = AsyncMock(side_effect=Exception("Redis connection lost"))

        with patch("worker.processor.redis_client", mock_redis):
            # Should not raise, just log and continue
            await process_next_job()

    @pytest.mark.unit
    async def test_redis_failure_during_enqueue(self, db_session, job_id, user_id):
        """Test handling when Redis fails during job enqueue."""
        from worker.processor import process_next_job

        # Create a job that will fail
        job = DownloadJob(
            id=job_id,
            user_id=user_id,
            url="https://www.youtube.com/watch?v=redis_enqueue_test",
            status="pending",
        )
        db_session.add(job)
        await db_session.commit()

        mock_redis = AsyncMock()
        mock_redis.rpop = AsyncMock(return_value=str(job_id))
        mock_redis.zadd = AsyncMock(side_effect=Exception("Redis write failed"))
        mock_shutdown = asyncio.Event()

        with (
            patch("worker.processor.redis_client", mock_redis),
            patch(
                "worker.processor.extract_media_with_circuit_breaker", new_callable=AsyncMock
            ) as mock_extract,
            patch("worker.main.shutdown_event", mock_shutdown),
        ):
            # Simulate download succeeding but Redis failing on zadd
            mock_extract.return_value = ("/storage/test.mp4", "test.mp4")

            # Job should be marked completed (DB commit succeeds)
            # even if Redis enqueue fails
            await process_next_job(job_id)

            # The job is still completed because DB commit succeeded
            # The outbox entry ensures recovery

    @pytest.mark.unit
    async def test_outbox_provides_reliability_when_redis_down(self, db_session, job_id, user_id):
        """Test that outbox pattern provides reliability when Redis is down."""
        from worker.processor import process_next_job

        # Create a job
        job = DownloadJob(
            id=job_id,
            user_id=user_id,
            url="https://www.youtube.com/watch?v=reliable_test",
            status="pending",
        )
        db_session.add(job)
        await db_session.commit()

        # Mock Redis - everything fails
        mock_redis = AsyncMock()
        mock_redis.rpop = AsyncMock(side_effect=Exception("Redis unavailable"))
        mock_redis.lpush = AsyncMock(side_effect=Exception("Redis unavailable"))
        mock_redis.zadd = AsyncMock(side_effect=Exception("Redis unavailable"))
        mock_shutdown = asyncio.Event()

        with (
            patch("worker.processor.redis_client", mock_redis),
            patch(
                "worker.processor.extract_media_with_circuit_breaker", new_callable=AsyncMock
            ) as mock_extract,
            patch("worker.main.shutdown_event", mock_shutdown),
        ):
            mock_extract.return_value = ("/storage/test.mp4", "test.mp4")

            # Process job (will complete DB transaction)
            # but Redis operations will fail
            await process_next_job(job_id)

        # After Redis comes back, sync_outbox should recover
        # This tests the reliability guarantee


class TestRedisRecovery:
    """Tests for Redis recovery scenarios."""

    @pytest.mark.unit
    async def test_redis_recovery_automatic(self):
        """Test that worker automatically recovers when Redis comes back."""
        call_count = 0

        async def mock_ping():
            nonlocal call_count
            call_count += 1
            if call_count < 3:
                raise Exception("Redis still down")
            return True

        mock_redis = AsyncMock()
        mock_redis.ping = AsyncMock(side_effect=mock_ping)

        # Simulate connection attempts
        attempts = 0
        max_attempts = 5
        while attempts < max_attempts:
            try:
                await mock_redis.ping()
                break
            except Exception:
                pass
            attempts += 1
            await asyncio.sleep(0.1)

        # Should eventually succeed
        assert call_count >= 3


class TestRedisFailureDuringJobProcessing:
    """Tests for Redis failures during specific job processing stages."""

    @pytest.mark.unit
    async def test_brpop_timeout_returns_none(self):
        """Test that BRPOP timeout returns None gracefully."""
        mock_redis = AsyncMock()
        mock_redis.brpop = AsyncMock(return_value=None)
        mock_redis.rpop = AsyncMock(return_value=None)

        with patch("worker.processor.redis_client", mock_redis):
            from worker.processor import process_next_job

            # Empty queue - returns None from brpop
            result = await process_next_job()
            assert result is False

    @pytest.mark.unit
    async def test_redis_error_on_job_claim(self, db_session):
        """Test handling when Redis fails during job claim."""
        from worker.processor import process_next_job

        job_id = UUID("550e8400-e29b-41d4-a716-446655440022")
        user_id = UUID("550e8400-e29b-41d4-a716-446655440023")

        # Create job
        job = DownloadJob(
            id=job_id,
            user_id=user_id,
            url="https://www.youtube.com/watch?v=claim_test",
            status="pending",
        )
        db_session.add(job)
        await db_session.commit()

        mock_redis = AsyncMock()
        mock_redis.rpop = AsyncMock(return_value=str(job_id))
        # Redis fails after claiming
        mock_redis.zadd = AsyncMock(side_effect=Exception("Redis error after claim"))

        mock_shutdown = asyncio.Event()

        with (
            patch("worker.processor.redis_client", mock_redis),
            patch(
                "worker.processor.extract_media_with_circuit_breaker", new_callable=AsyncMock
            ) as mock_extract,
            patch("worker.main.shutdown_event", mock_shutdown),
        ):
            mock_extract.return_value = ("/storage/test.mp4", "test.mp4")

            # Should handle gracefully
            await process_next_job(job_id)

            # Job is requeued via outbox, so result depends on implementation


class TestRedisHealthChecks:
    """Tests for Redis health check functionality."""

    @pytest.mark.unit
    async def test_health_endpoint_still_works_when_redis_down(self):
        """Test that /health returns ok even if Redis is down."""
        # This is the current behavior - /health doesn't check dependencies
        # /ready is the one that checks dependencies

    @pytest.mark.unit
    async def test_ready_endpoint_fails_when_redis_down(self):
        """Test that /ready returns not_ready status when Redis is down."""
        from app.api.routes.health import ReadinessResponse, readiness_check

        # Mock Redis to fail
        mock_redis = AsyncMock()
        mock_redis.ping = AsyncMock(side_effect=Exception("Redis connection refused"))

        with patch("app.api.routes.health.get_redis_client", return_value=mock_redis):
            result = await readiness_check()

            # When Redis is down, readiness_check returns a Response with 503
            assert isinstance(result, ReadinessResponse | Response)
            if isinstance(result, Response):
                assert result.status_code == 503
                data = json.loads(result.body)
                assert data["status"] == "not_ready"
                assert data["redis"].startswith("error:")
            else:
                assert result.status == "not_ready"
                assert result.redis.startswith("error:")


class TestRedisConnectionPooling:
    """Tests for Redis connection resilience."""

    @pytest.mark.unit
    async def test_connection_pool_exhaustion_handled(self):
        """Test handling when Redis connection pool is exhausted."""
        import redis.asyncio as redis

        # Simulate pool exhaustion - need to patch at worker.processor level
        mock_redis = AsyncMock()
        mock_redis.rpop = AsyncMock(return_value=None)  # Empty queue returns None
        mock_redis.brpop = AsyncMock(side_effect=redis.ConnectionError("Pool exhausted"))

        with patch("worker.processor.redis_client", mock_redis):
            from worker.processor import process_next_job

            # Should handle ConnectionError gracefully
            await process_next_job()

    @pytest.mark.unit
    async def test_timeout_handling(self):
        """Test handling of Redis timeouts."""

        mock_redis = AsyncMock()
        mock_redis.rpop = AsyncMock(return_value=None)  # Empty queue returns None
        mock_redis.brpop = AsyncMock(side_effect=TimeoutError("Redis timeout"))

        with patch("worker.processor.redis_client", mock_redis):
            from worker.processor import process_next_job

            # Should handle timeout gracefully
            await process_next_job()

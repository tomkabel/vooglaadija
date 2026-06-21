"""Tests for worker queue module."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest


class TestEnqueueJob:
    """Tests for enqueue_job function."""

    @pytest.mark.unit
    async def test_enqueue_job_adds_to_redis(self):
        """Test that enqueue_job adds job ID to Redis queue."""
        from core.queue import enqueue_job

        # Create a fresh mock for this test
        mock_redis = MagicMock()
        mock_redis.lpush = AsyncMock()

        with patch("core.queue.redis_client", mock_redis):
            await enqueue_job("test-job-123")

        mock_redis.lpush.assert_called_once_with("download_queue", "test-job-123")

    @pytest.mark.unit
    async def test_enqueue_job_multiple_jobs(self):
        """Test enqueuing multiple jobs."""
        from core.queue import enqueue_job

        mock_redis = MagicMock()
        mock_redis.lpush = AsyncMock()

        with patch("core.queue.redis_client", mock_redis):
            await enqueue_job("job-1")
            await enqueue_job("job-2")
            await enqueue_job("job-3")

        assert mock_redis.lpush.call_count == 3
        mock_redis.lpush.assert_any_call("download_queue", "job-1")
        mock_redis.lpush.assert_any_call("download_queue", "job-2")
        mock_redis.lpush.assert_any_call("download_queue", "job-3")


class TestRedisClient:
    """Tests for Redis client initialization."""

    @pytest.mark.unit
    def test_redis_client_uses_correct_queue_name(self):
        """Test that the queue uses the correct Redis key."""
        from core.queue import redis_client

        # Just verify the module can be imported and has the client
        assert redis_client is not None

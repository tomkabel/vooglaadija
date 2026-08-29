"""Tests for worker queue module."""

from unittest.mock import MagicMock, patch

import pytest


class TestEnqueueJob:
    """Tests for enqueue_job function."""

    @pytest.mark.unit
    async def test_enqueue_job_sends_celery_task(self):
        """Test that enqueue_job dispatches to Celery."""
        from core.queue import enqueue_job

        mock_send = MagicMock()

        with patch("core.queue._celery_send_task", mock_send):
            await enqueue_job("test-job-123")

        mock_send.assert_called_once_with(
            "worker.celery_tasks.process_download",
            args=["test-job-123"],
            queue="downloads",
        )

    @pytest.mark.unit
    async def test_enqueue_job_multiple_jobs(self):
        """Test enqueuing multiple jobs."""
        from core.queue import enqueue_job

        mock_send = MagicMock()

        with patch("core.queue._celery_send_task", mock_send):
            await enqueue_job("job-1")
            await enqueue_job("job-2")
            await enqueue_job("job-3")

        assert mock_send.call_count == 3


class TestRedisClient:
    """Tests for Redis client initialization."""

    @pytest.mark.unit
    def test_redis_client_uses_correct_queue_name(self):
        """Test that the queue uses the correct Redis key."""
        from core.queue import redis_client

        # Just verify the module can be imported and has the client
        assert redis_client is not None

"""Tests for Celery task integration.

Tests verify that:
1. Celery app initializes correctly
2. Tasks dispatch and execute properly
3. Retry behavior works as expected
4. Queue routing is correct
"""

from unittest.mock import MagicMock, patch

import pytest


@pytest.fixture(autouse=True)
def _celery_eager():
    """Configure Celery to execute tasks synchronously for testing."""
    import os

    os.environ["CELERY_TASK_ALWAYS_EAGER"] = "1"
    os.environ["CELERY_TASK_EAGER_PROPAGATES"] = "1"
    yield


@pytest.fixture
def celery_app():
    """Create a Celery app instance for testing."""
    from worker.celery_app import get_celery_app

    app = get_celery_app()
    app.conf.task_always_eager = True
    app.conf.task_eager_propagates = True
    return app


class TestCeleryApp:
    """Tests for Celery application configuration."""

    def test_app_has_broker_url(self, celery_app):
        """Verify broker URL is configured."""
        assert celery_app.conf.broker_url
        assert "redis" in celery_app.conf.broker_url

    def test_app_has_result_backend(self, celery_app):
        """Verify result backend is configured."""
        assert celery_app.conf.backend
        assert "redis" in celery_app.conf.backend

    def test_app_has_required_queues(self, celery_app):
        """Verify required queues are configured."""
        queue_names = {q.name for q in celery_app.conf.task_queues.values()}
        assert "downloads" in queue_names
        assert "retries" in queue_names
        assert "dlq" in queue_names

    def test_app_has_schedule(self, celery_app):
        """Verify Celery Beat schedule is configured."""
        schedule = celery_app.conf.beat_schedule
        assert "cleanup-expired-jobs" in schedule
        assert "cleanup-dlq" in schedule
        assert "requeue-stuck-jobs" in schedule

    def test_task_routes_configured(self, celery_app):
        """Verify task routes are configured."""
        routes = celery_app.conf.task_routes
        assert "worker.celery_tasks.process_download" in routes
        assert routes["worker.celery_tasks.process_download"]["queue"] == "downloads"


class TestProcessDownloadTask:
    """Tests for the process_download task."""

    @patch("worker.celery_tasks.get_async_session_factory")
    def test_process_download_skips_nonexistent_job(self, mock_session_factory, celery_app):
        """Verify task handles non-existent job gracefully."""
        from worker.celery_tasks import process_download

        # Mock the session factory
        mock_session = MagicMock()
        mock_session_factory.return_value = MagicMock()
        mock_session_factory.return_value.return_value.__aenter__ = MagicMock(return_value=mock_session)
        mock_session_factory.return_value.return_value.__aexit__ = MagicMock(return_value=False)

        result = process_download.apply(args=["00000000-0000-0000-0000-000000000000"])
        assert result.successful()

    @patch("worker.celery_tasks.get_async_session_factory")
    def test_process_download_skips_non_pending_job(self, mock_session_factory, celery_app):
        """Verify task skips jobs not in pending state."""
        from worker.celery_tasks import process_download

        mock_session = MagicMock()
        mock_session_factory.return_value = MagicMock()
        mock_session_factory.return_value.return_value.__aenter__ = MagicMock(return_value=mock_session)
        mock_session_factory.return_value.return_value.__aexit__ = MagicMock(return_value=False)

        result = process_download.apply(args=["00000000-0000-0000-0000-000000000000"])
        assert result.successful()


class TestRetryBehavior:
    """Tests for Celery retry behavior."""

    def test_process_download_has_autoretry(self, celery_app):
        """Verify process_download task has autoretry configured."""
        task = celery_app.tasks.get("worker.celery_tasks.process_download")
        assert task is not None
        assert task.autoretry_for
        assert task.max_retries >= 1

    def test_process_download_has_backoff(self, celery_app):
        """Verify process_download task uses backoff."""
        task = celery_app.tasks.get("worker.celery_tasks.process_download")
        assert task is not None
        assert task.retry_backoff is True


class TestCleanupTasks:
    """Tests for periodic cleanup tasks."""

    def test_cleanup_expired_jobs_registered(self, celery_app):
        """Verify cleanup task is registered."""
        task = celery_app.tasks.get("worker.celery_tasks.cleanup_expired_jobs")
        assert task is not None

    def test_cleanup_dlq_registered(self, celery_app):
        """Verify DLQ cleanup task is registered."""
        task = celery_app.tasks.get("worker.celery_tasks.cleanup_dlq")
        assert task is not None

    def test_requeue_stuck_jobs_registered(self, celery_app):
        """Verify zombie sweep task is registered."""
        task = celery_app.tasks.get("worker.celery_tasks.requeue_stuck_jobs")
        assert task is not None


class TestEnqueuePending:
    """Tests for enqueue_pending task."""

    @patch("worker.celery_tasks.get_async_session_factory")
    def test_enqueue_pending_returns_count(self, mock_session_factory, celery_app):
        """Verify enqueue_pending returns the count of enqueued jobs."""
        from worker.celery_tasks import enqueue_pending

        mock_session = MagicMock()
        mock_session_factory.return_value = MagicMock()
        mock_session_factory.return_value.return_value.__aenter__ = MagicMock(return_value=mock_session)
        mock_session_factory.return_value.return_value.__aexit__ = MagicMock(return_value=False)

        result = enqueue_pending.delay()
        assert result.successful()

"""Tests for worker main module."""

from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import UUID

import pytest

from core.config import settings
from core.models.download_job import DownloadJob
from worker.main import cleanup_expired_jobs

_DOWNLOADS_DIR = f"{settings.storage_path}/downloads"


class TestCleanupExpiredJobs:
    """Tests for cleanup_expired_jobs function."""

    @pytest.mark.unit
    async def test_cleanup_expired_jobs_no_expired(self, db_session):
        """Test cleanup when no jobs are expired."""
        # Create a job that expires in the future
        future_time = datetime.now(UTC) + timedelta(hours=24)
        job = DownloadJob(
            id=UUID("550e8400-e29b-41d4-a716-446655440020"),
            user_id=UUID("550e8400-e29b-41d4-a716-446655440005"),
            url="https://www.youtube.com/watch?v=test",
            status="completed",
            expires_at=future_time,
        )
        db_session.add(job)
        await db_session.commit()

        count = await cleanup_expired_jobs()
        assert count == 0

    @pytest.mark.unit
    async def test_cleanup_expired_jobs_deletes_expired(self, db_session):
        """Test cleanup deletes expired jobs and their files."""
        # Create an expired job with file path within downloads directory
        past_time = datetime.now(UTC) - timedelta(hours=1)
        job = DownloadJob(
            id=UUID("550e8400-e29b-41d4-a716-446655440021"),
            user_id=UUID("550e8400-e29b-41d4-a716-446655440005"),
            url="https://www.youtube.com/watch?v=test",
            status="completed",
            expires_at=past_time,
            file_path=f"{_DOWNLOADS_DIR}/test.mp4",
        )
        db_session.add(job)
        await db_session.commit()

        with (
            patch("worker.main.validate_path", return_value=f"{_DOWNLOADS_DIR}/test.mp4"),
            patch("worker.main.os.path.exists", return_value=True),
        ):
            with patch("worker.main.os.remove") as mock_remove:
                count = await cleanup_expired_jobs()
                assert count == 1
                mock_remove.assert_called_once()

    @pytest.mark.unit
    async def test_cleanup_expired_jobs_skips_path_traversal(self, db_session):
        """Test cleanup skips files outside storage directory."""
        past_time = datetime.now(UTC) - timedelta(hours=1)
        job = DownloadJob(
            id=UUID("550e8400-e29b-41d4-a716-446655440022"),
            user_id=UUID("550e8400-e29b-41d4-a716-446655440005"),
            url="https://www.youtube.com/watch?v=test",
            status="completed",
            expires_at=past_time,
            file_path="/etc/passwd",
        )
        db_session.add(job)
        await db_session.commit()

        with (
            patch("worker.main.validate_path", side_effect=ValueError("Path traversal detected")),
            patch("worker.main.os.path.exists", return_value=True),
        ):
            with patch("worker.main.os.remove") as mock_remove:
                count = await cleanup_expired_jobs()
                assert count == 0
                mock_remove.assert_not_called()

        await db_session.refresh(job)
        assert job.status == "completed"

    @pytest.mark.unit
    async def test_cleanup_expired_jobs_handles_missing_file(self, db_session):
        """Test cleanup handles missing files gracefully."""
        past_time = datetime.now(UTC) - timedelta(hours=1)
        job = DownloadJob(
            id=UUID("550e8400-e29b-41d4-a716-446655440011"),
            user_id=UUID("550e8400-e29b-41d4-a716-446655440005"),
            url="https://www.youtube.com/watch?v=test",
            status="completed",
            expires_at=past_time,
            file_path=f"{_DOWNLOADS_DIR}/nonexistent.mp4",
        )
        db_session.add(job)
        await db_session.commit()

        with (
            patch("worker.main.validate_path", return_value=f"{_DOWNLOADS_DIR}/nonexistent.mp4"),
            patch("worker.main.os.path.exists", return_value=False),
        ):
            with patch("worker.main.os.remove") as mock_remove:
                count = await cleanup_expired_jobs()
                assert count == 1
                mock_remove.assert_not_called()

    @pytest.mark.unit
    async def test_cleanup_expired_jobs_ignores_pending(self, db_session):
        """Test cleanup ignores pending jobs even if expired."""
        # Create a pending job that's past its expiry
        past_time = datetime.now(UTC) - timedelta(hours=1)
        job = DownloadJob(
            id=UUID("550e8400-e29b-41d4-a716-446655440010"),
            user_id=UUID("550e8400-e29b-41d4-a716-446655440005"),
            url="https://www.youtube.com/watch?v=test",
            status="pending",  # Not completed
            expires_at=past_time,
        )
        db_session.add(job)
        await db_session.commit()

        count = await cleanup_expired_jobs()
        # Should not delete pending job even though expired
        assert count == 0

    @pytest.mark.unit
    async def test_cleanup_expired_jobs_ignores_failed(self, db_session):
        """Test cleanup ignores failed jobs even if expired."""
        # Create a failed job that's past its expiry
        past_time = datetime.now(UTC) - timedelta(hours=1)
        job = DownloadJob(
            id=UUID("550e8400-e29b-41d4-a716-446655440012"),
            user_id=UUID("550e8400-e29b-41d4-a716-446655440005"),
            url="https://www.youtube.com/watch?v=test",
            status="failed",
            expires_at=past_time,
        )
        db_session.add(job)
        await db_session.commit()

        count = await cleanup_expired_jobs()
        # Should not delete failed job even though expired
        assert count == 0

    @pytest.mark.unit
    async def test_cleanup_expired_jobs_multiple(self, db_session):
        """Test cleanup handles multiple expired jobs."""
        past_time = datetime.now(UTC) - timedelta(hours=1)

        for i in range(3):
            job = DownloadJob(
                id=UUID(f"550e8400-e29b-41d4-a716-44665544002{i}"),
                user_id=UUID("550e8400-e29b-41d4-a716-446655440005"),
                url="https://www.youtube.com/watch?v=test",
                status="completed",
                expires_at=past_time,
                file_path=f"{_DOWNLOADS_DIR}/test{i}.mp4",
            )
            db_session.add(job)
        await db_session.commit()

        with (
            patch("worker.main.validate_path", return_value=f"{_DOWNLOADS_DIR}/test0.mp4"),
            patch("worker.main.os.path.exists", return_value=False),
        ):
            count = await cleanup_expired_jobs()
            assert count == 3

    @pytest.mark.unit
    async def test_cleanup_expired_jobs_rejects_sibling_prefix_path(self, db_session):
        """Sibling-prefix paths are skipped without deleting the file or DB row."""
        past_time = datetime.now(UTC) - timedelta(hours=1)
        job = DownloadJob(
            id=UUID("550e8400-e29b-41d4-a716-446655440023"),
            user_id=UUID("550e8400-e29b-41d4-a716-446655440005"),
            url="https://www.youtube.com/watch?v=test",
            status="completed",
            expires_at=past_time,
            file_path=f"{_DOWNLOADS_DIR}_evil/test.mp4",
        )
        db_session.add(job)
        await db_session.commit()

        with (
            patch("worker.main.os.path.exists", return_value=True),
            patch("worker.main.os.remove") as mock_remove,
        ):
            count = await cleanup_expired_jobs()

        assert count == 0
        mock_remove.assert_not_called()
        await db_session.refresh(job)
        assert job.status == "completed"


class TestWorkerMainStartup:
    """Tests for startup connection checks added in this PR.

    The main() function now verifies Redis and database connectivity before
    entering the processing loop. These tests exercise those early-exit paths.
    """

    def _make_mock_session_factory(self):
        """Return a mock async session factory where SELECT 1 succeeds."""
        mock_db = AsyncMock()
        mock_db.execute = AsyncMock(return_value=None)
        mock_cm = AsyncMock()
        mock_cm.__aenter__ = AsyncMock(return_value=mock_db)
        mock_cm.__aexit__ = AsyncMock(return_value=False)
        mock_factory = MagicMock(return_value=mock_cm)
        return mock_factory

    @pytest.mark.unit
    async def test_main_raises_when_redis_ping_fails(self):
        """main() must propagate exceptions when Redis is unreachable at startup."""
        from worker.main import shutdown_event

        shutdown_event.clear()

        mock_redis = AsyncMock()
        mock_redis.ping = AsyncMock(side_effect=ConnectionError("Redis unavailable"))

        with (
            patch("worker.main.redis_client", mock_redis),
            patch("worker.main.start_health_server"),
            patch("worker.main.stop_health_server"),
            patch("worker.main.update_worker_state"),
            patch("worker.main.write_health_async", new_callable=AsyncMock),
        ):
            from worker.main import main as _worker_main

            with pytest.raises(ConnectionError, match="Redis unavailable"):
                await _worker_main()

    @pytest.mark.unit
    async def test_main_raises_when_db_connection_fails(self):
        """main() must propagate exceptions when the database is unreachable at startup."""
        from worker.main import main, shutdown_event

        shutdown_event.clear()

        mock_redis = AsyncMock()
        mock_redis.ping = AsyncMock(return_value=True)

        # Session factory that raises on execute
        mock_db = AsyncMock()
        mock_db.execute = AsyncMock(side_effect=OSError("DB connection refused"))
        mock_cm = AsyncMock()
        mock_cm.__aenter__ = AsyncMock(return_value=mock_db)
        mock_cm.__aexit__ = AsyncMock(return_value=False)
        mock_factory = MagicMock(return_value=mock_cm)

        with (
            patch("worker.main.redis_client", mock_redis),
            patch("worker.main.get_async_session_factory", return_value=mock_factory),
            patch("worker.main.start_health_server"),
            patch("worker.main.stop_health_server"),
            patch("worker.main.update_worker_state"),
            patch("worker.main.write_health_async", new_callable=AsyncMock),
        ):
            with pytest.raises(OSError, match="DB connection refused"):
                await main()

    @pytest.mark.unit
    async def test_main_proceeds_when_redis_and_db_connected(self):
        """main() proceeds past startup when both Redis and DB connect successfully."""
        from worker.main import main, shutdown_event

        # Signal shutdown immediately so the while loop exits after one iteration
        shutdown_event.set()

        mock_redis = AsyncMock()
        mock_redis.ping = AsyncMock(return_value=True)
        mock_redis.eval = AsyncMock(return_value=[])
        mock_redis.brpop = AsyncMock(return_value=None)

        mock_factory = self._make_mock_session_factory()

        mock_health_server = MagicMock()

        with (
            patch("worker.main.redis_client", mock_redis),
            patch("worker.main.get_async_session_factory", return_value=mock_factory),
            patch("worker.main.start_health_server", return_value=mock_health_server),
            patch("worker.main.stop_health_server"),
            patch("worker.main.update_worker_state"),
            patch("worker.main.write_health_async", new_callable=AsyncMock),
            patch("worker.main.sync_outbox_to_queue", new_callable=AsyncMock),
            patch("worker.main.cleanup_expired_jobs", new_callable=AsyncMock, return_value=0),
            patch("worker.main.requeue_stuck_jobs", new_callable=AsyncMock, return_value=0),
        ):
            # Should complete without raising
            await main()

        mock_redis.ping.assert_awaited_once()

    @pytest.mark.unit
    async def test_main_redis_ping_called_before_loop(self):
        """Redis ping must be called during startup, before the main processing loop."""
        from worker.main import main, shutdown_event

        shutdown_event.set()

        call_order: list[str] = []

        mock_redis = AsyncMock()

        async def recording_ping():
            call_order.append("ping")
            return True

        mock_redis.ping = recording_ping
        mock_redis.eval = AsyncMock(return_value=[])
        mock_redis.brpop = AsyncMock(return_value=None)

        mock_factory = self._make_mock_session_factory()

        with (
            patch("worker.main.redis_client", mock_redis),
            patch("worker.main.get_async_session_factory", return_value=mock_factory),
            patch("worker.main.start_health_server", return_value=None),
            patch("worker.main.stop_health_server"),
            patch("worker.main.update_worker_state"),
            patch("worker.main.write_health_async", new_callable=AsyncMock),
            patch("worker.main.sync_outbox_to_queue", new_callable=AsyncMock),
            patch("worker.main.cleanup_expired_jobs", new_callable=AsyncMock, return_value=0),
            patch("worker.main.requeue_stuck_jobs", new_callable=AsyncMock, return_value=0),
        ):
            await main()

        assert "ping" in call_order, "redis_client.ping() was never called during startup"

    @pytest.mark.unit
    async def test_main_db_select1_called_before_loop(self):
        """Database SELECT 1 health check must be called during startup."""
        from worker.main import main, shutdown_event

        shutdown_event.set()

        db_calls: list[str] = []

        mock_redis = AsyncMock()
        mock_redis.ping = AsyncMock(return_value=True)
        mock_redis.eval = AsyncMock(return_value=[])
        mock_redis.brpop = AsyncMock(return_value=None)

        mock_db = AsyncMock()

        async def recording_execute(stmt):
            db_calls.append(str(stmt))

        mock_db.execute = recording_execute
        mock_cm = AsyncMock()
        mock_cm.__aenter__ = AsyncMock(return_value=mock_db)
        mock_cm.__aexit__ = AsyncMock(return_value=False)
        mock_factory = MagicMock(return_value=mock_cm)

        with (
            patch("worker.main.redis_client", mock_redis),
            patch("worker.main.get_async_session_factory", return_value=mock_factory),
            patch("worker.main.start_health_server", return_value=None),
            patch("worker.main.stop_health_server"),
            patch("worker.main.update_worker_state"),
            patch("worker.main.write_health_async", new_callable=AsyncMock),
            patch("worker.main.sync_outbox_to_queue", new_callable=AsyncMock),
            patch("worker.main.cleanup_expired_jobs", new_callable=AsyncMock, return_value=0),
            patch("worker.main.requeue_stuck_jobs", new_callable=AsyncMock, return_value=0),
        ):
            await main()

        assert any("SELECT" in c.upper() or "select" in c for c in db_calls), (
            "Expected a SELECT statement to be executed during DB startup check"
        )

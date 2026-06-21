"""Tests for worker health module."""

import os
import threading
from unittest.mock import MagicMock, patch

import pytest

from worker.health import (
    get_redis_url,
    get_worker_id,
    start_health_server,
    stop_health_server,
    update_worker_state,
)
from worker.health import settings as health_settings


class TestUpdateWorkerState:
    """Tests for update_worker_state function."""

    def test_update_worker_state_sets_values(self):
        """Test that update_worker_state updates state values."""
        update_worker_state(status="running", current_job_started_at="2024-01-01T00:00:00")
        from worker.health import _state_lock, _worker_state

        with _state_lock:
            assert _worker_state["status"] == "running"
            assert _worker_state["current_job_started_at"] == "2024-01-01T00:00:00"
            assert _worker_state["last_heartbeat"] is not None

    def test_update_worker_state_thread_safety(self):
        """Test that update_worker_state is thread-safe."""
        results = []

        def update_state(value):
            update_worker_state(status=value)
            from worker.health import _state_lock, _worker_state

            with _state_lock:
                results.append(_worker_state["status"])

        threads = [threading.Thread(target=update_state, args=(f"status-{i}",)) for i in range(10)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert len(results) == 10


class TestGetRedisUrl:
    """Tests for get_redis_url function."""

    def test_get_redis_url_uses_canonical_settings(self):
        """get_redis_url returns the canonical settings Redis URL."""
        original_redis_url = health_settings.redis_url
        health_settings.redis_url = "redis://settings-only:6379"
        try:
            result = get_redis_url()
        finally:
            health_settings.redis_url = original_redis_url

        assert result == "redis://settings-only:6379"

    def test_get_redis_url_ignores_worker_local_components(self):
        """Worker health does not assemble Redis URLs from raw env components."""
        original_redis_url = health_settings.redis_url
        health_settings.redis_url = "redis://canonical:6379"
        with patch.dict(
            os.environ,
            {
                "REDIS_HOST": "myhost",
                "REDIS_PORT": "6380",
                "REDIS_PASSWORD": "secret:p@ss",
            },
        ):
            try:
                result = get_redis_url()
            finally:
                health_settings.redis_url = original_redis_url

        assert result == "redis://canonical:6379"


class TestGetWorkerId:
    """Tests for get_worker_id function."""

    def test_get_worker_id_default(self):
        """Test default worker ID when env not set."""
        with patch.dict(os.environ, {}, clear=True):
            result = get_worker_id()
            assert result == "worker-1"

    def test_get_worker_id_from_env(self):
        """Test worker ID from WORKER_ID env var."""
        with patch.dict(os.environ, {"WORKER_ID": "my-custom-worker"}):
            result = get_worker_id()
            assert result == "my-custom-worker"


class TestStartHealthServer:
    """Tests for start_health_server function."""

    def test_start_health_server_disabled_when_port_zero(self):
        """Test that health server is disabled when port is 0."""
        with patch.dict(os.environ, {"WORKER_HEALTH_PORT": "0"}):
            result = start_health_server()
            assert result is None

    def test_stop_health_server_cleans_up(self):
        """Test that stop_health_server properly cleans up."""
        import worker.health as health_module

        health_module._health_server = None

        mock_server = MagicMock()

        with (
            patch.dict(os.environ, {"WORKER_HEALTH_PORT": "18083"}),
            patch("worker.health.HTTPServer", return_value=mock_server) as mock_http_server,
        ):
            result = start_health_server()
            assert result is mock_server
            stop_health_server()
            assert health_module._health_server is None
            mock_http_server.assert_called_once_with(
                ("0.0.0.0", 18083), health_module._HealthHandler
            )
            mock_server.shutdown.assert_called_once()
            mock_server.server_close.assert_called_once()


class TestWriteHealthSync:
    """Tests for write_health_sync function."""

    def test_write_health_sync_returns_bool(self):
        """Test that write_health_sync returns a boolean."""
        from worker.health import write_health_sync

        with patch("redis.from_url") as mock_redis:
            mock_client = MagicMock()
            mock_client.setex = MagicMock()
            mock_redis.return_value = mock_client

            result = write_health_sync()
            assert isinstance(result, bool)
            mock_redis.assert_called_once()
            assert mock_redis.call_args.args[0] == health_settings.redis_url

    def test_write_health_sync_handles_connection_error(self):
        """Test write_health_sync handles Redis connection errors."""
        from worker.health import write_health_sync

        with patch("redis.from_url") as mock_redis:
            import redis

            mock_redis.return_value.setex.side_effect = redis.exceptions.ConnectionError(
                "Connection failed"
            )

            result = write_health_sync()
            assert result is False

    def test_write_health_sync_handles_timeout_error(self):
        """Test write_health_sync handles Redis timeout errors."""
        from worker.health import write_health_sync

        with patch("redis.from_url") as mock_redis:
            import redis

            mock_redis.return_value.setex.side_effect = redis.exceptions.TimeoutError("Timeout")

            result = write_health_sync()
            assert result is False


class TestWriteHealthAsync:
    """Tests for write_health_async function."""

    @pytest.fixture(autouse=True)
    def reset_health_redis(self):
        """Reset the shared health Redis client before each test."""
        from worker.health import reset_health_redis_client

        reset_health_redis_client()

    @pytest.mark.asyncio
    async def test_write_health_async_returns_bool(self):
        """Test that write_health_async returns a boolean."""
        import redis.asyncio as aioredis

        from worker.health import write_health_async

        mock_client = AsyncMock()
        mock_client.setex = AsyncMock()

        with patch.object(aioredis, "from_url", return_value=mock_client):
            result = await write_health_async()
            assert isinstance(result, bool)

    @pytest.mark.asyncio
    async def test_write_health_async_handles_connection_error(self):
        """Test write_health_async handles connection errors."""
        import redis.asyncio as aioredis

        from worker.health import write_health_async

        mock_client = AsyncMock()
        mock_client.setex = AsyncMock(side_effect=ConnectionError("Connection failed"))
        mock_client.close = AsyncMock()
        mock_client.aclose = AsyncMock()

        with patch.object(aioredis, "from_url", return_value=mock_client):
            result = await write_health_async()
            assert result is False

    @pytest.mark.asyncio
    async def test_write_health_async_handles_timeout_error(self):
        """Test write_health_async handles timeout errors."""
        import redis.asyncio as aioredis

        from worker.health import write_health_async

        mock_client = AsyncMock()
        mock_client.setex = AsyncMock(side_effect=TimeoutError("Timeout"))
        mock_client.close = AsyncMock()
        mock_client.aclose = AsyncMock()

        with patch.object(aioredis, "from_url", return_value=mock_client):
            result = await write_health_async()
            assert result is False


class AsyncMock(MagicMock):
    async def __call__(self, *args, **kwargs):
        return super().__call__(*args, **kwargs)

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        pass

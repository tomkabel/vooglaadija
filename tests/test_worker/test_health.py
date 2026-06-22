"""Tests for worker health module."""

import asyncio
import os
import threading
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from httpx import ASGITransport, AsyncClient
from prometheus_client import CONTENT_TYPE_LATEST

from worker.health import (
    get_redis_url,
    get_worker_id,
    health_app,
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
        health_module._health_server_thread = None

        mock_server = MagicMock()
        mock_config = MagicMock()
        mock_thread = MagicMock()
        mock_thread.is_alive.return_value = False

        with (
            patch.dict(os.environ, {"WORKER_HEALTH_PORT": "18083"}),
            patch("worker.health.uvicorn.Config", return_value=mock_config) as mock_config_cls,
            patch("worker.health.uvicorn.Server", return_value=mock_server) as mock_server_cls,
            patch("worker.health.threading.Thread", return_value=mock_thread) as mock_thread_cls,
        ):
            result = start_health_server()
            assert result is mock_server
            stop_health_server()
            assert health_module._health_server is None
            assert health_module._health_server_thread is None
            mock_config_cls.assert_called_once_with(
                health_app,
                host="0.0.0.0",
                port=18083,
                log_level="info",
                access_log=False,
            )
            mock_server_cls.assert_called_once_with(mock_config)
            mock_thread_cls.assert_called_once_with(target=mock_server.run, daemon=True)
            mock_thread.start.assert_called_once()
            assert mock_server.should_exit is True
            mock_thread.join.assert_called_once_with(timeout=5)

    def test_start_health_server_defaults_to_port_8082(self):
        """Test start_health_server uses the worker health default port."""
        import worker.health as health_module

        health_module._health_server = None
        health_module._health_server_thread = None

        mock_server = MagicMock()
        mock_config = MagicMock()
        mock_thread = MagicMock()
        mock_thread.is_alive.return_value = False

        with (
            patch.dict(os.environ, {}, clear=True),
            patch("worker.health.uvicorn.Config", return_value=mock_config) as mock_config_cls,
            patch("worker.health.uvicorn.Server", return_value=mock_server),
            patch("worker.health.threading.Thread", return_value=mock_thread),
        ):
            result = start_health_server()
            assert result is mock_server
            stop_health_server()

        mock_config_cls.assert_called_once_with(
            health_app,
            host="0.0.0.0",
            port=8082,
            log_level="info",
            access_log=False,
        )

    @pytest.mark.asyncio
    async def test_start_health_server_captures_running_worker_loop(self):
        """Test start_health_server records the worker loop when called from async main."""
        import worker.health as health_module

        health_module._health_server = None
        health_module._health_server_thread = None
        health_module._worker_loop = None

        mock_server = MagicMock()
        mock_config = MagicMock()
        mock_thread = MagicMock()
        mock_thread.is_alive.return_value = False

        with (
            patch.dict(os.environ, {"WORKER_HEALTH_PORT": "18084"}),
            patch("worker.health.uvicorn.Config", return_value=mock_config),
            patch("worker.health.uvicorn.Server", return_value=mock_server),
            patch("worker.health.threading.Thread", return_value=mock_thread),
        ):
            result = start_health_server()
            assert result is mock_server
            assert health_module._worker_loop is asyncio.get_running_loop()
            stop_health_server()

        assert health_module._worker_loop is None


def _mock_session_factory(*, execute_side_effect=None):
    session = MagicMock()
    session.execute = AsyncMock(side_effect=execute_side_effect)
    context = MagicMock()
    context.__aenter__ = AsyncMock(return_value=session)
    context.__aexit__ = AsyncMock(return_value=None)
    return MagicMock(return_value=context)


class TestHealthAppEndpoints:
    """Tests for the worker FastAPI health app endpoints."""

    @pytest.mark.asyncio
    async def test_health_endpoint_ok_when_redis_and_database_pass(self):
        """Test /health returns ok when Redis and database checks pass."""
        mock_redis = MagicMock()
        mock_redis.ping = AsyncMock(return_value=True)
        update_worker_state(status="running")

        with (
            patch("worker.health.get_redis_client", return_value=mock_redis),
            patch("worker.health.get_async_session_factory", return_value=_mock_session_factory()),
        ):
            async with AsyncClient(
                transport=ASGITransport(app=health_app), base_url="http://test"
            ) as client:
                response = await client.get("/health")

        assert response.status_code == 200
        assert response.json() == {
            "status": "ok",
            "checks": {
                "redis": True,
                "database": True,
                "worker_loop": True,
            },
        }

    @pytest.mark.asyncio
    async def test_health_endpoint_degraded_when_redis_fails(self):
        """Test /health returns degraded and non-2xx when Redis check fails."""
        mock_redis = MagicMock()
        mock_redis.ping = AsyncMock(side_effect=ConnectionError("redis unavailable"))
        update_worker_state(status="running")

        with (
            patch("worker.health.get_redis_client", return_value=mock_redis),
            patch("worker.health.get_async_session_factory", return_value=_mock_session_factory()),
        ):
            async with AsyncClient(
                transport=ASGITransport(app=health_app), base_url="http://test"
            ) as client:
                response = await client.get("/health")

        assert response.status_code == 503
        assert response.json() == {
            "status": "degraded",
            "checks": {
                "redis": False,
                "database": True,
                "worker_loop": True,
            },
        }

    @pytest.mark.asyncio
    async def test_health_endpoint_degraded_when_database_fails(self):
        """Test /health returns degraded and non-2xx when database check fails."""
        mock_redis = MagicMock()
        mock_redis.ping = AsyncMock(return_value=True)
        update_worker_state(status="running")

        with (
            patch("worker.health.get_redis_client", return_value=mock_redis),
            patch(
                "worker.health.get_async_session_factory",
                return_value=_mock_session_factory(execute_side_effect=Exception("db down")),
            ),
        ):
            async with AsyncClient(
                transport=ASGITransport(app=health_app), base_url="http://test"
            ) as client:
                response = await client.get("/health")

        assert response.status_code == 503
        assert response.json() == {
            "status": "degraded",
            "checks": {
                "redis": True,
                "database": False,
                "worker_loop": True,
            },
        }

    @pytest.mark.asyncio
    async def test_health_endpoint_degraded_when_worker_loop_heartbeat_is_stale(self):
        """A stale worker heartbeat should fail the threaded health endpoint."""
        import worker.health as health_module

        mock_redis = MagicMock()
        mock_redis.ping = AsyncMock(return_value=True)

        with health_module._state_lock:
            health_module._worker_state["last_heartbeat"] = "2000-01-01T00:00:00+00:00"

        with (
            patch("worker.health.get_redis_client", return_value=mock_redis),
            patch("worker.health.get_async_session_factory", return_value=_mock_session_factory()),
        ):
            async with AsyncClient(
                transport=ASGITransport(app=health_app), base_url="http://test"
            ) as client:
                response = await client.get("/health")

        assert response.status_code == 503
        assert response.json() == {
            "status": "degraded",
            "checks": {
                "redis": True,
                "database": True,
                "worker_loop": False,
            },
        }

    @pytest.mark.asyncio
    async def test_metrics_endpoint_returns_prometheus_output(self):
        """Test /metrics returns Prometheus-formatted output."""
        async with AsyncClient(
            transport=ASGITransport(app=health_app), base_url="http://test"
        ) as client:
            response = await client.get("/metrics")

        assert response.status_code == 200
        assert response.headers["content-type"] == CONTENT_TYPE_LATEST
        assert response.text
        assert "# HELP" in response.text

    @pytest.mark.asyncio
    async def test_health_endpoint_runs_dependency_checks_on_worker_loop(self):
        """Threaded health requests run async dependency checks on the worker loop."""
        import worker.health as health_module

        worker_loop = asyncio.get_running_loop()
        observed_loops = []
        update_worker_state(status="running")

        async def passing_check():
            observed_loops.append(asyncio.get_running_loop())
            return True

        async def request_from_health_thread():
            async with AsyncClient(
                transport=ASGITransport(app=health_app), base_url="http://test"
            ) as client:
                return await client.get("/health")

        def run_threaded_request():
            return asyncio.run(request_from_health_thread())

        health_module._worker_loop = worker_loop
        try:
            with (
                patch("worker.health._check_redis", new=passing_check),
                patch("worker.health._check_database", new=passing_check),
            ):
                response = await asyncio.to_thread(run_threaded_request)
        finally:
            health_module._worker_loop = None

        assert response.status_code == 200
        assert response.json() == {
            "status": "ok",
            "checks": {
                "redis": True,
                "database": True,
                "worker_loop": True,
            },
        }
        assert observed_loops == [worker_loop, worker_loop]


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
    async def test_write_health_async_uses_shared_core_client(self):
        """Test write_health_async writes through the shared core Redis client path."""
        from worker.health import write_health_async

        mock_client = AsyncMock()
        mock_client.setex = AsyncMock()

        with patch("worker.health.get_redis_client", return_value=mock_client) as mock_get_client:
            result = await write_health_async()

        assert result is True
        mock_get_client.assert_called_once_with()
        mock_client.setex.assert_called_once()
        assert mock_client.setex.call_args.args[0].startswith("worker:health:")

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

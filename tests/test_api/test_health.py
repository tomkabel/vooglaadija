"""Tests for health check endpoints."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from httpx import ASGITransport, AsyncClient

from app.main import app


class TestHealthCheck:
    """Tests for GET /health endpoint."""

    @pytest.mark.asyncio
    async def test_health_check_healthy(self):
        """Test health check returns healthy status when all dependencies are up."""
        with patch.dict(
            "os.environ",
            {"DATABASE_URL": "postgresql://test", "REDIS_URL": "redis://test"},
        ):
            with (
                patch("app.api.routes.health.create_async_engine") as mock_engine,
                patch("app.api.routes.health.get_redis_client") as mock_get_redis_client,
            ):
                # Mock successful database connection
                mock_conn = AsyncMock()
                mock_conn.__aenter__ = AsyncMock(return_value=mock_conn)
                mock_conn.__aexit__ = AsyncMock()
                mock_conn.execute = AsyncMock()

                mock_engine.return_value.connect = MagicMock(return_value=mock_conn)
                mock_engine.return_value.dispose = AsyncMock()

                # Mock successful Redis connection
                mock_redis = MagicMock()
                mock_redis.ping = AsyncMock(return_value=True)
                mock_get_redis_client.return_value = mock_redis

                async with AsyncClient(
                    transport=ASGITransport(app=app), base_url="http://test"
                ) as client:
                    response = await client.get("/health")

                assert response.status_code == 200
                data = response.json()
                assert data["status"] == "healthy"
                assert data["dependencies"]["database"] == "ok"
                assert data["dependencies"]["redis"] == "ok"

    @pytest.mark.asyncio
    async def test_health_check_database_error(self):
        """Test health check returns unhealthy when database is down."""
        with patch.dict(
            "os.environ",
            {"DATABASE_URL": "postgresql://test", "REDIS_URL": "redis://test"},
        ):
            with (
                patch("app.api.routes.health.create_async_engine") as mock_engine,
                patch("app.api.routes.health.get_redis_client") as mock_get_redis_client,
            ):
                # Mock failed database connection
                mock_engine.return_value.connect = MagicMock(
                    side_effect=Exception("Connection refused")
                )
                mock_engine.return_value.dispose = AsyncMock()

                # Mock successful Redis to isolate database failure
                mock_redis = MagicMock()
                mock_redis.ping = AsyncMock(return_value=True)
                mock_get_redis_client.return_value = mock_redis

                async with AsyncClient(
                    transport=ASGITransport(app=app), base_url="http://test"
                ) as client:
                    response = await client.get("/health")

            assert response.status_code == 200  # Returns 200 but with unhealthy status
            data = response.json()
            assert data["status"] == "unhealthy"
            assert "error" in data["dependencies"]["database"]

    @pytest.mark.asyncio
    async def test_health_check_redis_error(self):
        """Test health check returns unhealthy when Redis is down."""
        with patch.dict(
            "os.environ",
            {"DATABASE_URL": "postgresql://test", "REDIS_URL": "redis://test"},
        ):
            with (
                patch("app.api.routes.health.create_async_engine") as mock_engine,
                patch("app.api.routes.health.get_redis_client") as mock_get_redis_client,
            ):
                # Mock successful database
                mock_conn = AsyncMock()
                mock_conn.__aenter__ = AsyncMock(return_value=mock_conn)
                mock_conn.__aexit__ = AsyncMock()
                mock_conn.execute = AsyncMock()

                mock_engine.return_value.connect = MagicMock(return_value=mock_conn)
                mock_engine.return_value.dispose = AsyncMock()

                # Mock failed Redis connection
                mock_redis = MagicMock()
                mock_redis.ping = AsyncMock(side_effect=Exception("Connection refused"))
                mock_get_redis_client.return_value = mock_redis

                async with AsyncClient(
                    transport=ASGITransport(app=app), base_url="http://test"
                ) as client:
                    response = await client.get("/health")

                assert response.status_code == 200
                data = response.json()
                assert data["status"] == "unhealthy"
                assert "error" in data["dependencies"]["redis"]

    @pytest.mark.asyncio
    async def test_health_check_missing_database_url(self):
        """Test health check handles missing DATABASE_URL."""
        with patch.dict("os.environ", {"DATABASE_URL": "", "REDIS_URL": ""}):
            with patch("app.api.routes.health.create_async_engine") as mock_engine:
                # Mock no database URL
                mock_engine.return_value.dispose = AsyncMock()

                async with AsyncClient(
                    transport=ASGITransport(app=app), base_url="http://test"
                ) as client:
                    response = await client.get("/health")

                assert response.status_code == 200
                data = response.json()
                assert data["status"] == "unhealthy"
                assert "missing DATABASE_URL" in data["dependencies"]["database"]

    @pytest.mark.asyncio
    async def test_health_check_missing_redis_url(self):
        """Test health check handles missing REDIS_URL."""
        with patch.dict("os.environ", {"DATABASE_URL": "postgresql://test", "REDIS_URL": ""}):
            with patch("app.api.routes.health.create_async_engine") as mock_engine:
                # Mock successful database
                mock_conn = AsyncMock()
                mock_conn.__aenter__ = AsyncMock(return_value=mock_conn)
                mock_conn.__aexit__ = AsyncMock()
                mock_conn.execute = AsyncMock()

                mock_engine.return_value.connect = MagicMock(return_value=mock_conn)
                mock_engine.return_value.dispose = AsyncMock()

                async with AsyncClient(
                    transport=ASGITransport(app=app), base_url="http://test"
                ) as client:
                    response = await client.get("/health")

                assert response.status_code == 200
                data = response.json()
                assert data["status"] == "unhealthy"
                assert "missing REDIS_URL" in data["dependencies"]["redis"]


class TestReadinessCheck:
    """Tests for GET /health/ready endpoint."""

    @pytest.mark.asyncio
    async def test_readiness_check_ready(self):
        """Test readiness check returns ready when all dependencies are up."""
        with patch.dict(
            "os.environ",
            {"DATABASE_URL": "postgresql://test", "REDIS_URL": "redis://test"},
        ):
            with (
                patch("app.api.routes.health.create_async_engine") as mock_engine,
                patch("app.api.routes.health.get_redis_client") as mock_get_redis_client,
            ):
                # Mock successful database
                mock_conn = AsyncMock()
                mock_conn.__aenter__ = AsyncMock(return_value=mock_conn)
                mock_conn.__aexit__ = AsyncMock()
                mock_conn.execute = AsyncMock()

                mock_engine.return_value.connect = MagicMock(return_value=mock_conn)
                mock_engine.return_value.dispose = AsyncMock()

                # Mock successful Redis
                mock_redis = MagicMock()
                mock_redis.ping = AsyncMock(return_value=True)
                mock_get_redis_client.return_value = mock_redis

                async with AsyncClient(
                    transport=ASGITransport(app=app), base_url="http://test"
                ) as client:
                    response = await client.get("/health/ready")

                assert response.status_code == 200
                data = response.json()
                assert data["status"] == "ready"
                assert data["database"] == "connected"
                assert data["redis"] == "connected"

    @pytest.mark.asyncio
    async def test_readiness_check_database_down(self):
        """Test readiness check returns 503 when database is down."""
        with patch.dict(
            "os.environ",
            {"DATABASE_URL": "postgresql://test", "REDIS_URL": "redis://test"},
        ):
            with (
                patch("app.api.routes.health.create_async_engine") as mock_engine,
                patch("app.api.routes.health.get_redis_client") as mock_get_redis_client,
            ):
                # Mock failed database
                mock_engine.return_value.connect = MagicMock(
                    side_effect=Exception("Connection refused")
                )
                mock_engine.return_value.dispose = AsyncMock()

                # Mock successful Redis
                mock_redis = MagicMock()
                mock_redis.ping = AsyncMock(return_value=True)
                mock_get_redis_client.return_value = mock_redis

                async with AsyncClient(
                    transport=ASGITransport(app=app), base_url="http://test"
                ) as client:
                    response = await client.get("/health/ready")

            assert response.status_code == 503
            data = response.json()
            assert data["status"] == "not_ready"
            assert "error" in data["database"]

    @pytest.mark.asyncio
    async def test_readiness_check_redis_down(self):
        """Test readiness check returns 503 when Redis is down."""
        with patch.dict(
            "os.environ",
            {"DATABASE_URL": "postgresql://test", "REDIS_URL": "redis://test"},
        ):
            with (
                patch("app.api.routes.health.create_async_engine") as mock_engine,
                patch("app.api.routes.health.get_redis_client") as mock_get_redis_client,
            ):
                # Mock successful database
                mock_conn = AsyncMock()
                mock_conn.__aenter__ = AsyncMock(return_value=mock_conn)
                mock_conn.__aexit__ = AsyncMock()
                mock_conn.execute = AsyncMock()

                mock_engine.return_value.connect = MagicMock(return_value=mock_conn)
                mock_engine.return_value.dispose = AsyncMock()

                # Mock failed Redis
                mock_redis = MagicMock()
                mock_redis.ping = AsyncMock(side_effect=Exception("Connection refused"))
                mock_get_redis_client.return_value = mock_redis

                async with AsyncClient(
                    transport=ASGITransport(app=app), base_url="http://test"
                ) as client:
                    response = await client.get("/health/ready")

            assert response.status_code == 503
            data = response.json()
            assert data["status"] == "not_ready"
            assert "error" in data["redis"]

    @pytest.mark.asyncio
    async def test_readiness_check_missing_database_url(self):
        """Test readiness check handles missing DATABASE_URL."""
        with patch.dict("os.environ", {"DATABASE_URL": "", "REDIS_URL": "redis://test"}):
            with patch("app.api.routes.health.get_redis_client") as mock_get_redis_client:
                # Mock successful Redis
                mock_redis = MagicMock()
                mock_redis.ping = AsyncMock(return_value=True)
                mock_get_redis_client.return_value = mock_redis

                async with AsyncClient(
                    transport=ASGITransport(app=app), base_url="http://test"
                ) as client:
                    response = await client.get("/health/ready")

            assert response.status_code == 503
            data = response.json()
            assert data["status"] == "not_ready"
            assert "missing DATABASE_URL" in data["database"]

    @pytest.mark.asyncio
    async def test_readiness_check_all_down(self):
        """Test readiness check returns 503 when all dependencies are down."""
        with patch.dict(
            "os.environ",
            {"DATABASE_URL": "postgresql://test", "REDIS_URL": "redis://test"},
        ):
            with (
                patch("app.api.routes.health.create_async_engine") as mock_engine,
                patch("app.api.routes.health.get_redis_client") as mock_get_redis_client,
            ):
                # Mock failed database
                mock_engine.return_value.connect = MagicMock(
                    side_effect=Exception("Connection refused")
                )
                mock_engine.return_value.dispose = AsyncMock()

                # Mock failed Redis
                mock_redis = MagicMock()
                mock_redis.ping = AsyncMock(side_effect=Exception("Connection refused"))
                mock_get_redis_client.return_value = mock_redis

                async with AsyncClient(
                    transport=ASGITransport(app=app), base_url="http://test"
                ) as client:
                    response = await client.get("/health/ready")

            assert response.status_code == 503
            data = response.json()
            assert data["status"] == "not_ready"
            assert "error" in data["database"]
            assert "error" in data["redis"]

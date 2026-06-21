"""Chaos Injection API route tests."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from httpx import ASGITransport, AsyncClient

from app.api.dependencies import get_current_user_from_cookie
from app.main import app
from core.config import settings


@pytest.fixture(autouse=True)
def enable_chaos_feature():
    """Enable the chaos feature flag for these tests."""
    saved = settings.feature_chaos_api_enabled
    settings.feature_chaos_api_enabled = True
    yield
    settings.feature_chaos_api_enabled = saved


@pytest.fixture(autouse=True)
def override_auth():
    """Override CurrentUserFromCookie to bypass auth for chaos API tests."""

    async def _mock_user():
        return MagicMock()

    app.dependency_overrides[get_current_user_from_cookie] = _mock_user
    yield
    app.dependency_overrides.pop(get_current_user_from_cookie, None)


@pytest.mark.unit
class TestChaosInject:
    """Tests for POST /api/v1/chaos/inject."""

    @pytest.mark.asyncio
    async def test_inject_circuit_breaker_open(self):
        mock_redis = MagicMock()
        mock_redis.set = AsyncMock()

        with patch("app.api.routes.chaos.get_redis_client", return_value=mock_redis):
            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as client:
                response = await client.post(
                    "/api/v1/chaos/inject",
                    data={"scenario": "circuit_breaker_open", "duration_seconds": 30},
                    headers={"X-CSRF-Token": "test-csrf"},
                    cookies={"csrf_token": "test-csrf"},
                )

        assert response.status_code == 200
        data = response.json()
        assert data["data"]["scenario"] == "circuit_breaker_open"
        assert data["data"]["duration_seconds"] == 30
        assert data["data"]["status"] == "active"
        mock_redis.set.assert_called_once()
        args, _kwargs = mock_redis.set.call_args
        assert args[0] == "chaos:circuit_breaker_override"
        assert args[1] == "1"

    @pytest.mark.asyncio
    async def test_inject_worker_crash(self):
        mock_redis = MagicMock()
        mock_redis.set = AsyncMock()

        with patch("app.api.routes.chaos.get_redis_client", return_value=mock_redis):
            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as client:
                response = await client.post(
                    "/api/v1/chaos/inject",
                    data={"scenario": "worker_crash", "duration_seconds": 30},
                    headers={"X-CSRF-Token": "test-csrf"},
                    cookies={"csrf_token": "test-csrf"},
                )

        assert response.status_code == 200
        mock_redis.set.assert_called_once()
        args, _kwargs = mock_redis.set.call_args
        assert args[0] == "chaos:zombie_job_trigger"

    @pytest.mark.asyncio
    async def test_inject_db_failover(self):
        mock_redis = MagicMock()
        mock_redis.set = AsyncMock()

        with patch("app.api.routes.chaos.get_redis_client", return_value=mock_redis):
            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as client:
                response = await client.post(
                    "/api/v1/chaos/inject",
                    data={"scenario": "db_failover", "duration_seconds": 30},
                    headers={"X-CSRF-Token": "test-csrf"},
                    cookies={"csrf_token": "test-csrf"},
                )

        assert response.status_code == 200
        mock_redis.set.assert_called_once()
        args, _kwargs = mock_redis.set.call_args
        assert args[0] == "chaos:db_failover"

    @pytest.mark.asyncio
    async def test_inject_throttle_spike(self):
        mock_redis = MagicMock()
        mock_redis.set = AsyncMock()
        mock_redis.zadd = AsyncMock(return_value=15)
        mock_redis.expire = AsyncMock()

        with patch("app.api.routes.chaos.get_redis_client", return_value=mock_redis):
            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as client:
                response = await client.post(
                    "/api/v1/chaos/inject",
                    data={"scenario": "throttle_spike", "duration_seconds": 30},
                    headers={"X-CSRF-Token": "test-csrf"},
                    cookies={"csrf_token": "test-csrf"},
                )

        assert response.status_code == 200
        mock_redis.set.assert_called_once()
        args, _kwargs = mock_redis.set.call_args
        assert args[0] == "chaos:throttle_spike"


@pytest.mark.unit
class TestChaosReset:
    """Tests for POST /api/v1/chaos/reset."""

    @pytest.mark.asyncio
    async def test_reset_deletes_all_chaos_keys(self):
        mock_redis = MagicMock()
        mock_redis.scan = AsyncMock(
            return_value=(
                0,
                [
                    "chaos:circuit_breaker_override",
                    "chaos:zombie_job_trigger",
                ],
            )
        )
        mock_redis.delete = AsyncMock()

        with patch("core.redis_client.get_redis_client", return_value=mock_redis):
            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as client:
                response = await client.post(
                    "/api/v1/chaos/reset",
                    headers={"X-CSRF-Token": "test-csrf"},
                    cookies={"csrf_token": "test-csrf"},
                )

        assert response.status_code == 200
        data = response.json()
        assert data["data"]["scenarios_reset"] == 2
        mock_redis.scan.assert_called_once()
        mock_redis.delete.assert_called_once()

    @pytest.mark.asyncio
    async def test_reset_no_keys(self):
        mock_redis = MagicMock()
        mock_redis.scan = AsyncMock(return_value=(0, []))
        mock_redis.delete = AsyncMock()

        with patch("core.redis_client.get_redis_client", return_value=mock_redis):
            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as client:
                response = await client.post(
                    "/api/v1/chaos/reset",
                    headers={"X-CSRF-Token": "test-csrf"},
                    cookies={"csrf_token": "test-csrf"},
                )

        assert response.status_code == 200
        data = response.json()
        assert data["data"]["scenarios_reset"] == 0
        mock_redis.delete.assert_not_called()


@pytest.mark.unit
class TestChaosStatus:
    """Tests for GET /api/v1/chaos/status."""

    @pytest.mark.asyncio
    async def test_status_all_inactive(self):
        mock_redis = MagicMock()
        mock_redis.exists = AsyncMock(return_value=0)

        with patch("app.api.routes.chaos.get_redis_client", return_value=mock_redis):
            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as client:
                response = await client.get("/api/v1/chaos/status")

        assert response.status_code == 200
        data = response.json()["data"]
        assert data["circuit_breaker_open"] is False
        assert data["worker_crash"] is False
        assert data["db_failover"] is False
        assert data["throttle_spike"] is False

    @pytest.mark.asyncio
    async def test_status_circuit_breaker_active(self):
        mock_redis = MagicMock()
        mock_redis.exists = AsyncMock(return_value=1)

        with patch("app.api.routes.chaos.get_redis_client", return_value=mock_redis):
            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as client:
                response = await client.get("/api/v1/chaos/status")

        assert response.status_code == 200
        data = response.json()["data"]
        assert data["circuit_breaker_open"] is True

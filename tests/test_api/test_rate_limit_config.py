"""Tests for rate limit configuration."""

from unittest.mock import MagicMock, patch

import pytest
from slowapi import Limiter

from app.api.rate_limit_config import (
    REDIS_STORAGE_URL,
    NoOpLimiter,
    _build_limiter,
    _parse_retry_after,
    _resolve_redis_storage_url,
    rate_limit_exceeded_handler,
)


class TestParseRetryAfter:
    """Tests for _parse_retry_after helper."""

    def test_parse_seconds(self):
        """Retry-After is the window length, not the request count."""
        result = _parse_retry_after("5 per 1 second")
        assert result == 1

    def test_parse_second_variant(self):
        """The "secs" spelling normalizes to the second multiplier."""
        result = _parse_retry_after("10 per 1 secs")
        assert result == 1

    def test_parse_minutes(self):
        """ "5 per 1 minute" means the client may retry after the 60s window."""
        result = _parse_retry_after("5 per 1 minute")
        assert result == 60

    def test_parse_minute_variant(self):
        """A multi-minute window scales by the window, not the limit."""
        result = _parse_retry_after("2 per 3 minutes")
        assert result == 180

    def test_parse_hours(self):
        """Test parsing hours unit."""
        result = _parse_retry_after("5 per 1 hour")
        assert result == 3600

    def test_parse_hour_variant(self):
        """Test parsing plural hours unit."""
        result = _parse_retry_after("1 per 2 hours")
        assert result == 7200

    def test_parse_days(self):
        """Test parsing days unit."""
        result = _parse_retry_after("5 per 1 day")
        assert result == 86400

    def test_parse_day_variant(self):
        """Test parsing plural days unit."""
        result = _parse_retry_after("2 per 1 days")
        assert result == 86400

    def test_parse_invalid_format_returns_default(self):
        """Test that invalid format returns default 60 seconds."""
        result = _parse_retry_after("invalid format")
        assert result == 60

    def test_parse_empty_string_returns_default(self):
        """Test that empty string returns default 60 seconds."""
        result = _parse_retry_after("")
        assert result == 60

    def test_parse_unknown_unit_defaults_to_60(self):
        """Test that unknown unit defaults to 60 multiplier (minute)."""
        result = _parse_retry_after("5 per 1 unknown")
        assert result == 60  # window (1) * minute multiplier (60)


class TestRateLimitExceededHandler:
    """Tests for rate_limit_exceeded_handler."""

    @pytest.mark.asyncio
    async def test_handler_returns_429_with_retry_after(self):
        """Test that handler returns 429 status with Retry-After header."""
        from fastapi import Request
        from slowapi.errors import RateLimitExceeded

        mock_request = MagicMock(spec=Request)
        mock_exc = MagicMock(spec=RateLimitExceeded)
        mock_exc.detail = "5 per 1 minute"

        response = await rate_limit_exceeded_handler(mock_request, mock_exc)

        assert response.status_code == 429
        assert "Retry-After" in response.headers
        assert response.headers["Retry-After"] == "60"

    @pytest.mark.asyncio
    async def test_handler_raises_non_rate_limit_exception(self):
        """Test that non-RateLimitExceeded exceptions are raised."""
        from fastapi import Request

        mock_request = MagicMock(spec=Request)
        mock_exc = ValueError("not a rate limit error")

        with pytest.raises(ValueError):
            await rate_limit_exceeded_handler(mock_request, mock_exc)


class TestRedisStorageConfig:
    """Tests for Redis-backed rate limit storage configuration."""

    def test_redis_storage_url_default(self, monkeypatch):
        """Default Redis URL falls back to local Redis."""
        monkeypatch.delenv("RATE_LIMIT_REDIS_URL", raising=False)
        monkeypatch.delenv("REDIS_URL", raising=False)
        assert _resolve_redis_storage_url() == "redis://localhost:6379"

    def test_redis_storage_url_prefers_rate_limit_var(self, monkeypatch):
        """RATE_LIMIT_REDIS_URL wins over REDIS_URL."""
        monkeypatch.setenv("RATE_LIMIT_REDIS_URL", "redis://rate:6379/1")
        monkeypatch.setenv("REDIS_URL", "redis://other:6379/2")
        assert _resolve_redis_storage_url() == "redis://rate:6379/1"

    def test_redis_storage_url_falls_back_to_redis_url(self, monkeypatch):
        """REDIS_URL env var is used when RATE_LIMIT_REDIS_URL is not set."""
        monkeypatch.delenv("RATE_LIMIT_REDIS_URL", raising=False)
        monkeypatch.setenv("REDIS_URL", "redis://custom-redis:6380/2")
        assert _resolve_redis_storage_url() == "redis://custom-redis:6380/2"

    def test_module_constant_is_redis_url(self):
        """Module-level REDIS_STORAGE_URL is a valid redis:// URL."""
        assert REDIS_STORAGE_URL.startswith("redis://")

    def test_noop_limiter_in_test_mode(self):
        """NoOpLimiter is used when TESTING=1."""
        with patch("app.api.rate_limit_config.is_testing", True):
            limiter = _build_limiter()
            assert isinstance(limiter, NoOpLimiter)

    def test_noop_limiter_limit_decorator(self):
        """NoOpLimiter.limit returns the original function unchanged."""
        limiter = NoOpLimiter()

        def dummy_func():
            return "test"

        decorated = limiter.limit("5/minute")(dummy_func)
        assert decorated is dummy_func
        assert decorated() == "test"

    @pytest.mark.asyncio
    async def test_noop_limiter_call(self):
        """NoOpLimiter.__call__ allows all requests."""
        limiter = NoOpLimiter()
        mock_request = MagicMock()

        result = await limiter(mock_request)
        assert result is None

    def test_build_limiter_uses_redis_storage(self):
        """_build_limiter wires the Redis URL into the storage in non-test mode."""
        with patch("app.api.rate_limit_config.is_testing", False):
            limiter = _build_limiter()

            assert isinstance(limiter, Limiter)
            assert limiter._storage_uri == REDIS_STORAGE_URL

    def test_build_limiter_redis_backend_is_limits_redis_storage(self):
        """The resolved backend is a Redis storage instance."""
        from limits.storage.redis import RedisStorage as LimitsRedisStorage

        with patch("app.api.rate_limit_config.is_testing", False):
            limiter = _build_limiter()

            assert isinstance(limiter._storage, LimitsRedisStorage)

    def test_build_limiter_gracefully_degrades_without_redis(self):
        """An unreachable Redis degrades to in-memory counters, never 500s."""
        with patch("app.api.rate_limit_config.is_testing", False):
            limiter = _build_limiter()

            assert limiter._in_memory_fallback_enabled is True
            assert limiter._swallow_errors is True

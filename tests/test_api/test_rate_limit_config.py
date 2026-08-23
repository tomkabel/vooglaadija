"""Tests for rate limit configuration."""

import os
from unittest.mock import MagicMock, patch

import pytest

from app.api.rate_limit_config import (
    REDIS_STORAGE_URL,
    NoOpLimiter,
    _build_limiter,
    _parse_retry_after,
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

    def test_redis_storage_url_default(self):
        """Default Redis URL falls back to local Redis."""
        assert REDIS_STORAGE_URL.startswith("redis://")

    def test_redis_storage_url_from_env(self):
        """REDIS_URL env var is used when RATE_LIMIT_REDIS_URL is not set."""
        with patch.dict(os.environ, {"REDIS_URL": "redis://custom-redis:6380/2"}):
            from app.api.rate_limit_config import REDIS_STORAGE_URL

            assert "redis://" in REDIS_STORAGE_URL

    def test_noop_limiter_in_test_mode(self):
        """NoOpLimiter is used when TESTING=1."""
        with patch.dict(os.environ, {"TESTING": "1"}):
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

    def test_noop_limiter_call(self):
        """NoOpLimiter.__call__ allows all requests."""
        import asyncio

        limiter = NoOpLimiter()
        mock_request = MagicMock()

        result = asyncio.get_event_loop().run_until_complete(limiter(mock_request))
        assert result is None

    def test_build_limiter_uses_redis_storage(self):
        """_build_limiter uses RedisStorage in non-test mode."""
        import sys
        from types import ModuleType

        mock_storage_instance = MagicMock()

        mock_storage_module = ModuleType("slowapi.storage")
        mock_storage_module.RedisStorage = MagicMock(return_value=mock_storage_instance)

        with patch.dict(sys.modules, {"slowapi.storage": mock_storage_module}):
            with patch.dict(os.environ, {"TESTING": "0"}):
                limiter = _build_limiter()

                mock_storage_module.RedisStorage.assert_called_once()
                assert limiter is not None

    def test_build_limiter_fallback_without_redis_import(self):
        """_build_limiter falls back to default Limiter if RedisStorage import fails."""
        with patch.dict(os.environ, {"TESTING": "0"}):
            with patch("builtins.__import__", side_effect=_import_raising_import_error):
                from slowapi import Limiter

                limiter = _build_limiter()
                assert isinstance(limiter, Limiter)


def _import_raising_import_error(name, *args, **kwargs):
    """Import helper that raises ImportError for slowapi.storage."""
    if name == "slowapi.storage":
        raise ImportError("No module named 'slowapi.storage'")
    return __import__(name, *args, **kwargs)

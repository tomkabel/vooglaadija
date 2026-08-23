"""Tests for rate limit configuration."""

import pytest

from app.api.rate_limit_config import _parse_retry_after, rate_limit_exceeded_handler


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
        from unittest.mock import MagicMock

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
        from unittest.mock import MagicMock

        from fastapi import Request

        mock_request = MagicMock(spec=Request)
        mock_exc = ValueError("not a rate limit error")

        with pytest.raises(ValueError):
            await rate_limit_exceeded_handler(mock_request, mock_exc)

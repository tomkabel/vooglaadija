"""Throttle predictor service tests."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.config import settings
from app.metrics import THROTTLE_RISK_SCORE


@pytest.fixture
def mock_redis():
    mock = AsyncMock()
    mock.set = AsyncMock()
    mock.zadd = AsyncMock(return_value=1)
    mock.zcard = AsyncMock(return_value=0)
    mock.zremrangebyscore = AsyncMock(return_value=0)
    mock.expire = AsyncMock(return_value=True)
    return mock


@pytest.fixture
def mock_redis_for_risk():
    mock = AsyncMock()
    mock.zadd = AsyncMock(return_value=1)
    mock.zremrangebyscore = AsyncMock(return_value=0)
    mock.expire = AsyncMock(return_value=True)
    return mock


@pytest.mark.unit
class TestRecordResponse:
    @pytest.mark.asyncio
    async def test_non_429_status_does_not_record(self, mock_redis):
        from app.services.throttle_predictor import record_response

        with patch("app.services.throttle_predictor.get_redis_client", return_value=mock_redis):
            await record_response("youtube", 200)

        mock_redis.zadd.assert_not_called()

    @pytest.mark.asyncio
    async def test_429_records_timestamp(self, mock_redis):
        from app.services.throttle_predictor import record_response

        with patch("app.services.throttle_predictor.get_redis_client", return_value=mock_redis):
            await record_response("youtube", 429)

        mock_redis.zremrangebyscore.assert_called_once()
        mock_redis.zadd.assert_called_once()
        args, _ = mock_redis.zadd.call_args
        assert args[0] == "throttle:window:youtube"

    @pytest.mark.asyncio
    async def test_429_prunes_old_entries(self, mock_redis):
        from app.services.throttle_predictor import record_response

        with patch("app.services.throttle_predictor.get_redis_client", return_value=mock_redis):
            await record_response("youtube", 429)

        mock_redis.zremrangebyscore.assert_called_once()
        call_args = mock_redis.zremrangebyscore.call_args
        assert call_args[0][0] == "throttle:window:youtube"
        assert call_args[0][1] == "-inf"

    @pytest.mark.asyncio
    async def test_429_sets_ttl_on_key(self, mock_redis):
        from app.services.throttle_predictor import record_response

        with patch("app.services.throttle_predictor.get_redis_client", return_value=mock_redis):
            await record_response("youtube", 429)

        mock_redis.expire.assert_called_once()
        key, ttl = mock_redis.expire.call_args[0]
        assert key == "throttle:window:youtube"
        assert ttl == settings.throttle_window_seconds * 2


@pytest.mark.unit
class TestGetRiskScore:
    @pytest.mark.asyncio
    async def test_zero_count_returns_zero(self, mock_redis_for_risk):
        from app.services.throttle_predictor import get_risk_score

        mock_redis_for_risk.zcard = AsyncMock(return_value=0)

        with patch(
            "app.services.throttle_predictor.get_redis_client", return_value=mock_redis_for_risk
        ):
            risk = await get_risk_score("youtube")

        assert risk == 0.0

    @pytest.mark.asyncio
    async def test_5_count_returns_0_5(self, mock_redis_for_risk):
        from app.services.throttle_predictor import get_risk_score

        mock_redis_for_risk.zcard = AsyncMock(return_value=5)

        with patch(
            "app.services.throttle_predictor.get_redis_client", return_value=mock_redis_for_risk
        ):
            risk = await get_risk_score("youtube")

        assert risk == 0.5

    @pytest.mark.asyncio
    async def test_10_count_returns_1_0(self, mock_redis_for_risk):
        from app.services.throttle_predictor import get_risk_score

        mock_redis_for_risk.zcard = AsyncMock(return_value=10)

        with patch(
            "app.services.throttle_predictor.get_redis_client", return_value=mock_redis_for_risk
        ):
            risk = await get_risk_score("youtube")

        assert risk == 1.0

    @pytest.mark.asyncio
    async def test_15_count_capped_at_1_0(self, mock_redis_for_risk):
        from app.services.throttle_predictor import get_risk_score

        mock_redis_for_risk.zcard = AsyncMock(return_value=15)

        with patch(
            "app.services.throttle_predictor.get_redis_client", return_value=mock_redis_for_risk
        ):
            risk = await get_risk_score("youtube")

        assert risk == 1.0


@pytest.mark.unit
class TestRiskCheckAndWarn:
    @pytest.mark.asyncio
    async def test_risk_0_5_no_warning(self, mock_redis_for_risk):
        from app.services.throttle_predictor import risk_check_and_warn

        mock_redis_for_risk.zcard = AsyncMock(return_value=5)

        with (
            patch(
                "app.services.throttle_predictor.get_redis_client", return_value=mock_redis_for_risk
            ),
            patch("app.services.throttle_predictor.logger.warning") as mock_warn,
        ):
            await risk_check_and_warn("youtube")

        mock_warn.assert_not_called()

    @pytest.mark.asyncio
    async def test_risk_0_8_triggers_warning(self, mock_redis_for_risk):
        from app.services.throttle_predictor import risk_check_and_warn

        mock_redis_for_risk.zcard = AsyncMock(return_value=8)

        with (
            patch(
                "app.services.throttle_predictor.get_redis_client", return_value=mock_redis_for_risk
            ),
            patch("app.services.throttle_predictor.logger.warning") as mock_warn,
        ):
            await risk_check_and_warn("youtube")

        mock_warn.assert_called_once()
        call_args = mock_warn.call_args
        assert call_args[0][0] == "throttle_risk_high"
        assert call_args[1]["service"] == "youtube"
        assert call_args[1]["risk_score"] == 0.8

    @pytest.mark.asyncio
    async def test_risk_0_7_at_threshold_triggers_warning(self, mock_redis_for_risk):
        from app.services.throttle_predictor import risk_check_and_warn

        mock_redis_for_risk.zcard = AsyncMock(return_value=7)

        with (
            patch(
                "app.services.throttle_predictor.get_redis_client", return_value=mock_redis_for_risk
            ),
            patch("app.services.throttle_predictor.logger.warning") as mock_warn,
        ):
            await risk_check_and_warn("youtube")

        mock_warn.assert_called_once()
        call_args = mock_warn.call_args
        assert call_args[1]["risk_score"] == 0.7


@pytest.mark.unit
class TestYtDlpStderrParsing:
    @pytest.mark.asyncio
    async def test_stderr_with_429_pattern_calls_record(self):
        from app.services.yt_dlp_service import _check_throttle

        mock_record = AsyncMock()

        with patch("app.services.throttle_predictor.record_response", mock_record):
            await _check_throttle("HTTP Error 429 Too Many Requests\n")

        mock_record.assert_called_once_with("youtube", 429)

    @pytest.mark.asyncio
    async def test_stderr_with_429_lowercase_matches(self):
        from app.services.yt_dlp_service import _check_throttle

        mock_record = AsyncMock()

        with patch("app.services.throttle_predictor.record_response", mock_record):
            await _check_throttle("http error 429: rate limit exceeded\n")

        mock_record.assert_called_once_with("youtube", 429)

    @pytest.mark.asyncio
    async def test_stderr_with_403_does_not_call_record(self):
        from app.services.yt_dlp_service import _check_throttle

        mock_record = AsyncMock()

        with patch("app.services.throttle_predictor.record_response", mock_record):
            await _check_throttle("HTTP Error 403 Forbidden\n")

        mock_record.assert_not_called()

    @pytest.mark.asyncio
    async def test_stderr_with_no_http_error_does_not_call_record(self):
        from app.services.yt_dlp_service import _check_throttle

        mock_record = AsyncMock()

        with patch("app.services.throttle_predictor.record_response", mock_record):
            await _check_throttle("ERROR: Unsupported URL\n")

        mock_record.assert_not_called()

    @pytest.mark.asyncio
    async def test_empty_stderr_does_not_call_record(self):
        from app.services.yt_dlp_service import _check_throttle

        mock_record = AsyncMock()

        with patch("app.services.throttle_predictor.record_response", mock_record):
            await _check_throttle("")

        mock_record.assert_not_called()

    @pytest.mark.asyncio
    async def test_none_is_empty_string_does_not_call_record(self):
        from app.services.yt_dlp_service import _check_throttle

        mock_record = AsyncMock()

        with patch("app.services.throttle_predictor.record_response", mock_record):
            await _check_throttle("")

        mock_record.assert_not_called()

    @pytest.mark.asyncio
    async def test_stderr_with_429_deep_in_text_matches(self):
        from app.services.yt_dlp_service import _check_throttle

        mock_record = AsyncMock()

        with patch("app.services.throttle_predictor.record_response", mock_record):
            await _check_throttle(
                "[download] Got error: HTTP Error 429 : Too Many Requests (caused by <HTTPError 429>)\n"
            )

        mock_record.assert_called_once_with("youtube", 429)


@pytest.mark.unit
class TestThrottleSpikeIntegration:
    @pytest.mark.asyncio
    async def test_inject_throttle_spike_adds_timestamps(self):
        from httpx import ASGITransport, AsyncClient

        from app.api.dependencies import CurrentUserFromCookie
        from app.main import app

        saved = settings.feature_chaos_api_enabled
        settings.feature_chaos_api_enabled = True

        mock_redis = AsyncMock()
        mock_redis.set = AsyncMock()
        mock_redis.zadd = AsyncMock(return_value=15)
        mock_redis.expire = AsyncMock(return_value=True)

        async def _mock_user():
            return MagicMock()

        app.dependency_overrides[CurrentUserFromCookie] = _mock_user

        try:
            with patch("app.api.routes.chaos.get_redis_client", return_value=mock_redis):
                async with AsyncClient(
                    transport=ASGITransport(app=app), base_url="http://test"
                ) as client:
                    response = await client.post(
                        "/api/v1/chaos/inject",
                        json={"scenario": "throttle_spike", "duration_seconds": 30},
                        headers={"X-CSRF-Token": "test-csrf"},
                        cookies={"csrf_token": "test-csrf"},
                    )

            assert response.status_code == 200
            data = response.json()
            assert data["data"]["scenario"] == "throttle_spike"
            assert data["data"]["status"] == "active"

            mock_redis.set.assert_called_once()
            mock_redis.zadd.assert_called_once()
            mock_redis.expire.assert_called_once()

            zadd_args = mock_redis.zadd.call_args[0]
            spike_data = zadd_args[1]
            assert len(spike_data) == 15

        finally:
            settings.feature_chaos_api_enabled = saved
            app.dependency_overrides.pop(CurrentUserFromCookie, None)

    @pytest.mark.asyncio
    async def test_inject_throttle_spike_sets_gauge(self):
        from httpx import ASGITransport, AsyncClient

        initial_value = THROTTLE_RISK_SCORE.labels(
            service="youtube", provider="yt-dlp"
        )._value.get()

        mock_redis = AsyncMock()
        mock_redis.set = AsyncMock()
        mock_redis.zadd = AsyncMock(return_value=15)
        mock_redis.expire = AsyncMock(return_value=True)

        from unittest.mock import MagicMock

        from app.api.dependencies import CurrentUserFromCookie
        from app.main import app

        saved = settings.feature_chaos_api_enabled
        settings.feature_chaos_api_enabled = True

        async def _mock_user():
            return MagicMock()

        app.dependency_overrides[CurrentUserFromCookie] = _mock_user

        try:
            with patch("app.api.routes.chaos.get_redis_client", return_value=mock_redis):
                async with AsyncClient(
                    transport=ASGITransport(app=app), base_url="http://test"
                ) as client:
                    await client.post(
                        "/api/v1/chaos/inject",
                        json={"scenario": "throttle_spike", "duration_seconds": 30},
                        headers={"X-CSRF-Token": "test-csrf"},
                        cookies={"csrf_token": "test-csrf"},
                    )

            current_value = THROTTLE_RISK_SCORE.labels(
                service="youtube", provider="yt-dlp"
            )._value.get()
            assert current_value == 1.0

        finally:
            settings.feature_chaos_api_enabled = saved
            app.dependency_overrides.pop(CurrentUserFromCookie, None)
            THROTTLE_RISK_SCORE.labels(service="youtube", provider="yt-dlp").set(initial_value)

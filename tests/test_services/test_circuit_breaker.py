"""Circuit Breaker Service Tests."""

from unittest.mock import AsyncMock, patch

import pytest

from app.services.circuit_breaker import (
    CircuitBreaker,
    CircuitBreakerOpenError,
    CircuitState,
    extract_media_with_circuit_breaker,
    get_circuit_breaker_stats,
    get_youtube_circuit_breaker,
)


@pytest.fixture(autouse=True)
def reset_circuit_breaker_singleton():
    """Reset the global singleton before and after each test."""
    import app.services.circuit_breaker as cb_module

    saved = cb_module._youtube_circuit_breaker
    cb_module._youtube_circuit_breaker = None
    yield
    cb_module._youtube_circuit_breaker = saved


class TestCircuitBreakerInitialization:
    """Tests for CircuitBreaker initialization and properties."""

    def test_default_initialization(self):
        """Assert CLOSED state, counters at 0."""
        cb = CircuitBreaker(name="test")
        assert cb.name == "test"
        assert cb._state == CircuitState.CLOSED
        assert cb._failure_count == 0
        assert cb._success_count == 0
        assert cb._half_open_calls == 0
        assert cb._last_failure_time is None

    def test_custom_thresholds(self):
        """Verify custom failure_threshold, success_threshold, reset_timeout."""
        cb = CircuitBreaker(
            name="custom",
            failure_threshold=10,
            success_threshold=5,
            reset_timeout=60.0,
            half_open_max_calls=2,
        )
        assert cb.failure_threshold == 10
        assert cb.success_threshold == 5
        assert cb.reset_timeout == 60.0
        assert cb.half_open_max_calls == 2


class TestCircuitBreakerClosedState:
    """Tests for CLOSED state logic."""

    @pytest.mark.asyncio
    async def test_can_execute_returns_true_when_closed(self):
        """Test can_execute returns True when circuit is CLOSED."""
        cb = CircuitBreaker(name="test")
        result = await cb.can_execute()
        assert result is True

    @pytest.mark.asyncio
    async def test_record_success_resets_failure_count(self):
        """Assert failure_count resets to 0 and logging occurs."""
        cb = CircuitBreaker(name="test")
        cb._failure_count = 3
        await cb.record_success()
        assert cb._failure_count == 0

    @pytest.mark.asyncio
    async def test_record_failure_increments_count(self):
        """Test that record_failure increments the failure counter."""
        cb = CircuitBreaker(name="test")
        await cb.record_failure(Exception("test error"))
        assert cb._failure_count == 1

    @pytest.mark.asyncio
    async def test_closed_to_open_transition(self):
        """Record failure_threshold failures, assert state becomes OPEN."""
        cb = CircuitBreaker(name="test", failure_threshold=3)
        for _ in range(3):
            await cb.record_failure(Exception("test"))
        assert cb._state == CircuitState.OPEN

    @pytest.mark.asyncio
    async def test_failure_count_does_not_exceed_threshold_before_opening(self):
        """Assert stays CLOSED at failure_threshold - 1."""
        cb = CircuitBreaker(name="test", failure_threshold=3)
        for _ in range(2):
            await cb.record_failure(Exception("test"))
        assert cb._state == CircuitState.CLOSED
        assert cb._failure_count == 2


class TestCircuitBreakerOpenState:
    """Tests for OPEN state logic."""

    @pytest.mark.asyncio
    async def test_can_execute_returns_false_when_open(self):
        """Test can_execute returns False when circuit is OPEN."""
        cb = CircuitBreaker(name="test", failure_threshold=1)
        await cb.record_failure(Exception("test"))
        assert cb._state == CircuitState.OPEN
        result = await cb.can_execute()
        assert result is False

    @pytest.mark.asyncio
    async def test_state_property_does_not_transition_without_lock(self):
        """state property returns current _state without OPEN→HALF_OPEN transition.

        The transition only happens under the async lock via can_execute().
        The state property is read-only and must not mutate state.
        """
        cb = CircuitBreaker(name="test", failure_threshold=1, reset_timeout=30.0)
        cb._state = CircuitState.OPEN
        cb._last_failure_time = 0.0

        with patch("app.services.circuit_breaker.time.monotonic", return_value=31.0):
            state = cb.state
            # State property returns stored _state without mutating it
            assert state == CircuitState.OPEN

    @pytest.mark.asyncio
    async def test_check_and_transition_to_half_open_under_lock(self):
        """Call can_execute after timeout; assert state mutates to HALF_OPEN."""
        cb = CircuitBreaker(name="test", failure_threshold=1, reset_timeout=30.0)
        cb._state = CircuitState.OPEN
        cb._last_failure_time = 0.0

        with patch("app.services.circuit_breaker.time.monotonic", return_value=31.0):
            await cb.can_execute()
            assert cb._state == CircuitState.HALF_OPEN
            assert cb._last_failure_time is None

    @pytest.mark.asyncio
    async def test_open_state_does_not_transition_before_timeout(self):
        """Assert remains OPEN when elapsed < reset_timeout."""
        cb = CircuitBreaker(name="test", failure_threshold=1, reset_timeout=30.0)
        cb._state = CircuitState.OPEN
        cb._last_failure_time = 0.0

        with patch("app.services.circuit_breaker.time.monotonic", return_value=15.0):
            result = await cb.can_execute()
            assert result is False
            assert cb._state == CircuitState.OPEN


class TestCircuitBreakerHalfOpenState:
    """Tests for HALF_OPEN state logic."""

    @pytest.mark.asyncio
    async def test_can_execute_allows_limited_calls(self):
        """Assert True up to half_open_max_calls, then False."""
        cb = CircuitBreaker(name="test", half_open_max_calls=2)
        cb._state = CircuitState.HALF_OPEN

        assert await cb.can_execute() is True
        assert cb._half_open_calls == 1
        assert await cb.can_execute() is True
        assert cb._half_open_calls == 2
        assert await cb.can_execute() is False

    @pytest.mark.asyncio
    async def test_record_success_in_half_open_increments_success_count(self):
        """Test success_count increments in HALF_OPEN state."""
        cb = CircuitBreaker(name="test")
        cb._state = CircuitState.HALF_OPEN
        await cb.record_success()
        assert cb._success_count == 1

    @pytest.mark.asyncio
    async def test_half_open_to_closed_transition(self):
        """Record success_threshold successes, assert state becomes CLOSED."""
        cb = CircuitBreaker(name="test", success_threshold=2)
        cb._state = CircuitState.HALF_OPEN
        cb._half_open_calls = 1

        await cb.record_success()
        assert cb._state == CircuitState.HALF_OPEN
        await cb.record_success()
        assert cb._state == CircuitState.CLOSED
        assert cb._failure_count == 0
        assert cb._success_count == 0

    @pytest.mark.asyncio
    async def test_record_failure_in_half_open_reopens_circuit(self):
        """Assert returns to OPEN, success_count reset, half_open_calls reset."""
        cb = CircuitBreaker(name="test")
        cb._state = CircuitState.HALF_OPEN
        cb._half_open_calls = 2
        cb._success_count = 1

        await cb.record_failure(Exception("test"))

        assert cb._state == CircuitState.OPEN
        assert cb._success_count == 0
        assert cb._half_open_calls == 0


class TestCircuitBreakerExecute:
    """Tests for execute() wrapper."""

    @pytest.mark.asyncio
    async def test_execute_returns_result_on_success(self):
        """Mock async function, assert result returned and record_success called."""
        cb = CircuitBreaker(name="test")
        mock_func = AsyncMock(return_value="success_result")

        result = await cb.execute(mock_func, "arg1", kwarg1="value")

        assert result == "success_result"
        mock_func.assert_called_once_with("arg1", kwarg1="value")

    @pytest.mark.asyncio
    async def test_execute_raises_circuit_breaker_open_error(self):
        """When open, assert CircuitBreakerOpenError with correct service_name."""
        cb = CircuitBreaker(name="myservice", failure_threshold=1, reset_timeout=30.0)
        await cb.record_failure(Exception("test"))

        with pytest.raises(CircuitBreakerOpenError) as exc_info:
            await cb.execute(AsyncMock(return_value="result"))

        assert exc_info.value.service_name == "myservice"
        assert exc_info.value.reset_timeout == 30.0

    @pytest.mark.asyncio
    async def test_execute_records_failure_and_re_raises(self):
        """Mock function that raises; assert exception propagates and record_failure called."""
        cb = CircuitBreaker(name="test")
        error = Exception("test error")
        mock_func = AsyncMock(side_effect=error)

        with pytest.raises(Exception) as exc_info:
            await cb.execute(mock_func)

        assert exc_info.value is error


class TestCircuitBreakerStateProperties:
    """Tests for state properties."""

    @pytest.mark.asyncio
    async def test_is_closed_property(self):
        """Test is_closed returns True only in CLOSED state."""
        cb = CircuitBreaker(name="test")
        assert cb.is_closed is True
        cb._state = CircuitState.OPEN
        assert cb.is_closed is False

    @pytest.mark.asyncio
    async def test_is_open_property(self):
        """Test is_open returns True only in OPEN state."""
        cb = CircuitBreaker(name="test", failure_threshold=1)
        assert cb.is_open is False
        await cb.record_failure(Exception("test"))
        assert cb.is_open is True

    @pytest.mark.asyncio
    async def test_is_half_open_property(self):
        """Test is_half_open returns True only in HALF_OPEN state."""
        cb = CircuitBreaker(name="test")
        assert cb.is_half_open is False
        cb._state = CircuitState.HALF_OPEN
        assert cb.is_half_open is True


class TestGlobalSingleton:
    """Tests for global singleton and stats."""

    def test_get_youtube_circuit_breaker_returns_singleton(self):
        """Test that multiple calls return the same instance."""
        cb1 = get_youtube_circuit_breaker()
        cb2 = get_youtube_circuit_breaker()
        assert cb1 is cb2

    def test_get_youtube_circuit_breaker_reads_env_vars(self):
        """Test that env vars are read for configuration."""
        with patch.dict(
            "os.environ",
            {
                "CIRCUIT_BREAKER_FAILURE_THRESHOLD": "10",
                "CIRCUIT_BREAKER_SUCCESS_THRESHOLD": "5",
                "CIRCUIT_BREAKER_RESET_TIMEOUT": "60.0",
                "CIRCUIT_BREAKER_HALF_OPEN_MAX_CALLS": "2",
            },
        ):
            import app.services.circuit_breaker as cb_module

            cb_module._youtube_circuit_breaker = None
            cb = get_youtube_circuit_breaker()
            assert cb.failure_threshold == 10
            assert cb.success_threshold == 5
            assert cb.reset_timeout == 60.0
            assert cb.half_open_max_calls == 2

    def test_get_circuit_breaker_stats_structure(self):
        """Assert dict contains expected keys and values."""
        cb = get_youtube_circuit_breaker()
        stats = cb.get_stats()

        assert "name" in stats
        assert "state" in stats
        assert "failure_count" in stats
        assert "success_count" in stats
        assert "failure_threshold" in stats
        assert "success_threshold" in stats
        assert "reset_timeout" in stats
        assert "last_failure_time" in stats
        assert stats["name"] == "youtube_api"


class TestExtractMediaWithCircuitBreaker:
    """Tests for extract_media_with_circuit_breaker."""

    @pytest.mark.asyncio
    async def test_extract_media_wraps_yt_dlp_call(self):
        """Patch _extract_media_url_internal, assert called with args."""
        with patch(
            "app.services.circuit_breaker._extract_media_url_internal",
            new_callable=AsyncMock,
            return_value=("file_path", "title"),
        ) as mock_extract:
            result = await extract_media_with_circuit_breaker(
                "https://youtube.com/watch?v=test", "/storage"
            )
            assert result == ("file_path", "title")
            mock_extract.assert_called_once_with(
                "https://youtube.com/watch?v=test", "/storage", progress_callback=None
            )

    @pytest.mark.asyncio
    async def test_extract_media_logs_circuit_state(self):
        """Assert debug log emitted with circuit state."""
        with patch(
            "app.services.circuit_breaker._extract_media_url_internal",
            new_callable=AsyncMock,
            return_value=("file_path", "title"),
        ):
            with patch("app.services.circuit_breaker.logger") as mock_logger:
                await extract_media_with_circuit_breaker(
                    "https://youtube.com/watch?v=test", "/storage"
                )
                mock_logger.debug.assert_called_once()
                call_args = mock_logger.debug.call_args
                assert "circuit_state" in call_args.kwargs or "circuit_state" in str(call_args)


class TestGetCircuitBreakerStats:
    """Tests for get_circuit_breaker_stats()."""

    def test_get_circuit_breaker_stats_returns_dict(self):
        """Test that stats function returns a dictionary."""
        stats = get_circuit_breaker_stats()
        assert isinstance(stats, dict)
        assert stats["name"] == "youtube_api"

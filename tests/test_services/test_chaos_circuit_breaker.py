"""Circuit breaker chaos override tests.

Tests the per-instance chaos override cache on CircuitBreaker
(no module-level state, no test pollution).
"""

import time
from unittest.mock import AsyncMock, patch

import pytest

from app.services.circuit_breaker import CircuitBreaker, CircuitState


@pytest.mark.unit
class TestChaosOverrideCache:
    """Tests for the per-instance TTL cache behavior."""

    @pytest.mark.asyncio
    async def test_cache_returns_cached_value_when_warm(self):
        """Cached value (True or False) returns immediately within TTL."""
        cb = CircuitBreaker(name="youtube_api")
        cb._chaos_cache = (time.monotonic(), True)

        with patch(
            "core.redis_client.check_chaos_key",
            side_effect=Exception("should not be called"),
        ):
            result = await cb._is_chaos_override_active()

        assert result is True

    @pytest.mark.asyncio
    async def test_cache_returns_false_when_warm_and_inactive(self):
        """Cached False value returns immediately within TTL."""
        cb = CircuitBreaker(name="youtube_api")
        cb._chaos_cache = (time.monotonic(), False)

        with patch(
            "core.redis_client.check_chaos_key",
            side_effect=Exception("should not be called"),
        ):
            result = await cb._is_chaos_override_active()

        assert result is False

    @pytest.mark.asyncio
    async def test_cache_expired_rechecks_via_redis(self):
        """Expired cache re-evaluates via Redis."""
        cb = CircuitBreaker(name="youtube_api")
        cb._chaos_cache = (time.monotonic() - 2.0, False)

        with patch(
            "core.redis_client.check_chaos_key",
            AsyncMock(return_value=True),
        ):
            result = await cb._is_chaos_override_active()

        assert result is True
        assert cb._chaos_cache is not None
        assert cb._chaos_cache[1] is True


@pytest.mark.unit
class TestCircuitBreakerChaosOverride:
    """Tests for circuit breaker can_execute with chaos override."""

    @pytest.mark.asyncio
    async def test_can_execute_false_when_override_active(self):
        """can_execute returns False when chaos override cache says active."""
        cb = CircuitBreaker(name="youtube_api")
        cb._chaos_cache = (time.monotonic(), True)

        result = await cb.can_execute()

        assert result is False

    @pytest.mark.asyncio
    async def test_can_execute_true_when_override_inactive(self):
        """Normal operation when chaos override is not active."""
        cb = CircuitBreaker(name="youtube_api")
        cb._chaos_cache = (time.monotonic(), False)

        result = await cb.can_execute()

        assert result is True

    @pytest.mark.asyncio
    async def test_can_execute_false_when_override_active_even_when_closed(self):
        """Chaos override takes precedence over actual CB state."""
        cb = CircuitBreaker(name="youtube_api")
        cb._state = CircuitState.CLOSED
        cb._chaos_cache = (time.monotonic(), True)

        result = await cb.can_execute()

        assert result is False


@pytest.mark.unit
class TestCircuitBreakerMetrics:
    """Tests for Prometheus metrics on state transitions."""

    @pytest.mark.asyncio
    async def test_recovery_counter_on_open_to_half_open(self):
        """RECOVERIES counter increments on OPEN->HALF_OPEN transition."""
        from core.metrics import RECOVERIES

        cb = CircuitBreaker(name="youtube_api", reset_timeout=0.01)
        cb._state = CircuitState.OPEN
        cb._last_failure_time = time.monotonic() - 1.0

        initial = RECOVERIES.labels(reason="circuit_breaker_recovery")._value.get()

        async with cb._lock:
            cb._check_and_transition_to_half_open()

        new_value = RECOVERIES.labels(reason="circuit_breaker_recovery")._value.get()
        assert new_value == initial + 1

    @pytest.mark.asyncio
    async def test_state_gauge_on_close_to_open(self):
        """State gauge changes to 1 when transitioning CLOSED->OPEN."""
        from core.metrics import CIRCUIT_BREAKER_STATE

        cb = CircuitBreaker(name="youtube_api", failure_threshold=1)

        await cb.record_failure(Exception("test"))

        gauge_value = CIRCUIT_BREAKER_STATE.labels(service="youtube_api")._value.get()
        assert gauge_value == 1  # OPEN

    @pytest.mark.asyncio
    async def test_state_gauge_on_half_open_to_closed(self):
        """State gauge returns to 0 when transitioning HALF_OPEN->CLOSED."""
        from core.metrics import CIRCUIT_BREAKER_STATE

        cb = CircuitBreaker(name="youtube_api", success_threshold=1)
        cb._state = CircuitState.HALF_OPEN
        cb._half_open_calls = 1

        await cb.record_success()

        gauge_value = CIRCUIT_BREAKER_STATE.labels(service="youtube_api")._value.get()
        assert gauge_value == 0  # CLOSED

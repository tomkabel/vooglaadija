"""Integration tests: full circuit breaker chaos flow."""

import time

import pytest

from app.config import settings
from app.metrics import CIRCUIT_BREAKER_STATE, RECOVERIES
from app.services.circuit_breaker import CircuitBreaker, CircuitState


@pytest.fixture(autouse=True)
def enable_chaos_feature():
    """Enable the chaos feature flag for integration tests."""
    saved = settings.feature_chaos_api_enabled
    settings.feature_chaos_api_enabled = True
    yield
    settings.feature_chaos_api_enabled = saved


@pytest.mark.integration
class TestCircuitBreakerChaosFlow:
    """Full flow: inject → CB opens → CB recovers."""

    @pytest.mark.asyncio
    async def test_inject_circuit_breaker_opens_and_recovers(self):
        """Test full chaos flow: set instance cache, verify OPEN, clear and verify CLOSED."""
        cb = CircuitBreaker(name="youtube_api")

        assert await cb.can_execute() is True

        cb._chaos_cache = (time.monotonic(), True)
        assert await cb.can_execute() is False

        cb._chaos_cache = (time.monotonic(), False)
        assert await cb.can_execute() is True

    @pytest.mark.asyncio
    async def test_circuit_breaker_recovery_counter(self):
        """Verify recovery counter increments on OPEN→HALF_OPEN transition."""
        initial = RECOVERIES.labels(reason="circuit_breaker_recovery")._value.get()

        cb = CircuitBreaker(name="youtube_api")
        cb._state = CircuitState.OPEN
        cb._last_failure_time = time.monotonic() - 10.0
        cb.reset_timeout = 0.01

        async with cb._lock:
            cb._check_and_transition_to_half_open()

        new_value = RECOVERIES.labels(reason="circuit_breaker_recovery")._value.get()
        assert new_value == initial + 1

    @pytest.mark.asyncio
    async def test_state_gauge_reflects_transitions(self):
        """Verify state gauge correctly reflects CLOSED→OPEN transition."""
        cb = CircuitBreaker(name="youtube_api")

        await cb.record_failure(Exception("err1"))
        await cb.record_failure(Exception("err2"))
        await cb.record_failure(Exception("err3"))
        await cb.record_failure(Exception("err4"))
        await cb.record_failure(Exception("err5"))

        gauge_value = CIRCUIT_BREAKER_STATE.labels(service="youtube_api")._value.get()
        assert gauge_value == 1  # OPEN

        cb._state = CircuitState.OPEN
        cb._last_failure_time = time.monotonic() - 10.0
        cb.reset_timeout = 0.01

        async with cb._lock:
            cb._check_and_transition_to_half_open()

        gauge_value = CIRCUIT_BREAKER_STATE.labels(service="youtube_api")._value.get()
        assert gauge_value == 2  # HALF_OPEN

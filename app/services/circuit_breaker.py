"""
Circuit Breaker pattern for external API calls.

Based on 2026 industry best practices for resilience engineering.
Prevents thundering herd problem when external services (like YouTube) are down.

States:
- CLOSED: Normal operation, requests pass through
- OPEN: Circuit is tripped, requests fail immediately
- HALF_OPEN: Testing if service recovered, limited requests pass through

Transitions:
- CLOSED → OPEN: After failure_threshold consecutive failures
- OPEN → HALF_OPEN: After reset_timeout elapsed
- HALF_OPEN → CLOSED: After success_threshold consecutive successes
- HALF_OPEN → OPEN: After failure in half-open state
"""

import asyncio
import os
import time
from collections.abc import Awaitable, Callable
from enum import Enum
from typing import Any

from app.logging_config import get_logger
from app.metrics import CIRCUIT_BREAKER_STATE, RECOVERIES
from app.services import redis_client

logger = get_logger(__name__)


class CircuitState(Enum):
    """Circuit breaker states."""

    CLOSED = "closed"  # Normal operation
    OPEN = "open"  # Circuit tripped, failing fast
    HALF_OPEN = "half_open"  # Testing recovery


class CircuitBreakerOpenError(Exception):
    """Raised when circuit breaker is open and request cannot proceed."""

    def __init__(self, service_name: str, reset_timeout: float):
        self.service_name = service_name
        self.reset_timeout = reset_timeout
        super().__init__(
            f"Circuit breaker is OPEN for {service_name}. "
            f"Service will be retried after {reset_timeout}s cooldown."
        )


class CircuitBreaker:
    """
    Circuit breaker implementation for external service calls.

    Tracks failures and opens the circuit when threshold is exceeded,
    preventing cascading failures and thundering herd problems.
    """

    REDIS_PREFIX = "cb"

    def __init__(
        self,
        name: str,
        failure_threshold: int = 5,
        success_threshold: int = 3,
        reset_timeout: float = 30.0,
        half_open_max_calls: int = 3,
        use_redis_distributed: bool = False,
    ):
        """
        Initialize circuit breaker.

        Args:
            name: Service name for logging
            failure_threshold: Consecutive failures before opening circuit
            success_threshold: Consecutive successes to close circuit from half-open
            reset_timeout: Seconds before attempting recovery (open → half-open)
            half_open_max_calls: Max concurrent calls in half-open state
            use_redis_distributed: If True, share failure count and half-open
                slot state across all worker processes via Redis. Requires that
                the redis_client imported at the top of this module is connected.
        """
        self.name = name
        self.failure_threshold = failure_threshold
        self.success_threshold = success_threshold
        self.reset_timeout = reset_timeout
        self.half_open_max_calls = half_open_max_calls
        self._use_redis = use_redis_distributed

        # Redis key namespace for this breaker
        self._cb_key = f"{self.REDIS_PREFIX}:{name}"

        # State tracking
        self._state = CircuitState.CLOSED
        self._failure_count = 0
        self._success_count = 0
        self._last_failure_time: float | None = None
        self._half_open_calls = 0
        self._lock = asyncio.Lock()

        # Publish initial gauge state so Prometheus has a data point from startup.
        # Without this, the metric only appears after a state transition, causing
        # a "No data" state in Grafana until the first failure or recovery event.
        CIRCUIT_BREAKER_STATE.labels(service=self.name).set(0)

        # Chaos override cache (per-instance, no module-level global)
        # TTL is 0.5s so demo recovery is visible within 500ms
        self._chaos_cache: tuple[float, bool] | None = None
        self._chaos_cache_ttl: float = 0.5

    @property
    def state(self) -> CircuitState:
        """Get current circuit state.

        Returns the actual stored state WITHOUT speculatively transitioning
        OPEN→HALF_OPEN. Transitions are only performed by
        _check_and_transition_to_half_open() under the async lock, so
        this property is consistent for read-only access.
        """
        return self._state

    def _check_and_transition_to_half_open(self) -> CircuitState:
        """Check timeout and transition OPEN→HALF_OPEN under lock.

        Returns the current state after potential transition.
        """
        if self._state == CircuitState.OPEN and self._last_failure_time is not None:
            elapsed = time.monotonic() - self._last_failure_time
            if elapsed >= self.reset_timeout:
                logger.info(
                    "circuit_breaker_reset_timeout_elapsed",
                    service=self.name,
                    elapsed_seconds=elapsed,
                    reset_timeout=self.reset_timeout,
                )
                self._state = CircuitState.HALF_OPEN
                self._last_failure_time = None  # Reset timer
                RECOVERIES.labels(reason="circuit_breaker_recovery").inc()
                CIRCUIT_BREAKER_STATE.labels(service=self.name).set(2)
        return self._state

    @property
    def is_closed(self) -> bool:
        """Check if circuit is closed (normal operation)."""
        return self.state == CircuitState.CLOSED

    @property
    def is_open(self) -> bool:
        """Check if circuit is open (failing fast)."""
        return self.state == CircuitState.OPEN

    @property
    def is_half_open(self) -> bool:
        """Check if circuit is half-open (testing recovery)."""
        return self.state == CircuitState.HALF_OPEN

    async def _is_chaos_override_active(self) -> bool:
        """Check chaos circuit-breaker override with per-instance TTL cache.

        Returns the cached value if TTL (0.5s) has not elapsed, otherwise
        re-checks Redis. This avoids a Redis round-trip on every call while
        detecting recovery within 500ms for demo responsiveness.
        """
        now = time.monotonic()

        if self._chaos_cache is not None:
            timestamp, cached = self._chaos_cache
            if (now - timestamp) < self._chaos_cache_ttl:
                return cached

        is_active = await redis_client.check_chaos_key(redis_client.CHAOS_CIRCUIT_BREAKER_KEY)
        self._chaos_cache = (now, is_active)
        return is_active

    # -- Redis-backed distributed state helpers ---------------------------------

    async def _distributed_failure_count(self) -> int:
        """Return the shared failure count from Redis, or 0."""
        try:
            client = redis_client.get_redis_client()
            val = await client.get(f"{self._cb_key}:failures")
            return int(val) if val else 0
        except Exception:
            return self._failure_count  # fall back to local

    async def _increment_failure_distributed(self) -> int:
        """Atomically increment the shared failure counter with a TTL failsafe."""
        try:
            client = redis_client.get_redis_client()
            pipe = client.pipeline()
            pipe.incr(f"{self._cb_key}:failures")
            pipe.expire(f"{self._cb_key}:failures", 120)
            results = await pipe.execute()
            return int(results[0])
        except Exception:
            self._failure_count += 1
            return self._failure_count

    async def _reset_failures_distributed(self) -> None:
        """Clear the shared failure counter."""
        try:
            client = redis_client.get_redis_client()
            await client.delete(f"{self._cb_key}:failures")
        except Exception:
            self._failure_count = 0

    async def _try_acquire_half_open_slot(self) -> bool:
        """Acquire a half-open slot in the shared pool.

        At most half_open_max_calls slots can be held across all workers.
        """
        if not self._use_redis:
            if self._half_open_calls < self.half_open_max_calls:
                self._half_open_calls += 1
                return True
            return False
        try:
            client = redis_client.get_redis_client()
            slot_key = f"{self._cb_key}:half_slots"
            current = await client.incr(slot_key)
            await client.expire(slot_key, 60)
            if current <= self.half_open_max_calls:
                return True
            await client.decr(slot_key)
            return False
        except Exception:
            # Fall back to local slot management if Redis is down
            if self._half_open_calls < self.half_open_max_calls:
                self._half_open_calls += 1
                return True
            return False

    async def _release_half_open_slot(self) -> None:
        """Release a previously acquired half-open slot."""
        try:
            if self._use_redis:
                client = redis_client.get_redis_client()
                slot_key = f"{self._cb_key}:half_slots"
                await client.decr(slot_key)
            else:
                self._half_open_calls = max(0, self._half_open_calls - 1)
        except Exception:
            self._half_open_calls = max(0, self._half_open_calls - 1)

    async def _distributed_last_failure(self) -> float | None:
        """Return the shared last-failure timestamp from Redis."""
        try:
            client = redis_client.get_redis_client()
            val = await client.get(f"{self._cb_key}:last_failure")
            return float(val) if val else None
        except Exception:
            return self._last_failure_time

    async def _set_last_failure_distributed(self) -> None:
        """Write the shared last-failure timestamp."""
        try:
            client = redis_client.get_redis_client()
            await client.setex(f"{self._cb_key}:last_failure", 120, time.time())
        except Exception:
            self._last_failure_time = time.monotonic()

    async def can_execute(self) -> bool:
        """Check if request can proceed."""
        if await self._is_chaos_override_active():
            return False

        async with self._lock:
            # Distributed: read shared failure count and last-failure timestamp
            d_failures = await self._distributed_failure_count()
            d_last_failure = await self._distributed_last_failure()

            # If distributed state says OPEN, propagate it locally
            if (
                self._use_redis
                and d_failures >= self.failure_threshold
                and d_last_failure is not None
            ):
                if self._state != CircuitState.OPEN:
                    elapsed = time.time() - d_last_failure
                    if elapsed < self.reset_timeout:
                        self._state = CircuitState.OPEN
                        self._last_failure_time = time.monotonic()
                        self._failure_count = d_failures
                        CIRCUIT_BREAKER_STATE.labels(service=self.name).set(1)

            # First: check for timeout-based transition OPEN→HALF_OPEN under lock
            current_state = self._check_and_transition_to_half_open()

            if current_state == CircuitState.CLOSED:
                return True

            if current_state == CircuitState.OPEN:
                return False

            # HALF_OPEN: allow limited concurrent calls via shared slot pool
            if current_state == CircuitState.HALF_OPEN:
                if await self._try_acquire_half_open_slot():
                    return True
                return False

            return False

    async def record_success(self) -> None:
        """Record a successful call."""
        async with self._lock:
            await self._release_half_open_slot()

            if self._state == CircuitState.HALF_OPEN:
                self._success_count += 1
                logger.info(
                    "circuit_breaker_success_in_half_open",
                    service=self.name,
                    success_count=self._success_count,
                    success_threshold=self.success_threshold,
                )

                if self._success_count >= self.success_threshold:
                    logger.info(
                        "circuit_breaker_closing",
                        service=self.name,
                        success_count=self._success_count,
                    )
                    self._state = CircuitState.CLOSED
                    self._failure_count = 0
                    self._success_count = 0
                    if self._use_redis:
                        await self._reset_failures_distributed()
                    CIRCUIT_BREAKER_STATE.labels(service=self.name).set(0)
            elif self._state == CircuitState.CLOSED:
                # Reset failure count on success (local + distributed)
                if self._failure_count > 0 or self._use_redis:
                    logger.info(
                        "circuit_breaker_failure_count_reset",
                        service=self.name,
                        previous_failures=self._failure_count,
                    )
                self._failure_count = 0
                if self._use_redis:
                    await self._reset_failures_distributed()

    async def record_failure(self, error: Exception | None = None) -> None:
        """Record a failed call."""
        async with self._lock:
            if self._use_redis:
                # Use distributed counters so all workers see the failure
                d_count = await self._increment_failure_distributed()
                await self._set_last_failure_distributed()
                current_count = d_count
            else:
                self._failure_count += 1
                current_count = self._failure_count

            self._last_failure_time = time.monotonic()

            error_msg = str(error)[:100] if error else "unknown"
            logger.warning(
                "circuit_breaker_failure_recorded",
                service=self.name,
                failure_count=current_count,
                failure_threshold=self.failure_threshold,
                error=error_msg,
                current_state=self._state.value,
            )

            if self._state == CircuitState.HALF_OPEN:
                await self._release_half_open_slot()
                logger.warning(
                    "circuit_breaker_opening_from_half_open",
                    service=self.name,
                    failure_count=current_count,
                )
                self._state = CircuitState.OPEN
                self._success_count = 0
                CIRCUIT_BREAKER_STATE.labels(service=self.name).set(1)
            elif self._state == CircuitState.CLOSED:
                if current_count >= self.failure_threshold:
                    logger.warning(
                        "circuit_breaker_opening",
                        service=self.name,
                        failure_count=current_count,
                        failure_threshold=self.failure_threshold,
                    )
                    self._state = CircuitState.OPEN
                    CIRCUIT_BREAKER_STATE.labels(service=self.name).set(1)

    async def execute(self, func, *args, **kwargs) -> Any:
        """
        Execute a function with circuit breaker protection.

        Cancellation is treated as a slot release, NOT a failure — this
        prevents asyncio.CancelledError from falsely reopening the circuit
        when an outer timeout fires against a healthy downstream service.

        Args:
            func: Async function to execute
            *args: Positional arguments for func
            **kwargs: Keyword arguments for func

        Returns:
            Result from func if successful

        Raises:
            CircuitBreakerOpenError: If circuit is open
            Exception: Re-raises any exception from func
        """
        if not await self.can_execute():
            raise CircuitBreakerOpenError(self.name, self.reset_timeout)

        try:
            result = await func(*args, **kwargs)
            await self.record_success()
            return result

        except asyncio.CancelledError:
            # Release half-open slot without recording a failure — the
            # cancellation is from an outer timeout/shutdown, not from the
            # downstream service itself.
            if self._state == CircuitState.HALF_OPEN:
                async with self._lock:
                    self._half_open_calls = max(0, self._half_open_calls - 1)
            raise

        except Exception as e:
            await self.record_failure(e)
            raise

    def get_stats(self) -> dict[str, Any]:
        """Get circuit breaker statistics."""
        return {
            "name": self.name,
            "state": self.state.value,
            "failure_count": self._failure_count,
            "success_count": self._success_count,
            "failure_threshold": self.failure_threshold,
            "success_threshold": self.success_threshold,
            "reset_timeout": self.reset_timeout,
            "last_failure_time": self._last_failure_time,
        }


# Global circuit breaker for YouTube API
_youtube_circuit_breaker: CircuitBreaker | None = None


def get_youtube_circuit_breaker() -> CircuitBreaker:
    """Get or create the YouTube API circuit breaker."""
    global _youtube_circuit_breaker
    if _youtube_circuit_breaker is None:
        _youtube_circuit_breaker = CircuitBreaker(
            name="youtube_api",
            failure_threshold=int(os.environ.get("CIRCUIT_BREAKER_FAILURE_THRESHOLD", "5")),
            success_threshold=int(os.environ.get("CIRCUIT_BREAKER_SUCCESS_THRESHOLD", "3")),
            reset_timeout=float(os.environ.get("CIRCUIT_BREAKER_RESET_TIMEOUT", "30.0")),
            half_open_max_calls=int(os.environ.get("CIRCUIT_BREAKER_HALF_OPEN_MAX_CALLS", "3")),
            use_redis_distributed=os.environ.get("CIRCUIT_BREAKER_USE_REDIS", "").lower()
            in ("1", "true", "yes"),
        )
    return _youtube_circuit_breaker


async def extract_media_with_circuit_breaker(
    url: str,
    storage_path: str,
    progress_callback: Callable[[dict], Awaitable[None]] | None = None,
) -> tuple[str, str, str | None]:
    """
    Extract media URL with circuit breaker protection.

    Wraps extract_media_url with circuit breaker to prevent
    hammering YouTube during outages.

    Args:
        url: The video URL to extract.
        storage_path: Base path for storing downloaded files.
        progress_callback: Optional async callback for download progress updates.
                           The circuit breaker does not interpret progress data;
                           it is purely a pass-through to extract_media_url.

    Returns:
        tuple of (file_path, file_name, title).
    """
    cb = get_youtube_circuit_breaker()

    logger.debug(
        "circuit_breaker_executing_youtube_extraction",
        circuit_state=cb.state.value,
        url=url[:50],
    )

    result: tuple[str, str, str | None] = await cb.execute(
        lambda u, s: _extract_media_url_internal(u, s, progress_callback=progress_callback),
        url,
        storage_path,
    )
    return result


async def _extract_media_url_internal(
    url: str,
    storage_path: str,
    progress_callback: Callable[[dict], Awaitable[None]] | None = None,
) -> tuple[str, str, str | None]:
    """Internal extraction without circuit breaker (called by circuit breaker)."""
    from app.services.yt_dlp_service import extract_media_url

    return await extract_media_url(url, storage_path, progress_callback=progress_callback)


def get_circuit_breaker_stats() -> dict[str, Any]:
    """Get stats for the YouTube circuit breaker."""
    return get_youtube_circuit_breaker().get_stats()


def is_circuit_breaker_closed() -> bool:
    """Check if the YouTube circuit breaker is in CLOSED state."""
    cb = get_youtube_circuit_breaker()
    return cb.state == CircuitState.CLOSED


def is_circuit_breaker_open() -> bool:
    """Check if the YouTube circuit breaker is in OPEN state."""
    cb = get_youtube_circuit_breaker()
    return cb.state == CircuitState.OPEN


def get_circuit_state_value() -> int:
    """Get the current circuit breaker state as Prometheus gauge value."""
    cb = get_youtube_circuit_breaker()
    state_map = {CircuitState.CLOSED: 0, CircuitState.OPEN: 1, CircuitState.HALF_OPEN: 2}
    return state_map.get(cb.state, 0)

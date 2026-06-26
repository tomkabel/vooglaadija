"""Shared Redis client with connection pooling — single source of truth.

Provides a singleton Redis client instance used by both the API server
and the worker process. Eliminates the multiple-Redis-client anti-pattern
that previously existed in the codebase.

redis-py's from_url() manages an internal connection pool internally,
so connections are reused rather than created per call.
"""

from __future__ import annotations

from typing import cast

import redis.asyncio as aioredis

from core.logging_config import get_logger

logger = get_logger(__name__)

_redis_state: dict[str, object] = {"client": None}

# Chaos Engineering Redis key constants — single source of truth
CHAOS_CIRCUIT_BREAKER_KEY = "chaos:circuit_breaker_override"
CHAOS_ZOMBIE_JOB_KEY = "chaos:zombie_job_trigger"
CHAOS_DB_FAILOVER_KEY = "chaos:db_failover"
CHAOS_THROTTLE_SPIKE_KEY = "chaos:throttle_spike"
CHAOS_SLOW_PROCESSING_KEY = "chaos:slow_processing"

CHAOS_KEY_PREFIX = "chaos:"

SCENARIO_KEY_MAP: dict[str, str] = {
    "circuit_breaker_open": CHAOS_CIRCUIT_BREAKER_KEY,
    "worker_crash": CHAOS_ZOMBIE_JOB_KEY,
    "db_failover": CHAOS_DB_FAILOVER_KEY,
    "throttle_spike": CHAOS_THROTTLE_SPIKE_KEY,
    "slow_processing": CHAOS_SLOW_PROCESSING_KEY,
}

KEY_TO_SCENARIO_FIELD: dict[str, str] = {
    CHAOS_CIRCUIT_BREAKER_KEY: "circuit_breaker_open",
    CHAOS_ZOMBIE_JOB_KEY: "worker_crash",
    CHAOS_DB_FAILOVER_KEY: "db_failover",
    CHAOS_THROTTLE_SPIKE_KEY: "throttle_spike",
    CHAOS_SLOW_PROCESSING_KEY: "slow_processing",
}


def get_redis_client() -> aioredis.Redis:
    """Get or create the shared Redis client singleton.

    Returns the same client instance on every call. The client manages
    its own connection pool internally (TCP connections are reused).

    Used by both API server and worker process — each process gets its own
    singleton (one per OS process), which is the correct behavior since
    redis-py's connection pool is not fork/process-safe.
    """
    if _redis_state["client"] is not None:
        return cast("aioredis.Redis", _redis_state["client"])

    from core.config import settings

    _redis_state["client"] = aioredis.from_url(
        settings.redis_url,
        decode_responses=True,
        socket_connect_timeout=5,
        socket_timeout=5,
        retry_on_timeout=False,
    )
    return cast("aioredis.Redis", _redis_state["client"])


def reset_redis_client() -> None:
    """Reset the singleton (for testing only)."""
    _redis_state["client"] = None


async def close_redis_client() -> None:
    """Close the shared Redis client connection pool."""
    if _redis_state["client"] is not None:
        await cast("aioredis.Redis", _redis_state["client"]).close()
        _redis_state["client"] = None


async def check_worker_health() -> bool:
    """Check if any worker has a fresh heartbeat in Redis.

    Workers write ``worker:health:<worker_id>`` keys with a 30-second TTL
    (set via SETEX) every ~20 seconds in their main loop. This function
    scans for any matching key — if one exists with remaining TTL, at
    least one worker is alive.

    Returns:
        True if at least one worker health key exists (worker is alive).
        False if no health keys found or Redis is unreachable.

    """
    try:
        client = get_redis_client()
        async for key in client.scan_iter(match="worker:health:*", count=10):
            ttl = await client.ttl(key)
            if ttl is not None and ttl > 0:
                return True
        return False
    except Exception:
        logger.warning("worker_health_check_failed", exc_info=True)
        return False


async def check_chaos_key(key: str) -> bool:
    """Check if a chaos Redis key exists, with structured error handling.

    Returns False on any error (fail-closed to avoid cascading failures).
    """
    try:
        client = get_redis_client()
        exists = await client.exists(key)
        return bool(exists)
    except Exception:
        logger.warning("chaos_key_check_failed", key=key, exc_info=True)
        return False


async def get_all_chaos_status() -> dict[str, bool]:
    """Return status for all chaos scenarios by checking Redis keys.

    Returns a dict mapping scenario field names to boolean active states.
    """
    status: dict[str, bool] = dict.fromkeys(KEY_TO_SCENARIO_FIELD.values(), False)
    try:
        client = get_redis_client()
        for key, field in KEY_TO_SCENARIO_FIELD.items():
            exists = await client.exists(key)
            status[field] = bool(exists)
    except Exception:
        logger.warning("chaos_status_check_failed", exc_info=True)
    return status


async def delete_chaos_keys() -> int:
    """Delete all chaos keys using SCAN (non-blocking) instead of KEYS.

    Uses incremental SCAN with count=100 to avoid blocking the Redis event loop.
    Returns the number of deleted keys.
    """
    deleted = 0
    try:
        client = get_redis_client()
        cursor = 0
        while True:
            cursor, keys = await client.scan(cursor=cursor, match=f"{CHAOS_KEY_PREFIX}*", count=100)
            if keys:
                await client.delete(*keys)
                deleted += len(keys)
            if cursor == 0:
                break
    except Exception:
        logger.warning("chaos_cleanup_failed", exc_info=True)
    return deleted

"""Redis-based token blacklist for JWT revocation.

Supports two revocation strategies:
1. Direct jti blacklist: individual token revocation (on logout)
2. Token version: bulk revocation (on password change)

The jti blacklist entries have a TTL matching the token's remaining lifetime
so revoked tokens are automatically cleaned up.
"""

import time

from core.logging_config import get_logger

logger = get_logger(__name__)

_blacklist_prefix = "token:blacklist:"
_blacklist_ttl = 60 * 60 * 24 * 8  # 8 days — covers max refresh token lifetime + buffer

# In-memory cache for token blacklist checks.
# Maps jti -> (result: bool, expiry: monotonic time).
# Short TTL (2s) absorbs Redis latency spikes without meaningful staleness.
_jti_cache: dict[str, tuple[bool, float]] = {}
_JTI_CACHE_TTL = 2.0


def _clear_jti_cache() -> None:
    """Clear expired cache entries (called on every check)."""
    now = time.monotonic()
    stale = [k for k, v in _jti_cache.items() if v[1] <= now]
    for k in stale:
        del _jti_cache[k]


async def blacklist_token(token_jti: str, ttl_seconds: int = _blacklist_ttl) -> None:
    """Add a token jti to the blacklist with a TTL.

    The TTL should match the token's remaining lifetime so the blacklist
    entry is automatically cleaned up after the token would have expired.
    """
    try:
        from core.redis_client import get_redis_client

        r = get_redis_client()
        await r.setex(f"{_blacklist_prefix}{token_jti}", ttl_seconds, "1")
    except Exception:
        logger.warning("token_blacklist_write_failed", jti=token_jti, exc_info=True)


async def is_token_blacklisted(token_jti: str) -> bool:
    """Check if a token jti has been blacklisted.

    Uses a short-lived in-memory cache (2s) to absorb Redis latency spikes.
    Returns True if the token is blacklisted, False otherwise.
    Returns True on Redis failure (fail-closed) — during a Redis outage all
    tokens are conservatively treated as blacklisted. The API being degraded
    is preferable to silently accepting revoked tokens.
    """
    # Check in-memory cache first (avoids Redis round-trip in common case)
    cached = _jti_cache.get(token_jti)
    if cached is not None and time.monotonic() < cached[1]:
        return cached[0]
    _jti_cache.pop(token_jti, None)

    try:
        from core.redis_client import get_redis_client

        r = get_redis_client()
        exists = await r.exists(f"{_blacklist_prefix}{token_jti}")
        result = bool(exists)
        _jti_cache[token_jti] = (result, time.monotonic() + _JTI_CACHE_TTL)
        return result
    except Exception:
        logger.warning("token_blacklist_check_failed", exc_info=True)
        return True

"""Error classification engine for download job failures.

Provides per-category retry policies with decorrelated jitter,
enabling the worker to handle different error types differently
instead of the monolithic "retry 3 times then fail" approach.

Each error category has its own retry policy:
- RATE_LIMITED (429):    5 retries, 60s-20m, decorrelated jitter
- TRANSIENT (5xx/timeout): 3 retries, 10s-10m, decorrelated jitter
- BLOCKED (403/geo):     0 retries, fail fast
- NOT_FOUND (404/gone):  0 retries, fail fast
- FORMAT_UNAVAILABLE:    0 retries (handled by format fallback chain)
- TIMEOUT:               2 retries, 30s-10m, full jitter
- STORAGE (disk full):   1 retry, 5m fixed
- UNKNOWN:               2 retries, 30s-10m, full jitter
"""

import re
import secrets
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import Enum

from redis.asyncio import Redis

# Use a SystemRandom instance for uniform() while still exposing the name
# `random` so tests can patch `app.services.error_classifier.random.uniform`.
random = secrets.SystemRandom()


class ErrorCategory(Enum):
    RATE_LIMITED = "rate_limited"
    TRANSIENT = "transient"
    BLOCKED = "blocked"
    NOT_FOUND = "not_found"
    FORMAT_UNAVAILABLE = "format_unavailable"
    TIMEOUT = "timeout"
    STORAGE = "storage"
    UNKNOWN = "unknown"


class JitterType(Enum):
    DECORRELATED = "decorrelated"
    FULL = "full"
    NONE = "none"


@dataclass
class RetryPolicy:
    max_retries: int
    base_delay_seconds: float
    max_delay_seconds: float
    jitter: JitterType
    circuit_breaker_eligible: bool
    respects_retry_after: bool


CATEGORY_POLICIES: dict[ErrorCategory, RetryPolicy] = {
    ErrorCategory.RATE_LIMITED: RetryPolicy(
        max_retries=5,
        base_delay_seconds=60.0,
        max_delay_seconds=1200.0,
        jitter=JitterType.DECORRELATED,
        circuit_breaker_eligible=False,
        respects_retry_after=True,
    ),
    ErrorCategory.TRANSIENT: RetryPolicy(
        max_retries=3,
        base_delay_seconds=10.0,
        max_delay_seconds=600.0,
        jitter=JitterType.DECORRELATED,
        circuit_breaker_eligible=True,
        respects_retry_after=False,
    ),
    ErrorCategory.BLOCKED: RetryPolicy(
        max_retries=0,
        base_delay_seconds=0.0,
        max_delay_seconds=0.0,
        jitter=JitterType.NONE,
        circuit_breaker_eligible=False,
        respects_retry_after=False,
    ),
    ErrorCategory.NOT_FOUND: RetryPolicy(
        max_retries=0,
        base_delay_seconds=0.0,
        max_delay_seconds=0.0,
        jitter=JitterType.NONE,
        circuit_breaker_eligible=False,
        respects_retry_after=False,
    ),
    ErrorCategory.FORMAT_UNAVAILABLE: RetryPolicy(
        max_retries=0,
        base_delay_seconds=0.0,
        max_delay_seconds=0.0,
        jitter=JitterType.NONE,
        circuit_breaker_eligible=False,
        respects_retry_after=False,
    ),
    ErrorCategory.TIMEOUT: RetryPolicy(
        max_retries=2,
        base_delay_seconds=30.0,
        max_delay_seconds=600.0,
        jitter=JitterType.FULL,
        circuit_breaker_eligible=True,
        respects_retry_after=False,
    ),
    ErrorCategory.STORAGE: RetryPolicy(
        max_retries=1,
        base_delay_seconds=300.0,
        max_delay_seconds=300.0,
        jitter=JitterType.FULL,
        circuit_breaker_eligible=False,
        respects_retry_after=False,
    ),
    ErrorCategory.UNKNOWN: RetryPolicy(
        max_retries=2,
        base_delay_seconds=30.0,
        max_delay_seconds=600.0,
        jitter=JitterType.FULL,
        circuit_breaker_eligible=True,
        respects_retry_after=False,
    ),
}


@dataclass
class ClassificationResult:
    category: ErrorCategory
    signal: str


_RATE_LIMITED_PATTERNS = [
    re.compile(r"HTTP Error 429", re.IGNORECASE),
    re.compile(r"\b429\b"),
    re.compile(r"Too Many Requests", re.IGNORECASE),
    re.compile(r"rate.?limit", re.IGNORECASE),
    re.compile(r"Retry.?After", re.IGNORECASE),
]

_TRANSIENT_PATTERNS = [
    re.compile(r"HTTP Error 50[0-9]", re.IGNORECASE),
    re.compile(r"\b50[2-3]\b"),
    re.compile(r"connection.*(?:refused|reset|abort|timeout)", re.IGNORECASE),
    re.compile(r"temporary.*(?:failure|error|unavailable)", re.IGNORECASE),
    re.compile(r"try again later", re.IGNORECASE),
    re.compile(r"(?:DNS|name resolution).*error", re.IGNORECASE),
    re.compile(r"network.*(?:error|unreachable)", re.IGNORECASE),
    re.compile(r"Empty response", re.IGNORECASE),
    re.compile(r"Read timed out", re.IGNORECASE),
    re.compile(r"ConnectionError", re.IGNORECASE),
    re.compile(r"timeout.*occurred", re.IGNORECASE),
    re.compile(r"remote.*disconnected", re.IGNORECASE),
    re.compile(r"reset by peer", re.IGNORECASE),
    re.compile(r"broken pipe", re.IGNORECASE),
]

_BLOCKED_PATTERNS = [
    re.compile(r"HTTP Error 403", re.IGNORECASE),
    re.compile(r"\b403\b"),
    re.compile(r"blocked", re.IGNORECASE),
    re.compile(r"age.?restrict", re.IGNORECASE),
    re.compile(r"copyright", re.IGNORECASE),
    re.compile(r"terms of service", re.IGNORECASE),
    re.compile(r"sign in to confirm", re.IGNORECASE),
    re.compile(r"login required", re.IGNORECASE),
    re.compile(r"GEO", re.IGNORECASE),
    re.compile(r"unavailable in your", re.IGNORECASE),
    re.compile(r"removed by", re.IGNORECASE),
    re.compile(r"copyright claim", re.IGNORECASE),
    re.compile(r"DMCA", re.IGNORECASE),
    re.compile(r"restricted", re.IGNORECASE),
]

_NOT_FOUND_PATTERNS = [
    re.compile(r"HTTP Error 404", re.IGNORECASE),
    re.compile(r"\b404\b"),
    re.compile(r"(?:video|page|content).*not found", re.IGNORECASE),
    re.compile(r"(?:video|page).*unavailable", re.IGNORECASE),
    re.compile(r"private video", re.IGNORECASE),
    re.compile(r"deleted video", re.IGNORECASE),
    re.compile(r"no longer available", re.IGNORECASE),
    re.compile(r"removed video", re.IGNORECASE),
]

_FORMAT_PATTERNS = [
    re.compile(r"format is not available", re.IGNORECASE),
    re.compile(r"Requested format.*not available", re.IGNORECASE),
    re.compile(r"All formats failed", re.IGNORECASE),
]

_TIMEOUT_PATTERNS = [
    re.compile(r"(?:timed? ?out)", re.IGNORECASE),
    re.compile(r"Timeout", re.IGNORECASE),
    re.compile(r"timed out", re.IGNORECASE),
]

_STORAGE_PATTERNS = [
    re.compile(r"no space left", re.IGNORECASE),
    re.compile(r"disk full", re.IGNORECASE),
    re.compile(r"permission denied", re.IGNORECASE),
    re.compile(r"StorageError", re.IGNORECASE),
]

RETRY_AFTER_PATTERN = re.compile(r"Retry-After:\s*(\d+)", re.IGNORECASE)

_PATTERN_CATEGORIES: list[tuple[str, list[re.Pattern]]] = [
    ("format_unavailable", _FORMAT_PATTERNS),
    ("rate_limited", _RATE_LIMITED_PATTERNS),
    ("blocked", _BLOCKED_PATTERNS),
    ("not_found", _NOT_FOUND_PATTERNS),
    ("storage", _STORAGE_PATTERNS),
    ("timeout", _TIMEOUT_PATTERNS),
    ("transient", _TRANSIENT_PATTERNS),
]


def _get_matching_signal(error_str: str, patterns: list[re.Pattern]) -> str:
    for p in patterns:
        m = p.search(error_str)
        if m:
            return m.group(0)
    return "pattern_match"


def classify_error(error_str: str) -> ClassificationResult:
    if not error_str:
        return ClassificationResult(category=ErrorCategory.UNKNOWN, signal="empty_error")

    for cat_name, patterns in _PATTERN_CATEGORIES:
        for p in patterns:
            if p.search(error_str):
                category = ErrorCategory(cat_name)
                return ClassificationResult(
                    category=category,
                    signal=_get_matching_signal(error_str, patterns),
                )

    return ClassificationResult(category=ErrorCategory.UNKNOWN, signal="unrecognized_error")


def extract_retry_after(stderr_text: str) -> int | None:
    m = RETRY_AFTER_PATTERN.search(stderr_text)
    return int(m.group(1)) if m else None


def calculate_delay(
    category: ErrorCategory,
    attempt: int,
    prev_delay: float | None = None,
    retry_after: int | None = None,
) -> float:
    policy = CATEGORY_POLICIES[category]

    if retry_after and policy.respects_retry_after:
        base = max(policy.base_delay_seconds, float(retry_after))
    else:
        base = policy.base_delay_seconds

    cap = policy.max_delay_seconds

    if policy.jitter == JitterType.NONE:
        return base

    if policy.jitter == JitterType.DECORRELATED:
        if attempt == 0 or prev_delay is None:
            return random.uniform(0, base)
        return min(cap, random.uniform(base, prev_delay * 3))

    if policy.jitter == JitterType.FULL:
        c = min(cap, base * (2**attempt))
        return random.uniform(0, c)

    return base


def get_effective_max_retries(category: ErrorCategory, safety_cap: int = 10) -> int:
    policy_max = CATEGORY_POLICIES[category].max_retries
    return min(policy_max, safety_cap) if safety_cap else policy_max


def get_attempt_timeout(attempt: int) -> float:
    return min(600.0, 300.0 * (1 + attempt * 0.5))


def is_non_retryable(category: ErrorCategory) -> bool:
    return CATEGORY_POLICIES[category].max_retries == 0


# -- Retry Budget -----------------------------------------------------------

_RETRY_BUDGET_KEY = "retry_budget:retries"
_TOTAL_REQUEST_KEY = "retry_budget:total"
_BUDGET_WINDOW_SECONDS = 60
_MAX_RETRY_RATIO = 0.10


def _retry_budget_keys() -> tuple[str, str]:
    return _RETRY_BUDGET_KEY, _TOTAL_REQUEST_KEY


def _budget_window() -> int:
    return _BUDGET_WINDOW_SECONDS


def _max_ratio() -> float:
    """
    Provide the maximum allowed retry ratio.
    
    Returns:
    	float: The configured maximum retry ratio.
    """
    return _MAX_RETRY_RATIO


async def check_retry_budget(redis_client: Redis) -> bool:
    """
    Determine whether another retry fits within the configured retry budget.
    
    Returns:
    	bool: `True` if the retry budget permits another retry or the budget check fails; `False` if the retry ratio has reached its limit.
    """
    retry_key, total_key = _retry_budget_keys()
    window = _budget_window()
    max_ratio = _max_ratio()
    now = datetime.now(UTC).timestamp()
    cutoff = now - window

    try:
        total = await redis_client.zcount(total_key, cutoff, now)
        retries = await redis_client.zcount(retry_key, cutoff, now)
    except Exception:
        return True

    if total == 0:
        return True

    return bool((retries / total) < max_ratio)


async def record_retry_budget_request(redis_client: Redis, is_retry: bool = False) -> None:
    """
    Record a request in the retry budget tracking window.
    
    Parameters:
        is_retry (bool): Whether the request should also count as a retry.
    """
    retry_key, total_key = _retry_budget_keys()
    window = _budget_window()
    now = datetime.now(UTC).timestamp()

    try:
        await redis_client.zadd(total_key, {str(now): now})
        await redis_client.expire(total_key, window * 2)

        if is_retry:
            await redis_client.zadd(retry_key, {str(now): now})
            await redis_client.expire(retry_key, window * 2)
    except Exception:
        pass


def format_attempt_error(
    attempt: int,
    max_retries: int,
    error_str: str,
    category: ErrorCategory,
) -> str:
    prefix = f"Attempt {attempt}/{max_retries} [{category.value}]"
    return f"{prefix}: {error_str}"

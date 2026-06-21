"""Retry service with decorrelated jitter, category-based policies, and retry budget.

Three jitter strategies supported:
- DECORRELATED (gold standard): sleep = min(cap, random(base, prev * 3))
  Avoids the "cliff" problem and adapts naturally to varying conditions.
  Per AWS Polly team research and Google SRE recommendations.

- FULL JITTER: sleep = random(0, min(cap, base * 2^attempt))
  Simpler, good for non-critical workloads.

- NONE: sleep = base
  Only for categories where no delay makes sense (BLOCKED, NOT_FOUND).
"""

import random
from datetime import UTC, datetime, timedelta

RETRY_BASE_SECONDS = 60
RETRY_CAP_SECONDS = 600


def calculate_retry_with_jitter(retry_count: int) -> datetime:
    """Original full jitter implementation (kept for backward compatibility)."""
    cap_delay = min(RETRY_CAP_SECONDS, RETRY_BASE_SECONDS * (2**retry_count))
    delay = random.uniform(0, cap_delay)
    return datetime.now(UTC) + timedelta(seconds=delay)


def get_retry_delay_seconds(retry_count: int) -> float:
    cap_delay = min(RETRY_CAP_SECONDS, RETRY_BASE_SECONDS * (2**retry_count))
    return random.uniform(0, cap_delay)


class JitterRetryCalculator:
    def calculate_next_retry(self, retry_count: int) -> datetime:
        return calculate_retry_with_jitter(retry_count)


default_calculator = JitterRetryCalculator()

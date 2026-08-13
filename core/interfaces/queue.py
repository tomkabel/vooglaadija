"""Queue protocol and in-memory implementation (offline test seam).

The production queue (``core.queue``) is Redis-backed and module-level; this
protocol captures the operations producers (API) and consumers (worker) rely
on, and ``MemoryQueue`` mirrors Redis list semantics (LPUSH / RPOP) so the
payload contract can be exercised without a broker.
"""

from __future__ import annotations

from collections import deque
from typing import Protocol, runtime_checkable
from uuid import UUID


@runtime_checkable
class JobQueue(Protocol):
    """Async job-queue contract shared by API producers and worker consumers."""

    async def enqueue(self, job_id: UUID | str) -> None:
        """Push a job id to the download queue (LPUSH)."""

    async def pop_next(self) -> str | bytes | None:
        """Pop the next job id from the download queue (RPOP)."""

    async def push_to_retry(self, job_id: UUID, retry_timestamp: float) -> bool:
        """Push a job to the retry queue, deduplicating (ZADD semantics).

        Returns True if added, False if the job was already scheduled.
        """

    async def close(self) -> None:
        """Release any resources held by the queue."""


class MemoryQueue:
    """In-memory JobQueue mirroring Redis list semantics.

    Payloads are stored exactly as the production queue stores them
    (``str(job_id)``), so consumers must normalize through
    ``worker.job_claimer.normalize_job_id`` — the same contract as Redis.
    """

    def __init__(self) -> None:
        self._download: deque[str] = deque()
        self._retry: dict[str, float] = {}
        self.closed = False

    async def enqueue(self, job_id: UUID | str) -> None:
        self._download.appendleft(str(job_id))

    async def pop_next(self) -> str | None:
        if not self._download:
            return None
        return self._download.pop()

    async def push_to_retry(self, job_id: UUID, retry_timestamp: float) -> bool:
        key = str(job_id)
        if key in self._retry:
            return False
        self._retry[key] = retry_timestamp
        return True

    async def close(self) -> None:
        self.closed = True

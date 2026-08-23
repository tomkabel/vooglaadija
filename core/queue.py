"""Redis queue operations for the download worker.

Delegates to core.redis_client for the shared Redis singleton.
Provides convenience wrappers for queue operations, metrics, and
deduplication to prevent duplicate job entries.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any
from uuid import UUID

from core.logging_config import get_logger
from core.redis_client import get_redis_client

if TYPE_CHECKING:
    import redis.asyncio as aioredis

logger = get_logger(__name__)


class _LazyRedisClient:
    """Proxy that delegates all attribute access to the shared Redis client.

    Lazily resolves the client on first access so the singleton is created
    on demand rather than at import time.
    """

    def __init__(self) -> None:
        self._client: aioredis.Redis | None = None

    def _ensure(self) -> aioredis.Redis:
        """
        Ensure the Redis client is initialized and return it.

        Returns:
            aioredis.Redis: The shared Redis client.
        """
        if self._client is None:
            self._client = get_redis_client()
        assert self._client is not None
        return self._client

    async def close(self) -> None:
        """Close the cached Redis client and clear the local client reference."""
        if self._client is not None:
            client = self._client
            await client.close()
            # Clear the global reference only when it still points to the
            # instance this proxy captured — a newer client may have replaced
            # it, and closing that one would be wrong.
            from core.redis_client import _redis_state

            if _redis_state.get("client") is client:
                _redis_state["client"] = None
            self._client = None

    def __getattr__(self, name: str) -> Any:
        """Forward attribute access to the lazily initialized Redis client.

        Parameters:
            name (str): Name of the attribute to retrieve.

        Returns:
            Any: Value of the requested attribute on the Redis client.
        """
        return getattr(self._ensure(), name)


redis_client = _LazyRedisClient()


async def enqueue_job(job_id: UUID | str) -> None:
    """Enqueue a download job for processing via Celery.

    Dispatches a Celery task to the downloads queue. The Celery worker
    will pick up the task and process it with automatic retry support.
    """
    from worker.celery_tasks import process_download

    process_download.apply_async(
        args=[str(job_id)],
        queue="downloads",
    )

    try:
        from core.metrics import QUEUE_DEPTH

        QUEUE_DEPTH.inc()
    except Exception:
        pass


async def push_to_retry_queue(job_id: UUID, retry_timestamp: float) -> bool:
    """Push a job to the retry queue, deduplicating via zscore.

    Returns True if the job was added, False if it already existed.
    """
    key = "retry_queue"
    try:
        exists = await redis_client.zscore(key, str(job_id))
        if exists is not None:
            logger.debug("retry_duplicate_skipped", job_id=str(job_id), existing_score=exists)
            return False
        await redis_client.zadd(key, {str(job_id): retry_timestamp})
        return True
    except Exception as e:
        logger.error("retry_queue_push_failed", job_id=str(job_id), error=str(e))
        return False


async def push_to_download_queue(job_id: UUID) -> bool:
    """Push a job to the download queue, deduplicating via LREM + LPUSH.

    Removes any existing copies first, then pushes once.
    Returns True on success.
    """
    key = "download_queue"
    try:
        await redis_client.lrem(key, 0, str(job_id))
        await redis_client.lpush(key, str(job_id))
        return True
    except Exception as e:
        logger.error("download_queue_push_failed", job_id=str(job_id), error=str(e))
        return False

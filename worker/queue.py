"""Redis queue operations for the download worker.

Delegates to app.services.redis_client for the shared Redis singleton.
Provides convenience wrappers for queue operations, metrics, and
deduplication to prevent duplicate job entries.
"""

from uuid import UUID

from app.logging_config import get_logger
from app.services.redis_client import get_redis_client

logger = get_logger(__name__)


class _LazyRedisClient:
    """Proxy that delegates all attribute access to the shared Redis client.

    Lazily resolves the client on first access so the singleton is created
    on demand rather than at import time.
    """

    def __init__(self) -> None:
        self._client = None

    def _ensure(self):
        if self._client is None:
            self._client = get_redis_client()
        return self._client

    async def close(self):
        if self._client is not None:
            await self._client.close()
            self._client = None

    def __getattr__(self, name):
        return getattr(self._ensure(), name)


redis_client = _LazyRedisClient()


async def enqueue_job(job_id: UUID | str) -> None:
    """Enqueue a download job for processing.

    Uses the async Redis client to push job IDs to the download queue.
    """
    await redis_client.lpush("download_queue", str(job_id))

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

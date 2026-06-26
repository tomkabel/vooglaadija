"""Transactional outbox relay for Redis queue recovery."""

import json
from datetime import UTC, datetime, timedelta

from sqlalchemy import delete, select

from core.database import get_async_session_factory
from core.logging_config import get_logger
from core.models.outbox import Outbox
from core.queue import push_to_download_queue, push_to_retry_queue

logger = get_logger(__name__)


async def _retry_job_is_already_enqueued(job_id) -> bool:
    """Return whether a retry job already exists in Redis after a deduplicated push."""
    try:
        from core.queue import redis_client

        return await redis_client.zscore("retry_queue", str(job_id)) is not None
    except Exception as exc:
        logger.error("retry_queue_duplicate_check_failed", job_id=str(job_id), error=str(exc))
        return False


async def sync_outbox_to_queue(batch_size: int = 100) -> int:
    """Sync pending outbox entries to Redis queue."""
    session_factory = get_async_session_factory()
    synced = 0

    async with session_factory() as db:
        claim_result = await db.execute(
            select(Outbox)
            .where(Outbox.status == "pending")
            .order_by(Outbox.created_at)
            .limit(batch_size)
            .with_for_update(skip_locked=True),
        )
        entries = claim_result.scalars().all()

        if not entries:
            return 0

        processed_entry_ids = []
        for entry in entries:
            try:
                enqueued = False
                if entry.event_type == "retry_scheduled":
                    payload_data = json.loads(entry.payload) if entry.payload else {}
                    next_retry_at = payload_data.get("next_retry_at")
                    if next_retry_at:
                        retry_timestamp = datetime.fromisoformat(next_retry_at).timestamp()
                        enqueued = await push_to_retry_queue(entry.job_id, retry_timestamp)
                        if not enqueued and await _retry_job_is_already_enqueued(entry.job_id):
                            logger.info(
                                "retry_outbox_already_recovered",
                                job_id=str(entry.job_id),
                            )
                            enqueued = True
                    else:
                        logger.error("missing_next_retry_at_in_payload", job_id=str(entry.job_id))
                        continue
                else:
                    enqueued = await push_to_download_queue(entry.job_id)
                if enqueued:
                    processed_entry_ids.append(entry.id)
                    synced += 1
            except Exception as e:
                logger.error(
                    "failed_to_enqueue_job_from_outbox", job_id=str(entry.job_id), error=str(e),
                )

        if processed_entry_ids:
            try:
                await db.execute(delete(Outbox).where(Outbox.id.in_(processed_entry_ids)))
                await db.commit()
            except Exception:
                await db.rollback()

    if synced > 0:
        logger.info("synced_outbox_entries_to_queue", count=synced)

    return synced


async def cleanup_stale_outbox_entries(hours: int = 24) -> int:
    """Delete old terminal outbox entries while retaining pending crash-recovery rows."""
    session_factory = get_async_session_factory()
    cutoff = datetime.now(UTC) - timedelta(hours=hours)
    async with session_factory() as db:
        result = await db.execute(
            delete(Outbox).where(
                Outbox.created_at < cutoff,
                Outbox.status.in_(["completed", "failed"]),
            ),
        )
        await db.commit()
        count = int(result.rowcount or 0)
        if count > 0:
            logger.info("outbox_cleanup_completed", deleted=count, cutoff_age_hours=hours)
        return count

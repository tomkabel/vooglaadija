"""Transactional outbox relay for Redis queue recovery.

Lifecycle: outbox rows are written with status='pending' inside the same DB
transaction that mutates the domain entity (DownloadJob). The relay claims
pending rows, pushes the corresponding Redis message, and transitions the row
to status='processed' (with processed_at). Rows are retained for observability
and reaped by ``cleanup_stale_outbox_entries`` after the retention window.

This module is also the single source of truth for the
``OUTBOX_OLDEST_PENDING_SECONDS`` and ``OUTBOX_PENDING`` gauges so the
metrics reflect relay reality even if the relay is the only thing running.
"""

import json
from datetime import UTC, datetime, timedelta

from sqlalchemy import delete, func, select, update

from core.database import get_async_session_factory
from core.logging_config import get_logger
from core.metrics import OUTBOX_OLDEST_PENDING_SECONDS, OUTBOX_PENDING
from core.models.outbox import Outbox
from core.queue import push_to_download_queue, push_to_retry_queue

logger = get_logger(__name__)

_PROCESSED_STATUS = "processed"
_FAILED_STATUS = "failed"
_PENDING_STATUS = "pending"


async def _retry_job_is_already_enqueued(job_id) -> bool:
    """Return whether a retry job already exists in Redis after a deduplicated push."""
    try:
        from core.queue import redis_client

        return await redis_client.zscore("retry_queue", str(job_id)) is not None
    except Exception as exc:
        logger.error("retry_queue_duplicate_check_failed", job_id=str(job_id), error=str(exc))
        return False


async def _update_staleness_metrics(db) -> None:
    """Refresh OUTBOX_PENDING and OUTBOX_OLDEST_PENDING_SECONDS for observability.

    Reads are best-effort. A failure here must not abort the relay cycle.
    """
    try:
        pending_count = await db.scalar(
            select(func.count()).where(Outbox.status == _PENDING_STATUS)
        )
        OUTBOX_PENDING.set(float(pending_count or 0))

        oldest = await db.scalar(
            select(func.extract("epoch", func.now() - func.min(Outbox.created_at))).where(
                Outbox.status == _PENDING_STATUS,
            ),
        )
        OUTBOX_OLDEST_PENDING_SECONDS.set(float(oldest or 0))
    except Exception as exc:
        logger.warning("outbox_staleness_metric_update_failed", error=str(exc))


async def sync_outbox_to_queue(batch_size: int = 100) -> int:
    """Sync pending outbox entries to Redis queue and mark them processed.

    Returns the number of outbox entries successfully delivered to Redis.

    Crash-safety: the row's status transitions from 'pending' to 'processed'
    in a single SQL UPDATE that runs only after the Redis push has been
    confirmed by the queue helper (push_to_download_queue / push_to_retry_queue
    return True only after Redis acknowledged the write). If the process dies
    between the Redis push and the UPDATE commit, the next sync will re-deliver
    the message — duplicates are prevented at the queue side by LREM+LPUSH
    for download_queue and ZSCORE-based dedup for retry_queue.
    """
    session_factory = get_async_session_factory()
    synced = 0

    async with session_factory() as db:
        claim_result = await db.execute(
            select(Outbox)
            .where(Outbox.status == _PENDING_STATUS)
            .order_by(Outbox.created_at)
            .limit(batch_size)
            .with_for_update(skip_locked=True),
        )
        entries = claim_result.scalars().all()

        if not entries:
            await _update_staleness_metrics(db)
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
            now = datetime.now(UTC)
            await db.execute(
                update(Outbox)
                .where(Outbox.id.in_(processed_entry_ids))
                .values(status=_PROCESSED_STATUS, processed_at=now),
            )
            try:
                await db.commit()
            except Exception:
                await db.rollback()
                logger.error(
                    "outbox_processed_status_update_failed",
                    count=len(processed_entry_ids),
                )

        await _update_staleness_metrics(db)

    if synced > 0:
        logger.info("synced_outbox_entries_to_queue", count=synced)

    return synced


async def cleanup_stale_outbox_entries(hours: int = 24) -> int:
    """Delete terminal outbox rows older than ``hours`` (default 24).

    Terminal rows are those whose status is ``processed`` or ``failed`` (the
    latter is reserved for relay-level delivery failures). ``pending`` rows
    are never reaped here — they are the crash-recovery set and must survive
    until the relay delivers them.
    """
    session_factory = get_async_session_factory()
    cutoff = datetime.now(UTC) - timedelta(hours=hours)
    async with session_factory() as db:
        result = await db.execute(
            delete(Outbox).where(
                Outbox.processed_at.is_not(None),
                Outbox.processed_at < cutoff,
                Outbox.status.in_([_PROCESSED_STATUS, _FAILED_STATUS]),
            ),
        )
        await db.commit()
        count = int(result.rowcount or 0)
        if count > 0:
            logger.info("outbox_cleanup_completed", deleted=count, cutoff_age_hours=hours)
        return count
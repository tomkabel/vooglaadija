"""Job processing with error classification, per-category retry, and circuit-aware deferral.

Each error is classified into a category with its own retry policy:
  RATE_LIMITED    5 retries  60s base  decorrelated jitter
  TRANSIENT       3 retries  10s base  decorrelated jitter
  BLOCKED/NOT_FOUND  0 retries — permanent failure
  TIMEOUT         2 retries  30s base  full jitter
  STORAGE         1 retry    5m fixed
  UNKNOWN         2 retries  30s base  full jitter

Circuit breaker open → defer (not fail) → auto-retry when circuit closes.
"""

import asyncio
import time
from datetime import UTC, datetime
from typing import Any, cast
from uuid import UUID

from sqlalchemy import select, update
from sqlalchemy.engine import CursorResult
from sqlalchemy.ext.asyncio import AsyncSession

from app.services.circuit_breaker import (
    CircuitBreaker,
    CircuitBreakerOpenError,
    get_youtube_circuit_breaker,
)
from core.database import get_async_session_factory
from core.logging_config import get_logger
from core.metrics import (
    CIRCUIT_DEFERRED_DEPTH,
    JOB_DURATION_SECONDS,
    JOBS_COMPLETED,
)
from core.models.download_job import DownloadJob
from core.queue import push_to_download_queue, redis_client
from worker import dlq_manager, job_claimer, job_executor, retry_scheduler
from worker.browser_executor import get_browser_downloader_circuit_breaker
from worker.health import update_worker_state

logger = get_logger(__name__)

# -- Circuit deferred queue -------------------------------------------------

_CIRCUIT_DEFERRED_KEY = "circuit_deferred_queue"


def _get_breaker_for_service(service: str) -> CircuitBreaker:
    """Return the circuit breaker that governs a deferred service's recovery."""
    if service == "browser_downloader":
        return get_browser_downloader_circuit_breaker()
    return get_youtube_circuit_breaker()


async def _defer_job_to_circuit(job_id: UUID, service: str) -> None:
    """Record a deferred job in the Redis sorted set, keyed by its service.

    The member encodes ``"<service>:<job_id>"`` so the drain loop can check the
    correct breaker per entry (youtube vs browser_downloader) before re-enqueue.

    The Redis write is retried a few times: a Redis outage here would otherwise
    orphan a job whose DB status is already ``deferred``. ``_drain_circuit_deferred``
    also scans the DB for deferred jobs as a fallback so no entry is lost.
    """
    member = f"{service}:{job_id}"
    last_err: Exception | None = None
    for attempt in range(3):
        try:
            ts = time.time()
            await redis_client.zadd(_CIRCUIT_DEFERRED_KEY, {member: ts})
            try:
                depth = await redis_client.zcard(_CIRCUIT_DEFERRED_KEY)
                CIRCUIT_DEFERRED_DEPTH.set(depth)
            except Exception:
                pass
            return
        except Exception as e:
            last_err = e
            logger.warning(
                "defer_job_redis_write_retry",
                job_id=str(job_id),
                service=service,
                attempt=attempt,
                error=str(e),
            )
    logger.error(
        "failed_to_defer_job",
        job_id=str(job_id),
        service=service,
        error=str(last_err),
    )


async def _reconcile_deferred_from_db(max_batch: int) -> list[str]:
    """Discover DB ``deferred`` jobs missing from the Redis set.

    Fallback used when the sorted set is empty, so a job whose Redis write
    failed (or was lost) is still recovered by the drain loop. The originating
    service is recovered from the deferred job's error message.
    """
    try:
        session_factory = get_async_session_factory()
        async with session_factory() as db:
            result = await db.execute(
                select(DownloadJob.id, DownloadJob.error).where(DownloadJob.status == "deferred")
            )
            rows = result.all()
        members: list[str] = []
        for job_id, error in rows[:max_batch]:
            service = "youtube"
            marker = "Circuit breaker open ("
            if error and marker in error:
                start = error.index(marker) + len(marker)
                end = error.index(")", start)
                service = error[start:end]
            member = f"{service}:{job_id}"
            members.append(member)
            try:
                await redis_client.zadd(_CIRCUIT_DEFERRED_KEY, {member: time.time()})
            except Exception:
                pass
        return members
    except Exception as e:
        logger.warning("circuit_defer_reconcile_failed", error=str(e))
        return []


async def _drain_circuit_deferred(max_batch: int = 10) -> int:
    """
    Move deferred jobs back to the download queue after their circuit recovers.

    Service-aware: each deferred entry carries its originating service, and only
    the matching breaker is checked before re-enqueue. Entries whose breaker is
    still open are left in the set for a future drain. When the Redis set is
    empty, DB ``deferred`` rows are reconciled in as a fallback.

    Parameters:
        max_batch (int): Maximum number of deferred jobs to process.

    Returns:
        int: Number of jobs successfully returned to the download queue.
    """
    try:
        members = await redis_client.zrange(_CIRCUIT_DEFERRED_KEY, 0, max_batch - 1)
        if not members:
            members = await _reconcile_deferred_from_db(max_batch)
        if not members:
            return 0

        session_factory = get_async_session_factory()
        drained = 0
        async with session_factory() as db:
            for raw in members:
                member = raw.decode() if isinstance(raw, (bytes, bytearray)) else raw
                service, _, job_id_str = member.partition(":")
                breaker = _get_breaker_for_service(service or "youtube")
                if not await breaker.is_accepting():
                    # Breaker still open — leave the entry for a future drain.
                    continue
                try:
                    result = cast(
                        CursorResult[Any],
                        await db.execute(
                            update(DownloadJob)
                            .where(DownloadJob.id == job_id_str, DownloadJob.status == "deferred")
                            .values(status="pending", updated_at=datetime.now(UTC)),
                        ),
                    )
                    if result.rowcount != 1:
                        await db.rollback()
                        await redis_client.zrem(_CIRCUIT_DEFERRED_KEY, member)
                        logger.warning(
                            "circuit_drain_db_mismatch",
                            job_id=job_id_str,
                            reason="job not in deferred state or not found",
                        )
                        continue

                    enqueued = await push_to_download_queue(UUID(job_id_str))
                    if not enqueued:
                        await db.rollback()
                        logger.error("circuit_drain_enqueue_failed", job_id=job_id_str)
                        continue

                    await db.commit()
                    await redis_client.zrem(_CIRCUIT_DEFERRED_KEY, member)
                    drained += 1
                except Exception as job_error:
                    await db.rollback()
                    logger.error(
                        "circuit_drain_job_failed",
                        job_id=job_id_str,
                        error=str(job_error),
                    )

        try:
            remaining = await redis_client.zcard(_CIRCUIT_DEFERRED_KEY)
            CIRCUIT_DEFERRED_DEPTH.set(remaining)
        except Exception:
            pass
        return drained
    except Exception as e:
        logger.error("circuit_drain_failed", error=str(e))
        return 0


async def process_next_job(job_id: UUID | str | None = None) -> bool:
    """Claim and process one worker job through the extracted execution boundary."""
    resolved_job_id = await job_claimer.next_job_id(job_id)
    if resolved_job_id is None:
        return False

    session_factory = get_async_session_factory()
    start_time = time.time()

    async with session_factory() as db:
        job = await job_claimer.claim_next(db, resolved_job_id)
        if job is None:
            logger.info("job_not_claimed", job_id=str(resolved_job_id))
            return False

        active_job_id = job.id
        update_worker_state(status="running", current_job_started_at=datetime.now(UTC).isoformat())

        try:
            await job_executor.publish_job_status(job)
            execution_result = await job_executor.execute(db, job, start_time=start_time)
            return await _handle_execution_result(db, job, execution_result)

        except asyncio.CancelledError:
            logger.info("Job %s cancelled, requeueing...", active_job_id)
            await job_executor.requeue_job(active_job_id, db)
            update_worker_state(status="running", current_job_started_at=None)
            raise

        finally:
            JOB_DURATION_SECONDS.observe(time.time() - start_time)

    return False


async def _handle_execution_result(
    db: AsyncSession,
    job: DownloadJob,
    result: job_executor.ExecutionResult,
) -> bool:
    if result.status == job_executor.ExecutionStatus.COMPLETED:
        return True
    if result.status in {
        job_executor.ExecutionStatus.CONSUMED,
        job_executor.ExecutionStatus.REQUEUED,
    }:
        return result.completed
    if result.error is None:
        return False
    if isinstance(result.error, CircuitBreakerOpenError):
        return await _handle_circuit_open(db, job.id, result.error)
    return await _handle_execution_error(db, job, result.error)


async def _handle_circuit_open(
    db: AsyncSession, active_job_id: UUID, cb_error: CircuitBreakerOpenError
) -> bool:
    """
    Defer a processing job while its service circuit breaker is open.

    Parameters:
        active_job_id (UUID): Identifier of the job to defer.
        cb_error (CircuitBreakerOpenError): Circuit-breaker error containing the service and recovery details.

    Returns:
        bool: `False` because the job is deferred or is no longer processing.
    """
    logger.warning(
        "circuit_breaker_open_deferring",
        job_id=str(active_job_id),
        service=cb_error.service_name,
        reset_timeout=cb_error.reset_timeout,
    )
    result = cast(
        CursorResult[Any],
        await db.execute(
            update(DownloadJob)
            .where(
                DownloadJob.id == active_job_id,
                DownloadJob.status == "processing",
            )
            .values(
                status="deferred",
                error=f"Circuit breaker open ({cb_error.service_name}), "
                f"deferred until recovery (cooldown: {cb_error.reset_timeout}s)",
                last_error=f"Circuit breaker open ({cb_error.service_name}), "
                f"deferred until recovery (cooldown: {cb_error.reset_timeout}s)",
                error_category="transient",
                updated_at=datetime.now(UTC),
            ),
        ),
    )
    await db.commit()
    if result.rowcount == 0:
        logger.warning(
            "circuit_deferral_skipped_job_not_processing",
            job_id=str(active_job_id),
        )
        return False

    await _defer_job_to_circuit(active_job_id, cb_error.service_name)

    select_result = await db.execute(select(DownloadJob).where(DownloadJob.id == active_job_id))
    deferred_job = select_result.scalar_one_or_none()
    if deferred_job:
        await job_executor.publish_job_status(deferred_job)

    JOBS_COMPLETED.labels(status="deferred").inc()
    update_worker_state(status="running", current_job_started_at=None)
    return False


async def _handle_execution_error(
    db: AsyncSession, claimed_job: DownloadJob, error: BaseException
) -> bool:
    active_job_id = claimed_job.id
    update_worker_state(status="running", current_job_started_at=None)

    select_result = await db.execute(select(DownloadJob).where(DownloadJob.id == active_job_id))
    job = select_result.scalar_one_or_none()

    if not job:
        logger.error("job_not_found_during_error_handling", job_id=str(active_job_id))
        return False

    decision = retry_scheduler.evaluate(job, error)
    if decision.is_final:
        if decision.effective_max_retries == 0:
            logger.info(
                "job_failed_non_retryable",
                job_id=str(active_job_id),
                category=decision.category.value,
                signal=decision.signal,
            )
        else:
            logger.warning(
                "job_failed_permanently_max_retries",
                job_id=str(active_job_id),
                category=decision.category.value,
                retry_count=job.retry_count,
                effective_max=decision.effective_max_retries,
            )
        return await dlq_manager.mark_failed_and_move_to_dlq(db, job, decision)

    await retry_scheduler.schedule_retry(db, job, decision)
    return False

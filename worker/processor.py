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
from uuid import UUID

from sqlalchemy import select, update

from app.services.circuit_breaker import (
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
from worker.health import update_worker_state

logger = get_logger(__name__)

# -- Circuit deferred queue -------------------------------------------------

_CIRCUIT_DEFERRED_KEY = "circuit_deferred_queue"


async def _defer_job_to_circuit(job_id: UUID, service: str) -> None:
    try:
        ts = time.time()
        await redis_client.zadd(_CIRCUIT_DEFERRED_KEY, {str(job_id): ts})
        try:
            depth = await redis_client.zcard(_CIRCUIT_DEFERRED_KEY)
            CIRCUIT_DEFERRED_DEPTH.set(depth)
        except Exception:
            pass
    except Exception as e:
        logger.error("failed_to_defer_job", job_id=str(job_id), error=str(e))


async def _drain_circuit_deferred(max_batch: int = 10) -> int:
    """Move deferred jobs back to download queue when circuit has recovered.

    Updates DB status 'deferred' → 'pending' atomically so the worker's
    claim (WHERE status='pending') succeeds on re-pickup.
    Returns number of jobs drained.
    """
    if not await _circuit_is_accepting():
        return 0
    try:
        members = await redis_client.zrange(_CIRCUIT_DEFERRED_KEY, 0, max_batch - 1)
        if not members:
            return 0

        session_factory = get_async_session_factory()
        drained = 0
        async with session_factory() as db:
            for job_id_str in members:
                try:
                    result = await db.execute(
                        update(DownloadJob)
                        .where(DownloadJob.id == job_id_str, DownloadJob.status == "deferred")
                        .values(status="pending", updated_at=datetime.now(UTC)),
                    )
                    if result.rowcount != 1:
                        await db.rollback()
                        await redis_client.zrem(_CIRCUIT_DEFERRED_KEY, job_id_str)
                        logger.warning(
                            "circuit_drain_db_mismatch",
                            job_id=job_id_str,
                            reason="job not in deferred state or not found",
                        )
                        continue

                    enqueued = await push_to_download_queue(job_id_str)
                    if not enqueued:
                        await db.rollback()
                        logger.error("circuit_drain_enqueue_failed", job_id=job_id_str)
                        continue

                    await db.commit()
                    await redis_client.zrem(_CIRCUIT_DEFERRED_KEY, job_id_str)
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


async def _circuit_is_accepting() -> bool:
    cb = get_youtube_circuit_breaker()
    return await cb.is_accepting()


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
    db,
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


async def _handle_circuit_open(db, active_job_id: UUID, cb_error: CircuitBreakerOpenError) -> bool:
    logger.warning(
        "circuit_breaker_open_deferring",
        job_id=str(active_job_id),
        service=cb_error.service_name,
        reset_timeout=cb_error.reset_timeout,
    )
    result = await db.execute(
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


async def _handle_execution_error(db, claimed_job: DownloadJob, error: BaseException) -> bool:
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

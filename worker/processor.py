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
import contextlib
import json
import os
import random
import time
import uuid
from datetime import UTC, datetime, timedelta
from uuid import UUID

from sqlalchemy import delete, select, update
from sqlalchemy.exc import OperationalError

from app.config import settings
from app.database import get_async_session_factory
from app.logging_config import get_logger
from app.metrics import (
    CIRCUIT_DEFERRED_DEPTH,
    DLQ_DEPTH,
    ERROR_CLASSIFICATION,
    JOB_DURATION_SECONDS,
    JOBS_COMPLETED,
    RECOVERIES,
    RETRIES_TOTAL,
)
from core.models.download_job import DownloadJob
from core.models.failed_job import FailedJob
from core.models.outbox import Outbox
from app.services.circuit_breaker import (
    CircuitBreakerOpenError,
    extract_media_with_circuit_breaker,
    get_youtube_circuit_breaker,
)
from app.services.error_classifier import (
    CATEGORY_POLICIES,
    ErrorCategory,
    calculate_delay,
    classify_error,
    format_attempt_error,
    get_attempt_timeout,
    is_non_retryable,
)
from app.services.pubsub_service import get_pubsub_service
from app.services.throttle_predictor import get_risk_score, risk_check_and_warn
from worker.health import update_worker_state
from worker.queue import push_to_download_queue, push_to_retry_queue, redis_client

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
    if not _circuit_is_accepting():
        return 0
    try:
        members = await redis_client.zrange(_CIRCUIT_DEFERRED_KEY, 0, max_batch - 1)
        if not members:
            return 0
        await redis_client.zrem(_CIRCUIT_DEFERRED_KEY, *members)

        session_factory = get_async_session_factory()
        drained = 0
        async with session_factory() as db:
            for job_id_str in members:
                result = await db.execute(
                    update(DownloadJob)
                    .where(DownloadJob.id == job_id_str, DownloadJob.status == "deferred")
                    .values(status="pending", updated_at=datetime.now(UTC))
                )
                if result.rowcount == 1:
                    await push_to_download_queue(job_id_str)
                    drained += 1
                else:
                    logger.warning(
                        "circuit_drain_db_mismatch",
                        job_id=job_id_str,
                        reason="job not in deferred state or not found",
                    )
            await db.commit()

        try:
            remaining = await redis_client.zcard(_CIRCUIT_DEFERRED_KEY)
            CIRCUIT_DEFERRED_DEPTH.set(remaining)
        except Exception:
            pass
        return drained
    except Exception as e:
        logger.error("circuit_drain_failed", error=str(e))
        return 0


def _circuit_is_accepting() -> bool:
    cb = get_youtube_circuit_breaker()
    return not cb.is_open


# -- Dead letter queue ------------------------------------------------------


async def _move_to_dlq(
    db,
    job: DownloadJob,
    category: ErrorCategory,
    final_error: str,
    retry_count: int,
    retry_history: str | None = None,
) -> None:
    failed = FailedJob(
        id=uuid.uuid4(),
        original_job_id=job.id,
        user_id=job.user_id,
        url=job.url,
        error_category=category.value,
        retry_history=retry_history if retry_history is not None else job.error,
        final_error=final_error,
        final_error_category=category.value,
        retry_count=retry_count,
        max_retries_at_failure=job.max_retries,
        title=job.title,
        failed_at=datetime.now(UTC),
        expires_at=datetime.now(UTC) + timedelta(days=7),
    )
    db.add(failed)
    try:
        from sqlalchemy import func as sa_func

        result = await db.execute(sa_func.count(FailedJob.id))
        DLQ_DEPTH.set(result.scalar())
    except Exception:
        pass


# -- Pub/sub ----------------------------------------------------------------


async def _publish_job_status(job) -> None:
    try:
        pubsub = get_pubsub_service()
        await pubsub.publish_job_status(
            job.user_id,
            {
                "id": str(job.id),
                "status": job.status,
                "url": job.url,
                "title": job.title,
                "file_name": job.file_name,
                "error": job.error,
                "error_category": job.error_category,
                "created_at": job.created_at.isoformat() if job.created_at else None,
                "updated_at": job.updated_at.isoformat() if job.updated_at else None,
            },
        )
    except Exception as e:
        logger.warning("pubsub_publish_failed", job_id=str(job.id), error=str(e))


async def _heartbeat(db, job_id: UUID) -> None:
    await db.execute(
        update(DownloadJob).where(DownloadJob.id == job_id).values(updated_at=datetime.now(UTC))
    )
    await db.commit()


async def _requeue_job(job_id: UUID, db) -> bool:
    outbox_entry = Outbox(
        id=uuid.uuid4(),
        job_id=job_id,
        event_type="retry_scheduled",
        payload=json.dumps(
            {
                "retry_count": 0,
                "next_retry_at": datetime.now(UTC).isoformat(),
            }
        ),
        status="pending",
    )
    db.add(outbox_entry)

    result = await db.execute(
        update(DownloadJob)
        .where(
            DownloadJob.id == job_id,
            DownloadJob.status == "processing",
        )
        .values(
            status="pending",
            updated_at=datetime.now(UTC),
        )
    )
    await db.commit()
    if result.rowcount == 0:
        logger.warning("requeue_skipped_job_not_processing", job_id=str(job_id))
        return False
    return True


async def _periodic_heartbeat(
    db_factory,
    job_id: UUID,
    stop_event: asyncio.Event,
) -> None:
    """Send heartbeats every 30s until the stop event is set.

    Uses its own session to avoid racing with the extraction's
    long-lived session (which may be mid-transaction).
    The zombie sweeper threshold is 15 minutes, so 30s is
    more than sufficient to prevent false zombie detection.
    """
    try:
        async with db_factory() as hb_db:
            while not stop_event.is_set():
                try:
                    await asyncio.wait_for(stop_event.wait(), timeout=30.0)
                    break
                except TimeoutError:
                    pass
                if stop_event.is_set():
                    break
                with contextlib.suppress(Exception):
                    await hb_db.execute(
                        update(DownloadJob)
                        .where(DownloadJob.id == job_id)
                        .values(updated_at=datetime.now(UTC))
                    )
                    await hb_db.commit()
    except asyncio.CancelledError:
        pass


def _cleanup_downloaded_file(file_path: str | None) -> None:
    if file_path:
        try:
            if os.path.exists(file_path):
                os.remove(file_path)
                logger.info("Cleaned up partial download: %s", file_path)
        except OSError as e:
            logger.warning("Failed to clean up partial download %s: %s", file_path, e)


async def _check_chaos_injection(db, job_id: UUID, start_time: float) -> bool:
    """Check and execute chaos injection scenarios.

    Returns True if the job was consumed by a chaos scenario (caller should
    return immediately), or raises OperationalError for DB failover scenarios.
    """
    from worker.state import shutdown_event

    # Chaos: zombie job trigger
    try:
        if await redis_client.exists("chaos:zombie_job_trigger"):
            logger.warning("chaos_zombie_job_triggered", job_id=str(job_id))
            outbox_entry = Outbox(
                id=uuid.uuid4(),
                job_id=job_id,
                event_type="zombie_recovery",
                payload=json.dumps({"recovered_at": datetime.now(UTC).isoformat(), "chaos": True}),
                status="pending",
            )
            db.add(outbox_entry)
            await db.execute(
                update(DownloadJob)
                .where(DownloadJob.id == job_id)
                .values(status="pending", updated_at=datetime.now(UTC))
            )
            await db.commit()
            RECOVERIES.labels(reason="zombie_sweep_recovery").inc()
            update_worker_state(status="running", current_job_started_at=None)
            JOB_DURATION_SECONDS.observe(time.time() - start_time)
            return True
    except Exception:
        pass

    # Chaos: DB failover
    try:
        if await redis_client.exists("chaos:db_failover"):
            logger.warning("chaos_db_failover_triggered", job_id=str(job_id))
            raise OperationalError(
                "could not connect to server",
                None,
                RuntimeError("chaos: simulated DB failover"),
            )
    except OperationalError:
        raise
    except Exception:
        pass

    # Chaos: slow processing
    try:
        if await redis_client.exists("chaos:slow_processing"):
            delay = random.uniform(5.0, 20.0)
            logger.info("chaos_slow_processing", job_id=str(job_id), delay_seconds=round(delay, 1))
            await asyncio.sleep(delay)
    except Exception:
        pass

    # Shutdown check (between chaos checks and extraction)
    if shutdown_event.is_set():
        logger.info("Shutdown requested, requeueing job %s", job_id)
        await _requeue_job(job_id, db)
        update_worker_state(status="running", current_job_started_at=None)
        return True

    return False


async def process_next_job(job_id: UUID | str | None = None) -> bool:
    from worker.state import shutdown_event

    # Import worker.main at call time (not via from ... import) so attribute
    # accesses always read the live module-level values, not import-time copies.
    # Both shutdown_requested_at and GRACE_PERIOD_SECONDS are reassigned
    # by _signal_handler after the function starts running.
    _worker_main = __import__("worker.main", fromlist=[""])

    if job_id is None:
        try:
            job_id_str = await redis_client.rpop("download_queue")
        except Exception as e:
            logger.warning("redis_rpop_failed", error=str(e))
            return False
        if not job_id_str:
            return False
        job_id = UUID(job_id_str)
    elif isinstance(job_id, str):
        job_id = UUID(job_id)

    session_factory = get_async_session_factory()
    start_time = time.time()

    async with session_factory() as db:
        result = await db.execute(
            update(DownloadJob)
            .where(DownloadJob.id == job_id, DownloadJob.status == "pending")
            .values(
                status="processing",
                updated_at=datetime.now(UTC),
            )
        )
        await db.commit()

        claimed = result.rowcount == 1

        if not claimed:
            logger.info("job_not_claimed", job_id=str(job_id))
            return False

        update_worker_state(status="running", current_job_started_at=datetime.now(UTC).isoformat())

        stop_hb: asyncio.Event | None = None
        hb_task: asyncio.Task[None] | None = None

        try:
            result = await db.execute(select(DownloadJob).where(DownloadJob.id == job_id))
            job = result.scalar_one_or_none()

            if not job:
                logger.warning("job_not_found_after_claim", job_id=str(job_id))
                update_worker_state(status="running", current_job_started_at=None)
                return False

            await _heartbeat(db, job_id)

            result = await db.execute(select(DownloadJob).where(DownloadJob.id == job_id))
            job = result.scalar_one_or_none()
            if job:
                await _publish_job_status(job)

            if await _check_chaos_injection(db, job_id, start_time):
                return False

            # Pre-emptive throttle check
            if settings.feature_throttle_preemptive_enabled:
                throttle_risk = await get_risk_score("youtube")
                if throttle_risk >= 1.0:
                    logger.warning(
                        "preemptive_throttle_block", job_id=str(job_id), risk_score=throttle_risk
                    )
                    await _requeue_job(job_id, db)
                    JOBS_COMPLETED.labels(status="deferred").inc()
                    update_worker_state(status="running", current_job_started_at=None)
                    return True
                if throttle_risk >= settings.throttle_risk_threshold:
                    await risk_check_and_warn("youtube", throttle_risk)
                    delay = 0.5 + throttle_risk * 2.0
                    logger.info(
                        "preemptive_throttle_delay",
                        job_id=str(job_id),
                        risk_score=throttle_risk,
                        delay_seconds=round(delay, 1),
                    )
                    await asyncio.sleep(delay)

            async def _progress_callback(progress_data: dict) -> None:
                try:
                    pubsub = get_pubsub_service()
                    await pubsub.publish_job_progress(
                        job.user_id,
                        {
                            "id": str(job.id),
                            "progress": {
                                "percent": progress_data.get("percent"),
                                "speed": progress_data.get("speed"),
                                "eta": progress_data.get("eta"),
                                "downloaded_bytes": progress_data.get("downloaded_bytes"),
                                "total_bytes": progress_data.get("total_bytes"),
                            },
                        },
                    )
                except Exception:
                    logger.warning("progress_publish_failed", job_id=str(job.id), exc_info=True)

            # Per-attempt timeout escalation — later retries get more time
            attempt_timeout = get_attempt_timeout(job.retry_count)

            # Shutdown-aware timeout shortening: if the worker is shutting down,
            # shrink the extraction timeout to leave margin for _requeue_job()
            # to complete before the orchestrator sends SIGKILL.
            #
            # Uses worker.main module attribute directly (not from ... import)
            # so that the signal handler's reassignment of the module-level
            # shutdown_requested_at is visible at read time, not just import time.
            # See: docs/analysis/architecture-vulnerability-analysis.md
            shutdown_ts = getattr(_worker_main, "shutdown_requested_at", None)
            if shutdown_ts is not None:
                elapsed = time.monotonic() - shutdown_ts
                grace_period = getattr(_worker_main, "GRACE_PERIOD_SECONDS", 25)
                remaining = grace_period - elapsed
                if remaining > 2.0:
                    # Leave 2s margin for _requeue_job to complete
                    attempt_timeout = min(attempt_timeout, remaining - 2.0)
                    attempt_timeout = max(attempt_timeout, 1.0)
                    logger.info(
                        "shutdown_shortening_extraction_timeout",
                        job_id=str(job_id),
                        remaining_grace=round(remaining, 1),
                        extraction_timeout=round(attempt_timeout, 1),
                    )
                elif remaining <= 2.0:
                    # Not enough time for extraction — requeue immediately
                    logger.warning(
                        "shutdown_grace_too_short_requeueing",
                        job_id=str(job_id),
                        remaining_grace=round(remaining, 1),
                    )
                    await _requeue_job(job_id, db)
                    update_worker_state(status="running", current_job_started_at=None)
                    return False

            # Spawn a background heartbeat task that updates updated_at every
            # 30s during extraction, preventing the zombie sweeper (15min
            # threshold) from racing with long-running extractions.
            stop_hb = asyncio.Event()
            hb_task = asyncio.create_task(
                _periodic_heartbeat(get_async_session_factory(), job_id, stop_hb)
            )

            loop = asyncio.get_running_loop()
            extract_task = loop.create_task(
                extract_media_with_circuit_breaker(
                    job.url,
                    settings.storage_path,
                    progress_callback=_progress_callback,
                )
            )

            try:
                file_path, file_name, title = await asyncio.wait_for(
                    extract_task, timeout=attempt_timeout
                )
            except TimeoutError:
                extract_task.cancel()
                try:
                    await extract_task
                except (asyncio.CancelledError, Exception):
                    pass

                # After a timed-out extraction during shutdown, requeue immediately
                if getattr(_worker_main, "shutdown_requested_at", None) is not None:
                    await _requeue_job(job_id, db)
                    _cleanup_downloaded_file(None)
                    update_worker_state(status="running", current_job_started_at=None)
                    JOBS_COMPLETED.labels(status="deferred").inc()
                    logger.info("shutdown_requeued_timed_out_job", job_id=str(job_id))
                    return False

                raise TimeoutError(
                    f"Extraction timed out after {attempt_timeout}s (attempt {job.retry_count + 1})"
                ) from None

            if shutdown_event.is_set():
                logger.info("Shutdown requested after download, requeueing job %s", job_id)
                await _requeue_job(job_id, db)
                _cleanup_downloaded_file(file_path)
                update_worker_state(status="running", current_job_started_at=None)
                return False

            await _heartbeat(db, job_id)

            result = await db.execute(
                update(DownloadJob)
                .where(
                    DownloadJob.id == job_id,
                    DownloadJob.status == "processing",
                )
                .values(
                    status="completed",
                    file_path=file_path,
                    file_name=file_name,
                    title=title,
                    completed_at=datetime.now(UTC),
                    expires_at=datetime.now(UTC) + timedelta(hours=settings.file_expire_hours),
                )
            )
            await db.commit()
            if result.rowcount == 0:
                # Job was re-claimed by zombie sweeper — our work is orphaned
                _cleanup_downloaded_file(file_path)
                logger.warning("job_already_requeued_by_zombie_sweeper", job_id=str(job_id))
                update_worker_state(status="running", current_job_started_at=None)
                return False
            await db.commit()

            result = await db.execute(select(DownloadJob).where(DownloadJob.id == job_id))
            job = result.scalar_one_or_none()
            if job:
                await _publish_job_status(job)

            update_worker_state(status="running", current_job_started_at=None)
            JOBS_COMPLETED.labels(status="success").inc()
            logger.info("job_completed_successfully", job_id=str(job_id))

            stop_hb.set()
            return True

        except asyncio.CancelledError:
            logger.info("Job %s cancelled, requeueing...", job_id)
            await _requeue_job(job_id, db)
            update_worker_state(status="running", current_job_started_at=None)
            raise

        except CircuitBreakerOpenError as cb_error:
            logger.warning(
                "circuit_breaker_open_deferring",
                job_id=str(job_id),
                service=cb_error.service_name,
                reset_timeout=cb_error.reset_timeout,
            )
            # Defer, don't fail — circuit will recover
            result = await db.execute(
                update(DownloadJob)
                .where(
                    DownloadJob.id == job_id,
                    DownloadJob.status == "processing",
                )
                .values(
                    status="deferred",
                    error=f"Circuit breaker open ({cb_error.service_name}), "
                    f"deferred until recovery (cooldown: {cb_error.reset_timeout}s)",
                    error_category="transient",
                    updated_at=datetime.now(UTC),
                )
            )
            await db.commit()
            if result.rowcount == 0:
                logger.warning(
                    "circuit_deferral_skipped_job_not_processing",
                    job_id=str(job_id),
                )
                return False

            await _defer_job_to_circuit(job_id, cb_error.service_name)

            result = await db.execute(select(DownloadJob).where(DownloadJob.id == job_id))
            job = result.scalar_one_or_none()
            if job:
                await _publish_job_status(job)

            JOBS_COMPLETED.labels(status="deferred").inc()
            update_worker_state(status="running", current_job_started_at=None)
            return False

        except Exception as e:
            error_str = str(e)
            classification = classify_error(error_str)
            category = classification.category
            job_max_retries = job.max_retries if job.max_retries else 3

            # Get category-specific retry limit, bounded by the job's max_retries
            cat_policy = CATEGORY_POLICIES[category]
            effective_max = min(cat_policy.max_retries, job_max_retries)

            # Record classification metric
            ERROR_CLASSIFICATION.labels(category=category.value).inc()

            update_worker_state(status="running", current_job_started_at=None)

            # Re-fetch job for current retry_count
            result = await db.execute(select(DownloadJob).where(DownloadJob.id == job_id))
            job = result.scalar_one_or_none()

            if not job:
                logger.error("job_not_found_during_error_handling", job_id=str(job_id))
                return False

            # Check if this error is non-retryable for its category
            if is_non_retryable(category) or job.retry_count >= effective_max:
                if is_non_retryable(category):
                    logger.info(
                        "job_failed_non_retryable",
                        job_id=str(job_id),
                        category=category.value,
                        signal=classification.signal,
                    )
                    final_error = f"Non-retryable error ({category.value}): {error_str}"
                else:
                    logger.warning(
                        "job_failed_permanently_max_retries",
                        job_id=str(job_id),
                        category=category.value,
                        retry_count=job.retry_count,
                        effective_max=effective_max,
                    )
                    final_error = (
                        f"Max retries ({effective_max}) exceeded for "
                        f"'{category.value}' category: {error_str}"
                    )

                # Format the accumulated error with all previous attempts
                # The error field already has the history, append the final one
                previous_errors = job.error or ""
                if previous_errors:
                    accumulated = f"{previous_errors} → {final_error}"
                else:
                    accumulated = final_error

                failed_result = await db.execute(
                    update(DownloadJob)
                    .where(
                        DownloadJob.id == job_id,
                        DownloadJob.status == "processing",
                    )
                    .values(
                        status="failed",
                        error=accumulated,
                        error_category=category.value,
                        completed_at=datetime.now(UTC),
                    )
                )
                if failed_result.rowcount == 0:
                    logger.warning(
                        "job_failed_update_skipped_not_processing",
                        job_id=str(job_id),
                    )
                    JOBS_COMPLETED.labels(status="failed").inc()
                    await db.commit()
                    return False

                JOBS_COMPLETED.labels(status="failed").inc()
                await db.commit()

                # Move to DLQ for forensic retention
                await _move_to_dlq(
                    db,
                    job,
                    category,
                    final_error,
                    job.retry_count,
                    retry_history=accumulated,
                )
                await db.commit()

                result = await db.execute(select(DownloadJob).where(DownloadJob.id == job_id))
                job = result.scalar_one_or_none()
                if job:
                    await _publish_job_status(job)

                logger.info("job_moved_to_dlq", job_id=str(job_id), category=category.value)

            else:
                # Retry with category-specific delay
                prev_delay = None
                if job.next_retry_at:
                    prev_delay = (job.next_retry_at - datetime.now(UTC)).total_seconds()
                    if prev_delay < 0:
                        prev_delay = None

                delay_seconds = calculate_delay(
                    category=category,
                    attempt=job.retry_count,
                    prev_delay=prev_delay,
                )
                next_retry = datetime.now(UTC) + timedelta(seconds=delay_seconds)

                formatted_error = format_attempt_error(
                    attempt=job.retry_count + 1,
                    max_retries=effective_max,
                    error_str=error_str,
                    category=category,
                )

                previous = job.error or ""
                if previous and job.retry_count > 0:
                    accumulated_error = f"{previous} → {formatted_error}"
                else:
                    accumulated_error = formatted_error

                # Transactional outbox for crash-safe retry
                outbox_entry = Outbox(
                    id=uuid.uuid4(),
                    job_id=job_id,
                    event_type="retry_scheduled",
                    payload=json.dumps(
                        {
                            "retry_count": job.retry_count + 1,
                            "category": category.value,
                            "next_retry_at": next_retry.isoformat(),
                        }
                    ),
                    status="pending",
                )
                db.add(outbox_entry)

                retry_result = await db.execute(
                    update(DownloadJob)
                    .where(
                        DownloadJob.id == job_id,
                        DownloadJob.status == "processing",
                    )
                    .values(
                        status="pending",
                        retry_count=job.retry_count + 1,
                        next_retry_at=next_retry,
                        error=accumulated_error,
                        error_category=category.value,
                        updated_at=datetime.now(UTC),
                    )
                )
                await db.commit()
                if retry_result.rowcount == 0:
                    logger.warning(
                        "job_retry_update_skipped_not_processing",
                        job_id=str(job_id),
                    )
                    return False

                try:
                    retry_ts = next_retry.timestamp()
                    await push_to_retry_queue(job_id, retry_ts)
                    await db.execute(delete(Outbox).where(Outbox.id == outbox_entry.id))
                    await db.commit()

                    RETRIES_TOTAL.labels(category=category.value).inc()

                    logger.info(
                        "job_scheduled_for_retry",
                        job_id=str(job_id),
                        category=category.value,
                        retry_count=job.retry_count + 1,
                        effective_max=effective_max,
                        next_retry_at=next_retry.isoformat(),
                        delay_seconds=round(delay_seconds, 1),
                    )

                    result = await db.execute(select(DownloadJob).where(DownloadJob.id == job_id))
                    job = result.scalar_one_or_none()
                    if job:
                        await _publish_job_status(job)

                except Exception as enqueue_error:
                    logger.error(
                        "job_failed_to_enqueue_for_retry",
                        job_id=str(job_id),
                        error=str(enqueue_error),
                    )

        finally:
            # Stop the periodic heartbeat if it was started
            try:
                if stop_hb is not None:
                    stop_hb.set()
                if hb_task is not None:
                    with contextlib.suppress(asyncio.CancelledError):
                        await hb_task
            except Exception:
                logger.warning("heartbeat_task_cleanup_failed", job_id=str(job_id), exc_info=True)
            JOB_DURATION_SECONDS.observe(time.time() - start_time)

    return False


async def reset_stuck_jobs(timeout_minutes: int = 10) -> int:
    """Reset jobs stuck in 'processing' status to 'failed'.

    Separate from the zombie sweeper (which requeues as pending).
    This is the hard timeout that marks jobs as failed.
    """
    # Note: this function is called from worker.main's periodic cleanup, not
    # from the zombie sweeper. The zombie sweeper (requeue_stuck_jobs) resets
    # to 'pending'; this function marks them as permanently 'failed'.
    cutoff = datetime.now(UTC) - timedelta(minutes=timeout_minutes)
    session_factory = get_async_session_factory()

    async with session_factory() as db:
        # Single UPDATE with RETURNING — avoids N+1 SELECT-per-job pattern
        # that was previously present via select() + per-job db.refresh().
        result = await db.execute(
            update(DownloadJob)
            .where(
                DownloadJob.status == "processing",
                DownloadJob.updated_at < cutoff,
            )
            .values(
                status="failed",
                error="Job timed out",
                error_category="timeout",
                completed_at=datetime.now(UTC),
                updated_at=datetime.now(UTC),
            )
            .returning(DownloadJob.id, DownloadJob.user_id)
            .execution_options(synchronize_session=False)
        )
        affected = result.fetchall()
        if not affected:
            return 0

        await db.commit()

        # Batch-publish status updates without per-job SELECT queries
        pubsub = get_pubsub_service()
        for job_id, user_id in affected:
            try:
                now_iso = datetime.now(UTC).isoformat()
                await pubsub.publish_job_status(
                    user_id,
                    {
                        "id": str(job_id),
                        "status": "failed",
                        "error": "Job timed out",
                        "error_category": "timeout",
                        "updated_at": now_iso,
                    },
                )
            except Exception as e:
                logger.warning(
                    "status_publish_failed",
                    job_id=str(job_id),
                    error=str(e),
                )

        count = len(affected)
        if count > 0:
            RECOVERIES.labels(reason="zombie_sweep_recovery").inc(amount=count)
            logger.warning("reset_stuck_jobs", count=count, timeout_minutes=timeout_minutes)

        return count


async def sync_outbox_to_queue(batch_size: int = 100) -> int:
    """Sync pending outbox entries to Redis queue.

    Pending rows are selected with ``FOR UPDATE SKIP LOCKED`` where supported,
    pushed to Redis, and deleted only after Redis confirms the enqueue. Failed
    pushes remain ``pending`` for the next sync cycle.

    This is intentionally at-least-once. Queue helpers deduplicate Redis entries,
    so a crash after Redis push but before DB delete can be retried without
    creating unbounded duplicate work, while a Redis outage cannot lose outbox
    rows by moving them to a terminal "enqueued" state too early.
    """
    session_factory = get_async_session_factory()
    synced = 0

    async with session_factory() as db:
        claim_result = await db.execute(
            select(Outbox)
            .where(Outbox.status == "pending")
            .order_by(Outbox.created_at)
            .limit(batch_size)
            .with_for_update(skip_locked=True)
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
                    "failed_to_enqueue_job_from_outbox", job_id=str(entry.job_id), error=str(e)
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
    """Delete outbox entries that are older than the given threshold.

    This provides periodic cleanup for entries that escaped normal
    processing (e.g., stuck 'enqueued' rows, orphaned entries).

    Only deletes entries with status 'enqueued' or 'completed' — pending
    entries are kept for crash recovery (sync_outbox_to_queue needs them).
    """
    session_factory = get_async_session_factory()
    cutoff = datetime.now(UTC) - timedelta(hours=hours)
    async with session_factory() as db:
        result = await db.execute(
            delete(Outbox).where(
                Outbox.created_at < cutoff, Outbox.status.in_(["enqueued", "completed"])
            )
        )
        await db.commit()
        count = result.rowcount
        if count > 0:
            logger.info("outbox_cleanup_completed", deleted=count, cutoff_age_hours=hours)
        return count

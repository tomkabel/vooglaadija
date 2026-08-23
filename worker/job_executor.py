"""Download execution behavior for claimed worker jobs."""

import asyncio
import contextlib
import json
import os
import random
import time
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from typing import Any, cast
from uuid import UUID

from sqlalchemy import select, update
from sqlalchemy.engine import CursorResult
from sqlalchemy.exc import OperationalError
from sqlalchemy.ext.asyncio import AsyncSession

from app.services.circuit_breaker import extract_media_with_circuit_breaker
from app.services.error_classifier import get_attempt_timeout
from app.services.pubsub_service import get_pubsub_service
from app.services.throttle_predictor import get_risk_score, risk_check_and_warn
from core.config import settings
from core.database import get_async_session_factory
from core.logging_config import get_logger
from core.metrics import JOBS_COMPLETED, RECOVERIES
from core.models.download_job import DownloadJob
from core.models.outbox import Outbox
from core.queue import redis_client
from worker.browser_executor import (
    extract_media as extract_media_browser,
)
from worker.browser_executor import (
    select_executor,
)
from worker.health import update_worker_state
from worker.job_claimer import heartbeat, periodic_heartbeat

logger = get_logger(__name__)


class ExecutionStatus(StrEnum):
    """Explicit result states returned by the executor."""

    COMPLETED = "completed"
    CONSUMED = "consumed"
    REQUEUED = "requeued"
    ERROR = "error"


@dataclass(slots=True)
class ExecutionResult:
    """Result contract between the executor and processor orchestrator."""

    status: ExecutionStatus
    job_id: UUID
    job: DownloadJob | None = None
    error: BaseException | None = None
    completed: bool = False


def _resolve_executor_kind(url: str) -> str:
    """
    Select the executor kind for a media URL, honoring the browser downloader setting.

    Parameters:
        url (str): Media URL used to determine the executor kind.

    Returns:
        str: `"youtube"` when browser downloading is disabled; otherwise, the executor kind selected for the URL.
    """
    if not settings.browser_downloader_enabled:
        return "youtube"
    return select_executor(url)


async def publish_job_status(job: DownloadJob) -> None:
    """Publish the current job status to the user's pub/sub channel."""
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


async def requeue_job(job_id: UUID, db: AsyncSession) -> bool:
    """
    Requeue a processing job through the transactional outbox.

    Parameters:
        job_id (UUID): Identifier of the job to requeue.

    Returns:
        bool: `True` if the job was processing and requeued, `False` otherwise.
    """
    outbox_entry = Outbox(
        id=uuid.uuid4(),
        job_id=job_id,
        event_type="retry_scheduled",
        payload=json.dumps(
            {
                "retry_count": 0,
                "next_retry_at": datetime.now(UTC).isoformat(),
            },
        ),
        status="pending",
    )
    db.add(outbox_entry)

    result = cast(
        CursorResult[Any],
        await db.execute(
            update(DownloadJob)
            .where(
                DownloadJob.id == job_id,
                DownloadJob.status == "processing",
            )
            .values(
                status="pending",
                updated_at=datetime.now(UTC),
            ),
        ),
    )
    await db.commit()
    if result.rowcount == 0:
        logger.warning("requeue_skipped_job_not_processing", job_id=str(job_id))
        return False
    return True


def cleanup_downloaded_file(file_path: str | None) -> None:
    """Remove a downloaded file when the job cannot safely keep ownership of it."""
    if file_path:
        try:
            if os.path.exists(file_path):
                os.remove(file_path)
                logger.info("Cleaned up partial download: %s", file_path)
        except OSError as e:
            logger.warning("Failed to clean up partial download %s: %s", file_path, e)


async def check_chaos_injection(db: AsyncSession, job_id: UUID, start_time: float) -> bool:
    """
    Run configured execution-time chaos scenarios and indicate whether the job was consumed.

    Parameters:
        db: Database session used for job recovery and requeueing.
        job_id (UUID): Identifier of the job being processed.
        start_time (float): Start time used to record the job's duration during recovery.

    Returns:
        bool: `true` if the job was recovered or requeued, `false` otherwise.
    """
    from worker.state import shutdown_event

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
                .values(status="pending", updated_at=datetime.now(UTC)),
            )
            await db.commit()
            RECOVERIES.labels(reason="zombie_sweep_recovery").inc()
            update_worker_state(status="running", current_job_started_at=None)
            from core.metrics import JOB_DURATION_SECONDS

            JOB_DURATION_SECONDS.observe(time.time() - start_time)
            return True
    except Exception:
        logger.debug("zombie_sweep_recovery skipped (non-critical)", exc_info=True)

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
        logger.debug("chaos_db_failover check skipped (non-critical)", exc_info=True)

    try:
        if await redis_client.exists("chaos:slow_processing"):
            delay = random.uniform(5.0, 20.0)  # noqa: S311 — chaos testing, not crypto
            logger.info("chaos_slow_processing", job_id=str(job_id), delay_seconds=round(delay, 1))
            await asyncio.sleep(delay)
    except Exception:
        logger.debug("chaos_slow_processing skipped (non-critical)", exc_info=True)

    if shutdown_event.is_set():
        logger.info("Shutdown requested, requeueing job %s", job_id)
        await requeue_job(job_id, db)
        update_worker_state(status="running", current_job_started_at=None)
        return True

    return False


async def execute(
    db: AsyncSession,
    job: DownloadJob,
    *,
    start_time: float,
    worker_main_module: Any | None = None,
) -> ExecutionResult:
    """
    Process an already-claimed download job and report its execution outcome.

    Parameters:
        job (DownloadJob): The claimed job to process.
        start_time (float): Monotonic timestamp used for execution and fault-injection timing.
        worker_main_module (Any | None): Optional worker module providing shutdown state and grace-period settings.

    Returns:
        ExecutionResult: The job ID, resulting execution status, and any completed job or error details.
    """
    from worker.state import shutdown_event

    if worker_main_module is None:
        worker_main_module = __import__("worker.main", fromlist=[""])

    job_id = job.id
    stop_hb: asyncio.Event | None = None
    hb_task: asyncio.Task[None] | None = None

    try:
        if await check_chaos_injection(db, job_id, start_time):
            return ExecutionResult(ExecutionStatus.CONSUMED, job_id, job=job)

        # Phase 2: route the job to the right executor. Browser-platform
        # jobs skip the throttle predictor (yt-dlp-specific signal) and the
        # progress callback (microservice is single-shot HTTP). The feature
        # flag forces a fallback to yt-dlp when the microservice is disabled.
        executor_kind = _resolve_executor_kind(job.url)

        if executor_kind == "youtube" and settings.feature_throttle_preemptive_enabled:
            throttle_risk = await get_risk_score("youtube")
            if throttle_risk >= 1.0:
                logger.warning(
                    "preemptive_throttle_block",
                    job_id=str(job_id),
                    risk_score=throttle_risk,
                )
                await requeue_job(job_id, db)
                JOBS_COMPLETED.labels(status="deferred").inc()
                update_worker_state(status="running", current_job_started_at=None)
                return ExecutionResult(
                    ExecutionStatus.REQUEUED,
                    job_id,
                    job=job,
                    completed=True,
                )
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

        async def progress_callback(progress_data: dict) -> None:
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

        attempt_timeout = get_attempt_timeout(job.retry_count)

        shutdown_ts = getattr(worker_main_module, "shutdown_requested_at", None)
        if shutdown_ts is not None:
            elapsed = time.monotonic() - shutdown_ts
            grace_period = getattr(worker_main_module, "GRACE_PERIOD_SECONDS", 25)
            remaining = grace_period - elapsed
            if remaining > 2.0:
                attempt_timeout = min(attempt_timeout, remaining - 2.0)
                attempt_timeout = max(attempt_timeout, 1.0)
                logger.info(
                    "shutdown_shortening_extraction_timeout",
                    job_id=str(job_id),
                    remaining_grace=round(remaining, 1),
                    extraction_timeout=round(attempt_timeout, 1),
                )
            elif remaining <= 2.0:
                logger.warning(
                    "shutdown_grace_too_short_requeueing",
                    job_id=str(job_id),
                    remaining_grace=round(remaining, 1),
                )
                await requeue_job(job_id, db)
                update_worker_state(status="running", current_job_started_at=None)
                return ExecutionResult(ExecutionStatus.REQUEUED, job_id, job=job)

        stop_hb = asyncio.Event()
        hb_task = asyncio.create_task(
            periodic_heartbeat(get_async_session_factory(), job_id, stop_hb),
        )

        loop = asyncio.get_running_loop()
        if executor_kind == "browser":
            logger.info(
                "job_routed_to_browser_executor",
                job_id=str(job_id),
                url=job.url,
            )
            extract_task = loop.create_task(
                extract_media_browser(
                    job.url,
                    settings.storage_path,
                    # Browser jobs now stream progress too: the microservice
                    # emits NDJSON progress events which browser_executor
                    # forwards to this callback (same pub/sub shape as yt-dlp).
                    progress_callback=progress_callback,
                ),
            )
        else:
            extract_task = loop.create_task(
                extract_media_with_circuit_breaker(
                    job.url,
                    settings.storage_path,
                    progress_callback=progress_callback,
                ),
            )

        try:
            file_path, file_name, title = await asyncio.wait_for(
                extract_task,
                timeout=attempt_timeout,
            )
        except TimeoutError:
            extract_task.cancel()
            try:
                await extract_task
            except (asyncio.CancelledError, Exception):
                logger.debug("extract_task cancel cleanup (non-critical)", exc_info=True)

            if getattr(worker_main_module, "shutdown_requested_at", None) is not None:
                await requeue_job(job_id, db)
                cleanup_downloaded_file(None)
                update_worker_state(status="running", current_job_started_at=None)
                JOBS_COMPLETED.labels(status="deferred").inc()
                logger.info("shutdown_requeued_timed_out_job", job_id=str(job_id))
                return ExecutionResult(ExecutionStatus.REQUEUED, job_id, job=job)

            raise TimeoutError(
                f"Extraction timed out after {attempt_timeout}s (attempt {job.retry_count + 1})",
            ) from None

        if shutdown_event.is_set():
            logger.info("Shutdown requested after download, requeueing job %s", job_id)
            await requeue_job(job_id, db)
            cleanup_downloaded_file(file_path)
            update_worker_state(status="running", current_job_started_at=None)
            return ExecutionResult(ExecutionStatus.REQUEUED, job_id, job=job)

        await heartbeat(db, job_id)

        result = cast(
            CursorResult[Any],
            await db.execute(
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
                ),
            ),
        )
        await db.commit()
        if result.rowcount == 0:
            cleanup_downloaded_file(file_path)
            logger.warning("job_already_requeued_by_zombie_sweeper", job_id=str(job_id))
            update_worker_state(status="running", current_job_started_at=None)
            return ExecutionResult(ExecutionStatus.CONSUMED, job_id, job=job)
        await db.commit()

        refreshed = await db.execute(select(DownloadJob).where(DownloadJob.id == job_id))
        completed_job = refreshed.scalar_one_or_none()
        if completed_job:
            await publish_job_status(completed_job)

        update_worker_state(status="running", current_job_started_at=None)
        JOBS_COMPLETED.labels(status="success").inc()
        logger.info("job_completed_successfully", job_id=str(job_id))

        return ExecutionResult(
            ExecutionStatus.COMPLETED,
            job_id,
            job=completed_job or job,
            completed=True,
        )

    except asyncio.CancelledError:
        logger.info("Job %s cancelled, requeueing...", job_id)
        await requeue_job(job_id, db)
        update_worker_state(status="running", current_job_started_at=None)
        raise

    except Exception as e:
        return ExecutionResult(ExecutionStatus.ERROR, job_id, job=job, error=e)

    finally:
        try:
            if stop_hb is not None:
                stop_hb.set()
            if hb_task is not None:
                with contextlib.suppress(asyncio.CancelledError):
                    await hb_task
        except Exception:
            logger.warning("heartbeat_task_cleanup_failed", job_id=str(job_id), exc_info=True)

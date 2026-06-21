"""Worker main loop with circuit-aware deferred queue draining and retry throttle."""

import asyncio
import os
import signal
import time as _time
from datetime import UTC, datetime, timedelta

from sqlalchemy import select

from core.config import settings
from core.database import get_async_session_factory
from core.logging_config import configure_logging, get_logger
from core.metrics import CIRCUIT_DEFERRED_DEPTH, QUEUE_DEPTH
from core.models.download_job import DownloadJob
from core.utils.security import validate_path
from worker.dlq_manager import cleanup_expired_dlq, update_dlq_depth as _update_dlq_depth
from worker.health import (
    close_health_redis_client,
    start_health_server,
    stop_health_server,
    update_worker_state,
    write_health_async,
)
from worker.outbox_relay import cleanup_stale_outbox_entries, sync_outbox_to_queue
from worker.processor import _drain_circuit_deferred, process_next_job
from core.queue import redis_client
from worker.state import shutdown_event
from worker.zombie_sweeper import requeue_stuck_jobs

# Module-level state — used by signal handler and main loop.
# Tests mutate these directly via `worker.main.GRACE_PERIOD_SECONDS = ...`
GRACE_PERIOD_SECONDS: int = int(os.environ.get("WORKER_GRACE_PERIOD_SECONDS", "25"))
shutdown_requested_at: float | None = None

configure_logging(log_level=os.environ.get("LOG_LEVEL", "INFO"))
logger = get_logger(__name__)

# Re-export for test compatibility — tests access worker.main.{name}
__all__ = [
    "GRACE_PERIOD_SECONDS",
    "_signal_handler",
    "get_grace_period_remaining",
    "main",
    "shutdown_event",
    "shutdown_requested_at",
]

# Max retries to release per main-loop iteration (prevents thundering herd)
MAX_RETRY_BATCH = 10


def _signal_handler() -> None:
    """Handle shutdown signals gracefully with timestamp tracking."""
    global shutdown_requested_at
    if shutdown_requested_at is None:
        shutdown_requested_at = _time.monotonic()
        logger.info(
            "received_shutdown_signal",
            signal="SIGTERM/SIGINT",
            grace_period_seconds=GRACE_PERIOD_SECONDS,
        )
    shutdown_event.set()


def get_grace_period_remaining() -> float | None:
    """Get remaining grace period in seconds, or None if shutdown not requested."""
    if shutdown_requested_at is None:
        return None
    elapsed = _time.monotonic() - shutdown_requested_at
    remaining = GRACE_PERIOD_SECONDS - elapsed
    return max(0.0, remaining)


async def _update_circuit_deferred_depth() -> None:
    try:
        depth = await redis_client.zcard("circuit_deferred_queue")
        CIRCUIT_DEFERRED_DEPTH.set(depth)
    except Exception:
        pass


async def cleanup_expired_jobs() -> int:
    """Delete expired jobs and their files."""
    session_factory = get_async_session_factory()
    downloads_dir = os.path.join(settings.storage_path, "downloads")

    async with session_factory() as db:
        now = datetime.now(UTC)

        result = await db.execute(
            select(DownloadJob).where(
                DownloadJob.expires_at < now, DownloadJob.status == "completed"
            )
        )
        expired_jobs = result.scalars().all()

        cleanup_count = 0
        for job in expired_jobs:
            if job.file_path:
                try:
                    safe_path = validate_path(downloads_dir, job.file_path)
                except (ValueError, PermissionError):
                    logger.warning(
                        "path_traversal_attempt_skipped",
                        job_id=str(job.id),
                        file_path=job.file_path,
                    )
                    continue

                if os.path.exists(safe_path):
                    try:
                        os.remove(safe_path)
                        logger.info(
                            "cleaned_up_expired_file", file_path=safe_path, job_id=str(job.id)
                        )
                        await db.delete(job)
                        cleanup_count += 1
                    except OSError as e:
                        logger.warning(
                            "failed_to_delete_expired_file", file_path=job.file_path, error=str(e)
                        )
                else:
                    logger.info("file_already_deleted", job_id=str(job.id), file_path=job.file_path)
                    await db.delete(job)
                    cleanup_count += 1
            else:
                try:
                    await db.delete(job)
                    cleanup_count += 1
                except Exception as db_err:
                    logger.warning(
                        "failed_to_delete_db_row", job_id=job.id, error=str(db_err), exc_info=True
                    )

        try:
            await db.commit()
        except Exception as commit_err:
            logger.warning("db_commit_failed", error=str(commit_err))

        if cleanup_count > 0:
            logger.info("cleanup_completed", expired_jobs_cleaned=cleanup_count)

        return cleanup_count


async def _update_queue_depth() -> None:
    try:
        lua_script = """
        local dl = redis.call('LLEN', KEYS[1])
        local rt = redis.call('ZCARD', KEYS[2])
        local cd = redis.call('ZCARD', KEYS[3])
        return dl + rt + cd
        """
        total = await redis_client.eval(
            lua_script, 3, "download_queue", "retry_queue", "circuit_deferred_queue"
        )
        QUEUE_DEPTH.set(int(total))
    except Exception as e:
        logger.warning("queue_depth_update_failed", error=str(e))


async def main() -> None:
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(sig, _signal_handler)

    logger.info("worker_started")

    logger.info("testing_redis_connection")
    try:
        await redis_client.ping()
        logger.info("redis_connection_successful")
    except Exception as e:
        logger.error("redis_connection_failed", error=str(e))
        raise

    logger.info("testing_database_connection")
    try:
        session_factory = get_async_session_factory()
        async with session_factory() as db:
            from sqlalchemy import text

            await db.execute(text("SELECT 1"))
        logger.info("database_connection_successful")
    except Exception as e:
        logger.error("database_connection_failed", error=str(e))
        raise

    health_server = start_health_server()

    cleanup_interval_minutes: int = int(os.environ.get("CLEANUP_INTERVAL_MINUTES", "5"))
    cleanup_interval = timedelta(minutes=cleanup_interval_minutes)
    last_cleanup = datetime.now(UTC) - cleanup_interval

    try:
        outbox_sync_interval_seconds = int(os.environ.get("OUTBOX_SYNC_INTERVAL_SECONDS", "30"))
    except (ValueError, TypeError):
        outbox_sync_interval_seconds = 30
    outbox_sync_interval_seconds = max(1, min(outbox_sync_interval_seconds, 3600))
    outbox_sync_interval = timedelta(seconds=outbox_sync_interval_seconds)
    last_outbox_sync = datetime.now(UTC) - outbox_sync_interval

    heartbeat_counter = 0
    heartbeat_interval = 10
    queue_depth_counter = 0
    queue_depth_interval = 5
    brpop_timeout = 2

    deferred_drain_counter = 0
    deferred_drain_interval = 5

    update_worker_state(status="running")

    # Track in-flight extraction for grace-period enforcement
    current_job_task: asyncio.Task | None = None

    while not shutdown_event.is_set():
        grace_remaining = get_grace_period_remaining()
        if grace_remaining is not None and grace_remaining <= 0:
            logger.warning(
                "grace_period_expired_forcing_shutdown",
                grace_period_seconds=GRACE_PERIOD_SECONDS,
            )
            break

        try:
            # Move due retry jobs from retry_queue to download_queue
            # With retry release throttle: max 10 per iteration
            now_ts = datetime.now(UTC).timestamp()
            lua_script = """
            local due_jobs = redis.call('ZRANGEBYSCORE', KEYS[1], 0, ARGV[1], 'LIMIT', 0, ARGV[2])
            if #due_jobs > 0 then
                redis.call('ZREM', KEYS[1], unpack(due_jobs))
                for _, job_id in ipairs(due_jobs) do
                    redis.call('LPUSH', KEYS[2], job_id)
                end
            end
            return #due_jobs
            """
            moved_count = await redis_client.eval(
                lua_script, 2, "retry_queue", "download_queue", now_ts, MAX_RETRY_BATCH
            )
            if moved_count and moved_count > 0:
                logger.info("retry_jobs_moved", moved_count=moved_count)

            deferred_drain_counter += 1
            if deferred_drain_counter >= deferred_drain_interval:
                deferred_drain_counter = 0
                drained = await _drain_circuit_deferred(max_batch=10)
                if drained:
                    logger.info("circuit_deferred_drained", count=drained)

            grace_remaining = get_grace_period_remaining()
            if grace_remaining is not None and grace_remaining <= 0:
                break
            effective_timeout = min(brpop_timeout, grace_remaining or brpop_timeout)
            effective_timeout = max(1, int(effective_timeout))

            result = await redis_client.brpop("download_queue", timeout=effective_timeout)
            if result:
                _, job_id_str = result

                # Wrap in a Task so we can enforce remaining grace period
                current_job_task = asyncio.create_task(process_next_job(job_id_str))
                try:
                    remaining = get_grace_period_remaining()
                    if remaining is not None and remaining > 1:
                        await asyncio.wait_for(
                            asyncio.shield(current_job_task),
                            timeout=remaining,
                        )
                    else:
                        await current_job_task
                except TimeoutError:
                    logger.warning(
                        "job_timeout_during_shutdown_killed",
                        job_id=job_id_str,
                    )
                    current_job_task.cancel()
                    try:
                        await asyncio.wait_for(current_job_task, timeout=3.0)
                    except (asyncio.CancelledError, TimeoutError):
                        pass
                finally:
                    current_job_task = None

        except asyncio.CancelledError:
            logger.info("Worker loop cancelled, exiting...")
            break
        except Exception as e:
            logger.error("job_processing_error", error=str(e))
            await asyncio.sleep(1)

        now = datetime.now(UTC)

        if now - last_outbox_sync >= outbox_sync_interval:
            try:
                synced = await sync_outbox_to_queue()
                if synced > 0:
                    logger.info("outbox_sync_completed", synced=synced)
                last_outbox_sync = now
            except Exception as e:
                logger.error("outbox_sync_error", error=str(e))

        if now - last_cleanup >= cleanup_interval:
            try:
                await cleanup_expired_jobs()
            except Exception as e:
                logger.error("expired_job_cleanup_error", error=str(e))

            try:
                await requeue_stuck_jobs(timeout_minutes=15)
            except Exception as e:
                logger.error("zombie_sweep_error", error=str(e))

            try:
                await cleanup_expired_dlq()
            except Exception as e:
                logger.error("dlq_cleanup_error", error=str(e))

            try:
                await _update_dlq_depth()
            except Exception as e:
                logger.error("dlq_depth_update_error", error=str(e))

            try:
                await _update_circuit_deferred_depth()
            except Exception as e:
                logger.error("circuit_deferred_depth_update_error", error=str(e))

            try:
                await cleanup_stale_outbox_entries(hours=24)
            except Exception as e:
                logger.error("outbox_cleanup_error", error=str(e))

            last_cleanup = now
            update_worker_state(last_cleanup=last_cleanup.isoformat())

        heartbeat_counter += 1
        if heartbeat_counter >= heartbeat_interval:
            try:
                await write_health_async()
                update_worker_state()
            except Exception as e:
                logger.warning("health_write_failed", error=str(e))
            heartbeat_counter = 0

        queue_depth_counter += 1
        if queue_depth_counter >= queue_depth_interval:
            await _update_queue_depth()
            queue_depth_counter = 0

        if shutdown_event.is_set():
            grace_remaining = get_grace_period_remaining()
            logger.info(
                "Shutdown requested, exiting main loop...",
                grace_period_seconds_remaining=grace_remaining,
            )
            break

    # Drain any remaining in-flight job task
    if current_job_task is not None and not current_job_task.done():
        current_job_task.cancel()
        try:
            await asyncio.wait_for(current_job_task, timeout=3.0)
        except (asyncio.CancelledError, TimeoutError):
            pass
        current_job_task = None

    logger.info("Worker shutdown complete, stopping health server...")

    if health_server:
        stop_health_server()
    await close_health_redis_client()
    logger.info("worker_stopped_gracefully")


if __name__ == "__main__":
    asyncio.run(main())

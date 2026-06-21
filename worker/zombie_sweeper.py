"""Zombie Sweeper module for handling jobs stuck in PROCESSING state.

This module handles SIGKILL/OOM scenarios where graceful shutdown never runs.
It polls for jobs that have been stuck in 'PROCESSING' status for too long
and requeues them as 'pending' instead of marking them as failed.

Uses a bulk UPDATE with RETURNING to atomically requeue stuck jobs,
avoiding the O(N) per-job savepoint loop and heartbeat-vs-sweeper races.

Outbox entries are created for each requeued job; the outbox relay
handles Redis enqueue asynchronously.

Poll interval: 5 minutes
Timeout: 15 minutes stuck in 'PROCESSING' = zombie
"""

import json
import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy import update

from core.database import get_async_session_factory
from app.logging_config import get_logger
from core.models.download_job import DownloadJob
from core.models.outbox import Outbox
from app.services.redis_client import CHAOS_ZOMBIE_JOB_KEY, get_redis_client

logger = get_logger(__name__)


async def requeue_stuck_jobs(timeout_minutes: int = 15) -> int:
    """Requeue jobs that have been stuck in 'PROCESSING' status too long.

    These are "zombie" jobs - workers that were killed (SIGKILL/OOM) without
    running graceful shutdown, leaving jobs permanently stuck in PROCESSING.

    Uses a single atomic bulk UPDATE with RETURNING to avoid:
    - O(N) SAVEPOINT overhead (prevents savepoint exhaustion on large backlogs)
    - Heartbeat race: the WHERE clause skips recently-updated rows, so active
      workers' heartbeats don't interfere with zombie detection
    - Dual-write issues: outbox entries are created after the RETURNING,
      under the same transaction

    When the chaos zombie trigger key exists in Redis, the timeout is
    automatically shortened to 1 minute so the demo recovery is visible
    within the 3-minute demo window.

    Args:
        timeout_minutes: Jobs stuck in PROCESSING for longer than this are requeued.
            When CHAOS_ZOMBIE_JOB_KEY is set, this is clamped to max 1 minute
            for demo responsiveness.

    Returns:
        Number of jobs requeued.
    """
    # Chaos-aware: if the chaos zombie key is active, clamp to 1 minute max
    # so the demo doesn't wait 15 minutes for recovery visualization
    try:
        client = get_redis_client()
        chaos_active = await client.exists(CHAOS_ZOMBIE_JOB_KEY)
        if chaos_active and timeout_minutes > 1:
            logger.info(
                "chaos_zombie_key_detected_shortening_timeout",
                original_minutes=timeout_minutes,
                clamped_minutes=1,
            )
            timeout_minutes = 1
    except Exception:
        pass

    cutoff = datetime.now(UTC) - timedelta(minutes=timeout_minutes)
    session_factory = get_async_session_factory()

    async with session_factory() as db:
        # Atomic bulk UPDATE: only rows that haven't been touched recently.
        # The WHERE updated_at < cutoff ensures active heartbeats keep their
        # jobs out of the sweep, preventing false-positive zombie claims.
        result = await db.execute(
            update(DownloadJob)
            .where(
                DownloadJob.status == "processing",
                DownloadJob.updated_at < cutoff,
            )
            .values(
                status="pending",
                updated_at=datetime.now(UTC),
            )
            .returning(DownloadJob.id)
        )
        requeued_ids = result.scalars().all()

        if not requeued_ids:
            logger.debug("no_zombie_jobs_found", timeout_minutes=timeout_minutes)
            return 0

        # Create outbox entries for each requeued job
        now = datetime.now(UTC)
        for job_id in requeued_ids:
            db.add(
                Outbox(
                    id=uuid.uuid4(),
                    job_id=job_id,
                    event_type="zombie_recovery",
                    payload=json.dumps({"recovered_at": now.isoformat()}),
                    status="pending",
                )
            )

        try:
            await db.commit()
        except Exception as e:
            logger.error(
                "zombie_sweep_commit_failed",
                error=str(e),
                requeued_count=len(requeued_ids),
            )
            return 0

        logger.warning(
            "zombie_sweep_completed",
            requeued_count=len(requeued_ids),
            timeout_minutes=timeout_minutes,
        )

        return len(requeued_ids)

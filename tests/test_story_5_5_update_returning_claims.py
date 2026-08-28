"""Story 5.5 guardrails for UPDATE RETURNING worker claim and reset paths."""

import asyncio
import inspect
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import AsyncMock, patch
from uuid import UUID, uuid4

import pytest
from sqlalchemy import select

from core.database import get_async_session_factory
from core.models.download_job import DownloadJob
from core.models.outbox import Outbox

pytestmark = pytest.mark.slow


@pytest.mark.unit
def test_claim_next_uses_update_returning_without_follow_up_select() -> None:
    """The job claim remains a single UPDATE RETURNING path without post-claim SELECT."""
    from worker.job_claimer import claim_next

    source = inspect.getsource(claim_next)

    assert "update(DownloadJob)" in source
    assert "DownloadJob.id == normalized_job_id" in source
    assert 'DownloadJob.status == "pending"' in source
    assert 'status="processing"' in source
    assert "updated_at=datetime.now(UTC)" in source
    assert ".returning(DownloadJob)" in source
    assert "synchronize_session=False" in source
    assert "scalar_one_or_none()" in source
    assert "await db.commit()" in source
    assert "select(" not in source


@pytest.mark.unit
def test_heartbeat_paths_do_not_participate_in_returning_claim() -> None:
    """Heartbeat helpers only refresh timestamps and do not use RETURNING."""
    from worker.job_claimer import heartbeat, periodic_heartbeat

    heartbeat_source = inspect.getsource(heartbeat)
    periodic_source = inspect.getsource(periodic_heartbeat)

    assert "updated_at=datetime.now(UTC)" in heartbeat_source
    assert ".returning(" not in heartbeat_source
    assert 'status="processing"' not in heartbeat_source
    assert "updated_at=datetime.now(UTC)" in periodic_source
    assert ".returning(" not in periodic_source
    assert 'status="processing"' not in periodic_source


@pytest.mark.unit
def test_zombie_sweeper_uses_bulk_update_returning_ids_for_outbox() -> None:
    """Zombie recovery uses returned IDs from one bulk UPDATE as the outbox source."""
    from worker.zombie_sweeper import requeue_stuck_jobs

    source = inspect.getsource(requeue_stuck_jobs)

    assert "update(DownloadJob)" in source
    assert 'DownloadJob.status == "processing"' in source
    assert "DownloadJob.updated_at < cutoff" in source
    assert 'status="pending"' in source
    assert ".returning(DownloadJob.id)" in source
    assert "requeued_ids = result.scalars().all()" in source
    assert "for job_id in requeued_ids:" in source
    assert source.index(".returning(DownloadJob.id)") < source.index("for job_id in requeued_ids:")
    assert "select(" not in source


@pytest.mark.unit
def test_dlq_reset_uses_returning_before_post_commit_publication() -> None:
    """The stuck-job reset returns job/user IDs and publishes only after commit."""
    from worker.dlq_manager import reset_stuck_jobs

    source = inspect.getsource(reset_stuck_jobs)

    assert "update(DownloadJob)" in source
    assert 'DownloadJob.status == "processing"' in source
    assert "DownloadJob.updated_at < cutoff" in source
    assert 'status="failed"' in source
    assert ".returning(DownloadJob.id, DownloadJob.user_id)" in source
    assert "affected = result.fetchall()" in source
    assert source.index("await db.commit()") < source.index("publish_job_status")


@pytest.mark.unit
def test_processor_keeps_queue_pop_and_invalid_uuid_handling_in_claimer() -> None:
    """The processor still delegates queue pop and ID normalization to job_claimer."""
    processor_source = Path("worker/processor.py").read_text()
    claimer_source = Path("worker/job_claimer.py").read_text()

    assert "resolved_job_id = await job_claimer.next_job_id(job_id)" in processor_source
    assert "await job_claimer.claim_next(db, resolved_job_id)" in processor_source
    assert "redis_client.rpop" in claimer_source
    assert "normalize_job_id" in claimer_source
    assert "invalid_job_id" in claimer_source


@pytest.mark.unit
@pytest.mark.asyncio
async def test_claim_next_returns_pending_job_once_and_persists_processing(db_session) -> None:
    """A pending job is claimed once and remains processing in the database."""
    from worker.job_claimer import claim_next

    job_id = uuid4()
    db_session.add(
        DownloadJob(
            id=job_id,
            user_id=uuid4(),
            url="https://www.youtube.com/watch?v=story55single",
            status="pending",
        )
    )
    await db_session.commit()

    session_factory = get_async_session_factory()
    async with session_factory() as first_session:
        claimed = await claim_next(first_session, job_id)
    async with session_factory() as second_session:
        claimed_again = await claim_next(second_session, job_id)
    async with session_factory() as verification_session:
        result = await verification_session.execute(
            select(DownloadJob).where(DownloadJob.id == job_id)
        )
        persisted = result.scalar_one()

    assert claimed is not None
    assert claimed.id == job_id
    assert claimed.status == "processing"
    assert claimed_again is None
    assert persisted.status == "processing"


@pytest.mark.unit
@pytest.mark.asyncio
async def test_claim_next_concurrent_workers_claim_exactly_once(db_session) -> None:
    """Two concurrent claim attempts return one job and one None with a processing row."""
    from worker.job_claimer import claim_next

    job_id = uuid4()
    db_session.add(
        DownloadJob(
            id=job_id,
            user_id=uuid4(),
            url="https://www.youtube.com/watch?v=story55concurrent",
            status="pending",
        )
    )
    await db_session.commit()

    session_factory = get_async_session_factory()

    async def try_claim() -> DownloadJob | None:
        async with session_factory() as session:
            return await claim_next(session, job_id)

    results = await asyncio.gather(try_claim(), try_claim())

    async with session_factory() as verification_session:
        result = await verification_session.execute(
            select(DownloadJob).where(DownloadJob.id == job_id)
        )
        persisted = result.scalar_one()

    assert sum(result is not None for result in results) == 1
    assert sum(result is None for result in results) == 1
    assert persisted.status == "processing"


@pytest.mark.unit
@pytest.mark.asyncio
async def test_zombie_requeue_writes_outbox_only_for_returned_stuck_jobs(db_session) -> None:
    """Zombie recovery requeues stale processing jobs and writes matching outbox rows."""
    from worker.zombie_sweeper import requeue_stuck_jobs

    stuck_id = UUID("550e8400-e29b-41d4-a716-446655445501")
    recent_id = UUID("550e8400-e29b-41d4-a716-446655445502")
    pending_id = UUID("550e8400-e29b-41d4-a716-446655445503")
    user_id = uuid4()
    db_session.add_all(
        [
            DownloadJob(
                id=stuck_id,
                user_id=user_id,
                url="https://www.youtube.com/watch?v=story55stuck",
                status="processing",
                updated_at=datetime.now(UTC) - timedelta(minutes=20),
            ),
            DownloadJob(
                id=recent_id,
                user_id=user_id,
                url="https://www.youtube.com/watch?v=story55recent",
                status="processing",
                updated_at=datetime.now(UTC) - timedelta(minutes=2),
            ),
            DownloadJob(
                id=pending_id,
                user_id=user_id,
                url="https://www.youtube.com/watch?v=story55pending",
                status="pending",
            ),
        ]
    )
    await db_session.commit()

    mock_redis = AsyncMock()
    mock_redis.exists = AsyncMock(return_value=0)

    with patch("worker.zombie_sweeper.get_redis_client", return_value=mock_redis):
        count = await requeue_stuck_jobs(timeout_minutes=15)

    jobs_result = await db_session.execute(
        select(DownloadJob).where(DownloadJob.id.in_([stuck_id, recent_id, pending_id]))
    )
    jobs_by_id = {job.id: job for job in jobs_result.scalars().all()}
    outbox_result = await db_session.execute(select(Outbox).where(Outbox.job_id == stuck_id))
    outbox_entry = outbox_result.scalar_one_or_none()
    other_outbox_result = await db_session.execute(
        select(Outbox).where(Outbox.job_id.in_([recent_id, pending_id]))
    )

    assert count == 1
    assert jobs_by_id[stuck_id].status == "pending"
    assert jobs_by_id[recent_id].status == "processing"
    assert jobs_by_id[pending_id].status == "pending"
    assert outbox_entry is not None
    assert outbox_entry.event_type == "zombie_recovery"
    assert outbox_entry.status == "pending"
    assert other_outbox_result.scalars().all() == []


@pytest.mark.unit
@pytest.mark.asyncio
async def test_reset_stuck_jobs_updates_only_stale_processing_and_publishes(db_session) -> None:
    """DLQ reset fails only stale processing jobs and publishes failure status."""
    from worker.dlq_manager import reset_stuck_jobs

    stuck_id = UUID("550e8400-e29b-41d4-a716-446655445511")
    recent_id = UUID("550e8400-e29b-41d4-a716-446655445512")
    completed_id = UUID("550e8400-e29b-41d4-a716-446655445513")
    failed_id = UUID("550e8400-e29b-41d4-a716-446655445514")
    user_id = uuid4()
    db_session.add_all(
        [
            DownloadJob(
                id=stuck_id,
                user_id=user_id,
                url="https://www.youtube.com/watch?v=story55reset",
                status="processing",
                updated_at=datetime.now(UTC) - timedelta(minutes=20),
            ),
            DownloadJob(
                id=recent_id,
                user_id=user_id,
                url="https://www.youtube.com/watch?v=story55active",
                status="processing",
                updated_at=datetime.now(UTC) - timedelta(minutes=2),
            ),
            DownloadJob(
                id=completed_id,
                user_id=user_id,
                url="https://www.youtube.com/watch?v=story55done",
                status="completed",
                updated_at=datetime.now(UTC) - timedelta(minutes=20),
            ),
            DownloadJob(
                id=failed_id,
                user_id=user_id,
                url="https://www.youtube.com/watch?v=story55failed",
                status="failed",
                updated_at=datetime.now(UTC) - timedelta(minutes=20),
            ),
        ]
    )
    await db_session.commit()

    mock_pubsub = AsyncMock()

    with patch("worker.dlq_manager.get_pubsub_service", return_value=mock_pubsub):
        count = await reset_stuck_jobs(timeout_minutes=10)

    jobs_result = await db_session.execute(
        select(DownloadJob).where(
            DownloadJob.id.in_([stuck_id, recent_id, completed_id, failed_id])
        )
    )
    jobs_by_id = {job.id: job for job in jobs_result.scalars().all()}

    assert count == 1
    assert jobs_by_id[stuck_id].status == "failed"
    assert jobs_by_id[stuck_id].error == "Job timed out"
    assert jobs_by_id[stuck_id].error_category == "timeout"
    assert jobs_by_id[recent_id].status == "processing"
    assert jobs_by_id[completed_id].status == "completed"
    assert jobs_by_id[failed_id].status == "failed"
    mock_pubsub.publish_job_status.assert_awaited_once()
    publish_user_id, payload = mock_pubsub.publish_job_status.await_args.args
    assert publish_user_id == user_id
    assert payload["id"] == str(stuck_id)
    assert payload["status"] == "failed"
    assert payload["error_category"] == "timeout"

"""Story 3.5 guardrails for retry, DLQ, and outbox extraction."""

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import AsyncMock, Mock, patch
from uuid import uuid4

import pytest
from sqlalchemy import select

from app.services.error_classifier import ErrorCategory
from core.models.download_job import DownloadJob
from core.models.failed_job import FailedJob
from core.models.outbox import Outbox


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


@pytest.mark.unit
def test_worker_retry_dlq_outbox_modules_import_directly() -> None:
    """The extracted owner modules import directly while process_next_job stays public."""
    import worker.dlq_manager
    import worker.outbox_relay
    import worker.retry_scheduler
    from worker.processor import process_next_job

    assert worker.retry_scheduler is not None
    assert worker.dlq_manager is not None
    assert worker.outbox_relay is not None
    assert callable(process_next_job)


@pytest.mark.unit
def test_processor_no_longer_references_retry_dlq_outbox_concerns() -> None:
    """The processor no longer references direct retry, DLQ, or outbox implementation symbols."""
    source = Path("worker/processor.py").read_text()

    forbidden = [
        "FailedJob",
        "Outbox",
        "calculate_delay",
        "classify_error",
        "extract_retry_after",
        "format_attempt_error",
        "push_to_retry_queue",
    ]
    for symbol in forbidden:
        assert symbol not in source


@pytest.mark.unit
def test_extracted_module_ownership_and_executor_boundary() -> None:
    """Retry scheduler owns retry APIs and the executor stays free of retry and DLQ modules."""
    from worker import retry_scheduler

    assert hasattr(retry_scheduler, "RetryDecision")
    assert callable(retry_scheduler.evaluate)
    assert callable(retry_scheduler.schedule_retry)

    executor_source = Path("worker/job_executor.py").read_text()
    assert "retry_scheduler" not in executor_source
    assert "dlq_manager" not in executor_source


@pytest.mark.unit
def test_retry_evaluate_uses_retry_after_for_rate_limited_errors() -> None:
    """Retry evaluation passes Retry-After through to the canonical delay calculator."""
    from worker.retry_scheduler import evaluate

    job = DownloadJob(
        id=uuid4(),
        user_id=uuid4(),
        url="https://www.youtube.com/watch?v=retry-after",
        status="processing",
        retry_count=0,
        max_retries=5,
    )

    with patch("worker.retry_scheduler.calculate_delay", return_value=90.0) as delay_mock:
        decision = evaluate(job, RuntimeError("HTTP Error 429 Retry-After: 90"))

    delay_mock.assert_called_once_with(
        category=ErrorCategory.RATE_LIMITED,
        attempt=0,
        prev_delay=None,
        retry_after=90,
    )
    assert decision.is_final is False
    assert decision.category is ErrorCategory.RATE_LIMITED
    assert decision.delay_seconds == 90.0
    assert decision.retry_after == 90
    assert decision.effective_max_retries == 5
    assert decision.final_error is None
    assert decision.next_retry_at is not None


@pytest.mark.unit
@pytest.mark.asyncio
async def test_schedule_retry_updates_db_outbox_and_redis(db_session) -> None:
    """Retry scheduling updates the job, writes outbox, queues Redis, and deletes synced outbox."""
    from worker.retry_scheduler import evaluate, schedule_retry

    job_id = uuid4()
    job = DownloadJob(
        id=job_id,
        user_id=uuid4(),
        url="https://www.youtube.com/watch?v=schedule-retry",
        status="processing",
        retry_count=0,
        max_retries=5,
    )
    db_session.add(job)
    await db_session.commit()

    with patch("worker.retry_scheduler.calculate_delay", return_value=42.0):
        decision = evaluate(job, RuntimeError("HTTP Error 502 temporary failure"))

    queued: list[tuple[object, float]] = []

    async def record_retry(job_id_arg, retry_timestamp: float) -> bool:
        queued.append((job_id_arg, retry_timestamp))
        return True

    with (
        patch("worker.retry_scheduler.push_to_retry_queue", new_callable=AsyncMock) as push_mock,
        patch("worker.retry_scheduler.publish_job_status", new_callable=AsyncMock),
    ):
        push_mock.side_effect = record_retry
        enqueued = await schedule_retry(db_session, job, decision)

    assert enqueued is True
    assert len(queued) == 1
    assert queued[0][0] == job_id

    result = await db_session.execute(select(DownloadJob).where(DownloadJob.id == job_id))
    retried = result.scalar_one()
    assert retried.status == "pending"
    assert retried.retry_count == 1
    assert retried.error_category == ErrorCategory.TRANSIENT.value
    assert retried.next_retry_at is not None
    assert queued[0][1] == pytest.approx(_as_utc(retried.next_retry_at).timestamp(), abs=0.01)

    outbox_result = await db_session.execute(select(Outbox).where(Outbox.job_id == job_id))
    assert outbox_result.scalars().all() == []


@pytest.mark.unit
@pytest.mark.asyncio
async def test_schedule_retry_retains_pending_outbox_when_redis_fails(db_session) -> None:
    """Retry scheduling keeps the pending outbox row when Redis enqueue fails."""
    from worker.retry_scheduler import evaluate, schedule_retry

    job_id = uuid4()
    job = DownloadJob(
        id=job_id,
        user_id=uuid4(),
        url="https://www.youtube.com/watch?v=retry-redis-fail",
        status="processing",
        retry_count=0,
        max_retries=5,
    )
    db_session.add(job)
    await db_session.commit()

    with patch("worker.retry_scheduler.calculate_delay", return_value=42.0):
        decision = evaluate(job, RuntimeError("HTTP Error 503 temporary failure"))

    with (
        patch(
            "worker.retry_scheduler.push_to_retry_queue", new_callable=AsyncMock, return_value=False
        ),
        patch("worker.retry_scheduler.publish_job_status", new_callable=AsyncMock),
    ):
        enqueued = await schedule_retry(db_session, job, decision)

    assert enqueued is False
    outbox_result = await db_session.execute(select(Outbox).where(Outbox.job_id == job_id))
    pending_outbox = outbox_result.scalar_one()
    assert pending_outbox.status == "pending"
    assert pending_outbox.event_type == "retry_scheduled"


@pytest.mark.unit
@pytest.mark.asyncio
async def test_schedule_retry_skips_outbox_when_guarded_update_misses(db_session) -> None:
    """Retry scheduling does not create an outbox row when the job is not processing."""
    from worker.retry_scheduler import evaluate, schedule_retry

    job_id = uuid4()
    job = DownloadJob(
        id=job_id,
        user_id=uuid4(),
        url="https://www.youtube.com/watch?v=retry-skip",
        status="pending",
        retry_count=0,
        max_retries=5,
    )
    db_session.add(job)
    await db_session.commit()

    with patch("worker.retry_scheduler.calculate_delay", return_value=42.0):
        decision = evaluate(job, RuntimeError("HTTP Error 503 temporary failure"))

    with patch("worker.retry_scheduler.push_to_retry_queue", new_callable=AsyncMock) as push_mock:
        enqueued = await schedule_retry(db_session, job, decision)

    assert enqueued is False
    push_mock.assert_not_called()

    outbox_result = await db_session.execute(select(Outbox).where(Outbox.job_id == job_id))
    assert outbox_result.scalars().all() == []

    result = await db_session.execute(select(DownloadJob).where(DownloadJob.id == job_id))
    unchanged = result.scalar_one()
    assert unchanged.status == "pending"
    assert unchanged.retry_count == 0


@pytest.mark.unit
@pytest.mark.asyncio
async def test_move_to_dlq_writes_required_failed_job_fields_and_depth(db_session) -> None:
    """DLQ movement writes required FailedJob fields and updates the DLQ depth metric."""
    from worker.dlq_manager import move_to_dlq
    from worker.retry_scheduler import RetryDecision

    job_id = uuid4()
    user_id = uuid4()
    job = DownloadJob(
        id=job_id,
        user_id=user_id,
        url="https://www.youtube.com/watch?v=dlq",
        status="failed",
        error="attempt history",
        error_category=ErrorCategory.NOT_FOUND.value,
        retry_count=2,
        max_retries=3,
        title="DLQ Video",
    )
    db_session.add(job)
    await db_session.commit()

    decision = RetryDecision(
        is_final=True,
        delay_seconds=None,
        category=ErrorCategory.NOT_FOUND,
        effective_max_retries=0,
        final_error="Non-retryable error (not_found): missing",
        retry_after=None,
        retry_count=2,
        accumulated_error="attempt history -> final",
    )
    metric = Mock()

    with patch("worker.dlq_manager.DLQ_DEPTH", metric):
        failed = await move_to_dlq(db_session, job, decision, retry_history="attempt history")
        await db_session.commit()

    result = await db_session.execute(select(FailedJob).where(FailedJob.id == failed.id))
    saved = result.scalar_one()
    assert saved.original_job_id == job_id
    assert saved.user_id == user_id
    assert saved.url == job.url
    assert saved.error_category == ErrorCategory.NOT_FOUND.value
    assert saved.retry_history == "attempt history"
    assert saved.final_error == "Non-retryable error (not_found): missing"
    assert saved.final_error_category == ErrorCategory.NOT_FOUND.value
    assert saved.retry_count == 2
    assert saved.max_retries_at_failure == 3
    assert saved.title == "DLQ Video"
    assert saved.expires_at is not None
    metric.set.assert_called_once_with(1)


@pytest.mark.unit
def test_replay_all_retains_batch_original_job_lookup() -> None:
    """Replay-all keeps a single batch original-job lookup instead of per-failed-job selects."""
    source = Path("app/services/download_service.py").read_text()

    assert "DownloadJob.id.in_(original_ids)" in source
    assert "originals_by_id" in source
    assert "for failed_job in failed_jobs:" in source
    replay_all_source = source[source.index("async def replay_all_failed") :]
    assert "await self.replay_failed(" not in replay_all_source


@pytest.mark.unit
@pytest.mark.asyncio
async def test_outbox_relay_handles_retry_payload_success_and_failure(db_session) -> None:
    """Outbox relay deletes successful retry rows and retains failed retry rows as pending."""
    from worker.outbox_relay import sync_outbox_to_queue

    user_id = uuid4()
    successful_job_id = uuid4()
    failed_job_id = uuid4()
    next_retry = datetime.now(UTC) + timedelta(seconds=30)

    for job_id in (successful_job_id, failed_job_id):
        db_session.add(
            DownloadJob(
                id=job_id,
                user_id=user_id,
                url=f"https://www.youtube.com/watch?v={job_id}",
                status="pending",
            )
        )
        db_session.add(
            Outbox(
                id=uuid4(),
                job_id=job_id,
                event_type="retry_scheduled",
                payload=json.dumps({"next_retry_at": next_retry.isoformat()}),
                status="pending",
            )
        )
    await db_session.commit()

    with patch(
        "worker.outbox_relay.push_to_retry_queue",
        new_callable=AsyncMock,
        side_effect=[True, False],
    ):
        synced = await sync_outbox_to_queue(batch_size=10)

    assert synced == 1
    result = await db_session.execute(select(Outbox))
    remaining = result.scalars().all()
    assert len(remaining) == 1
    assert remaining[0].job_id == failed_job_id
    assert remaining[0].status == "pending"


@pytest.mark.unit
def test_outbox_relay_static_contracts_are_preserved() -> None:
    """Outbox relay keeps batch size, skip-locked selection, and pending-safe stale cleanup."""
    source = Path("worker/outbox_relay.py").read_text()

    assert ".limit(batch_size)" in source
    assert ".with_for_update(skip_locked=True)" in source
    assert 'entry.event_type == "retry_scheduled"' in source
    assert "push_to_download_queue" in source
    assert 'Outbox.status.in_(["enqueued", "completed"])' in source

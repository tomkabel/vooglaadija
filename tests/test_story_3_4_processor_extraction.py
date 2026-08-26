import pytest

"""Story 3.4 guardrails for processor claim and execution extraction."""

import asyncio
from pathlib import Path
from unittest.mock import AsyncMock, patch
from uuid import uuid4

import pytest
from sqlalchemy import select

from core.database import get_async_session_factory
from core.models.download_job import DownloadJob

pytestmark = pytest.mark.slow




@pytest.mark.unit
def test_worker_extraction_modules_import_directly() -> None:
    """The extracted worker modules import directly while process_next_job stays public."""
    import worker.job_claimer
    import worker.job_executor
    from worker.processor import process_next_job

    assert worker.job_claimer is not None
    assert worker.job_executor is not None
    assert callable(process_next_job)


@pytest.mark.unit
def test_processor_no_longer_contains_inline_claim_select_pattern() -> None:
    """The processor no longer owns both the pending claim update and post-claim select."""
    source = Path("worker/processor.py").read_text()

    assert 'DownloadJob.status == "pending"' not in source
    assert "select(DownloadJob).where(DownloadJob.id == job_id)" not in source


@pytest.mark.unit
def test_claim_next_uses_update_returning() -> None:
    """The claimer source uses SQLAlchemy UPDATE RETURNING for the job claim."""
    source = Path("worker/job_claimer.py").read_text()

    assert "update(DownloadJob)" in source
    assert ".returning(DownloadJob)" in source
    assert "synchronize_session=False" in source


@pytest.mark.unit
@pytest.mark.asyncio
async def test_next_job_id_normalizes_queue_bytes_and_rejects_invalid_ids() -> None:
    """The claimer normalizes queued byte IDs and rejects invalid identifiers."""
    from worker.job_claimer import next_job_id

    job_id = uuid4()
    mock_redis = AsyncMock()
    mock_redis.rpop = AsyncMock(return_value=str(job_id).encode())

    with patch("worker.job_claimer.redis_client", mock_redis):
        resolved = await next_job_id()

    assert resolved == job_id
    assert await next_job_id("not-a-uuid") is None


@pytest.mark.unit
@pytest.mark.asyncio
async def test_claim_next_returns_job_once_for_pending_row(db_session) -> None:
    """A pending job is returned by the first claim and skipped by the second claim."""
    from worker.job_claimer import claim_next

    job_id = uuid4()
    job = DownloadJob(
        id=job_id,
        user_id=uuid4(),
        url="https://www.youtube.com/watch?v=story34",
        status="pending",
    )
    db_session.add(job)
    await db_session.commit()

    session_factory = get_async_session_factory()
    async with session_factory() as first_session:
        claimed = await claim_next(first_session, job_id)

    async with session_factory() as second_session:
        claimed_again = await claim_next(second_session, job_id)

    assert claimed is not None
    assert claimed.id == job_id
    assert claimed.status == "processing"
    assert claimed_again is None


@pytest.mark.unit
@pytest.mark.asyncio
async def test_claim_next_allows_only_one_concurrent_claim(db_session) -> None:
    """Concurrent claims against the same pending job return exactly one job object."""
    from worker.job_claimer import claim_next

    job_id = uuid4()
    job = DownloadJob(
        id=job_id,
        user_id=uuid4(),
        url="https://www.youtube.com/watch?v=story34concurrent",
        status="pending",
    )
    db_session.add(job)
    await db_session.commit()

    session_factory = get_async_session_factory()

    async def try_claim() -> DownloadJob | None:
        async with session_factory() as session:
            return await claim_next(session, job_id)

    results = await asyncio.gather(try_claim(), try_claim())

    assert sum(result is not None for result in results) == 1
    assert sum(result is None for result in results) == 1


@pytest.mark.unit
@pytest.mark.asyncio
async def test_executor_completes_claimed_job_and_publishes_status(db_session) -> None:
    """The executor completes a claimed job with file metadata and status publishing."""
    from worker.job_claimer import claim_next
    from worker.job_executor import ExecutionStatus, execute

    job_id = uuid4()
    job = DownloadJob(
        id=job_id,
        user_id=uuid4(),
        url="https://www.youtube.com/watch?v=story34execute",
        status="pending",
    )
    db_session.add(job)
    await db_session.commit()

    claimed = await claim_next(db_session, job_id)
    assert claimed is not None

    mock_pubsub = AsyncMock()
    with (
        patch(
            "worker.job_executor.extract_media_with_circuit_breaker",
            new_callable=AsyncMock,
            return_value=("/storage/story34.mp4", "story34.mp4", "Story 34"),
        ),
        patch("worker.job_executor.get_risk_score", new_callable=AsyncMock, return_value=0.0),
        patch("worker.job_executor.get_pubsub_service", return_value=mock_pubsub),
    ):
        result = await execute(db_session, claimed, start_time=0.0)

    session_factory = get_async_session_factory()
    async with session_factory() as fresh_session:
        refreshed = await fresh_session.execute(select(DownloadJob).where(DownloadJob.id == job_id))
        completed = refreshed.scalar_one()

    assert result.status is ExecutionStatus.COMPLETED
    assert completed.status == "completed"
    assert completed.file_path == "/storage/story34.mp4"
    assert completed.file_name == "story34.mp4"
    assert completed.title == "Story 34"
    assert mock_pubsub.publish_job_status.await_count >= 1


@pytest.mark.unit
@pytest.mark.asyncio
async def test_processor_defers_jobs_when_executor_hits_open_circuit(db_session) -> None:
    """Circuit-breaker errors from the executor are deferred by the processor."""
    from app.services.circuit_breaker import CircuitBreakerOpenError
    from core.database import get_async_session_factory
    from worker.processor import process_next_job


    job_id = uuid4()
    job = DownloadJob(
        id=job_id,
        user_id=uuid4(),
        url="https://www.youtube.com/watch?v=story34circuit",
        status="pending",
    )
    db_session.add(job)
    await db_session.commit()

    executor_redis = AsyncMock()
    executor_redis.exists = AsyncMock(return_value=0)
    processor_redis = AsyncMock()
    processor_redis.zadd = AsyncMock(return_value=1)
    processor_redis.zcard = AsyncMock(return_value=1)

    with (
        patch("worker.job_executor.redis_client", executor_redis),
        patch("worker.processor.redis_client", processor_redis),
        patch("worker.job_executor.publish_job_status", new_callable=AsyncMock),
        patch("worker.job_executor.get_risk_score", new_callable=AsyncMock, return_value=0.0),
        patch(
            "worker.job_executor.extract_media_with_circuit_breaker",
            new_callable=AsyncMock,
            side_effect=CircuitBreakerOpenError("youtube", 30.0),
        ),
    ):
        processed = await process_next_job(job_id)

    session_factory = get_async_session_factory()
    async with session_factory() as fresh_session:
        refreshed = await fresh_session.execute(select(DownloadJob).where(DownloadJob.id == job_id))
        deferred = refreshed.scalar_one()

    assert processed is False
    assert deferred.status == "deferred"
    assert deferred.error_category == "transient"
    assert "Circuit breaker open" in (deferred.error or "")
    processor_redis.zadd.assert_awaited_once()


@pytest.mark.unit
def test_extracted_modules_keep_static_boundaries() -> None:
    """The claimer and executor avoid importing each other's out-of-scope concerns."""
    claimer_source = Path("worker/job_claimer.py").read_text()
    executor_source = Path("worker/job_executor.py").read_text()

    assert "extract_media_with_circuit_breaker" not in claimer_source
    assert "circuit_breaker" not in claimer_source
    assert "calculate_delay" not in executor_source
    assert "push_to_retry_queue" not in executor_source
    assert "FailedJob" not in executor_source

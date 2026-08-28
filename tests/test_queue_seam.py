"""Queue seam contract tests: offline app->worker payload verification.

These tests verify that what the API producer enqueues is what the worker
consumer can deserialize, using the in-memory seam instead of a live Redis
instance (Feathers: seams for network-free contract testing).
"""

from unittest.mock import patch
from uuid import UUID

import pytest

from core.interfaces.queue import JobQueue, MemoryQueue
from worker.job_claimer import normalize_job_id


@pytest.mark.unit
async def test_memory_queue_implements_job_queue_protocol():
    """The in-memory seam satisfies the JobQueue contract."""
    queue = MemoryQueue()
    assert isinstance(queue, JobQueue)
    await queue.close()


@pytest.mark.unit
async def test_payload_contract_round_trip():
    """Producer serialization (str UUID) survives the seam to the consumer."""
    queue = MemoryQueue()
    job_id = UUID("12345678-1234-5678-1234-567812345678")

    await queue.enqueue(str(job_id))

    payload = await queue.pop_next()
    assert normalize_job_id(payload) == job_id


@pytest.mark.unit
async def test_pop_order_mirrors_redis_lpush_rpop():
    """MemoryQueue preserves the FIFO order of production LPUSH/RPOP pairs."""
    queue = MemoryQueue()
    first = UUID("00000000-0000-0000-0000-000000000001")
    second = UUID("00000000-0000-0000-0000-000000000002")

    await queue.enqueue(first)
    await queue.enqueue(second)

    assert normalize_job_id(await queue.pop_next()) == first
    assert normalize_job_id(await queue.pop_next()) == second
    assert await queue.pop_next() is None


@pytest.mark.unit
async def test_production_serialization_matches_seam():
    """core.queue producers serialize exactly as the seam expects.

    Asserts the complete lpush call (queue key + payload): a regression that
    writes the job id to any other Redis queue would strand jobs without the
    worker ever seeing them.
    """
    from core.queue import enqueue_job

    job_id = UUID("12345678-1234-5678-1234-567812345678")
    recorded: list[tuple[str, str | None, str | None]] = []

    def mock_send(task_name, args=None, queue=None):
        recorded.append((task_name, args[0] if args else None, queue))

    with patch("core.queue._celery_send_task", side_effect=mock_send):
        await enqueue_job(job_id)

    assert recorded == [("worker.celery_tasks.process_download", str(job_id), "downloads")]


@pytest.mark.unit
async def test_retry_queue_deduplicates():
    """push_to_retry mirrors ZADD deduplication semantics."""
    queue = MemoryQueue()
    job_id = UUID("12345678-1234-5678-1234-567812345678")

    assert await queue.push_to_retry(job_id, 100.0) is True
    assert await queue.push_to_retry(job_id, 200.0) is False

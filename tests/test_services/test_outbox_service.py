"""Tests for outbox service."""

import uuid

import pytest
from sqlalchemy import select

from app.services.outbox_service import write_job_to_outbox
from core.models.outbox import Outbox


@pytest.mark.asyncio
async def test_write_job_to_outbox_creates_entry(db_session):
    """Test that write_job_to_outbox creates a new outbox entry."""
    job_id = uuid.uuid4()

    result = await write_job_to_outbox(
        db_session,
        job_id=job_id,
        event_type="enqueue_download",
        payload='{"test": "data"}',
    )

    assert result is not None
    assert result.job_id == job_id
    assert result.event_type == "enqueue_download"
    assert result.status == "pending"
    assert result.payload == '{"test": "data"}'

    await db_session.commit()

    saved = await db_session.execute(select(Outbox).where(Outbox.job_id == job_id))
    entry = saved.scalars().one()
    assert entry.job_id == job_id
    assert entry.status == "pending"


@pytest.mark.asyncio
async def test_write_job_to_outbox_idempotent_skips_duplicate(db_session):
    """Test that write_job_to_outbox returns None if pending entry already exists."""
    job_id = uuid.uuid4()

    first_result = await write_job_to_outbox(db_session, job_id=job_id)
    assert first_result is not None

    second_result = await write_job_to_outbox(db_session, job_id=job_id)
    assert second_result is None

    await db_session.commit()

    count_result = await db_session.execute(select(Outbox).where(Outbox.job_id == job_id))
    entries = count_result.scalars().all()
    assert len(entries) == 1


@pytest.mark.asyncio
async def test_write_job_to_outbox_allows_after_processed(db_session):
    """Test that write_job_to_outbox allows new entry after existing is processed."""
    job_id = uuid.uuid4()

    first_result = await write_job_to_outbox(db_session, job_id=job_id)
    assert first_result is not None

    await db_session.commit()

    first_result.status = "processed"
    await db_session.commit()

    second_result = await write_job_to_outbox(db_session, job_id=job_id)
    assert second_result is not None

    await db_session.commit()

    count_result = await db_session.execute(select(Outbox).where(Outbox.job_id == job_id))
    entries = count_result.scalars().all()
    assert len(entries) == 2


@pytest.mark.asyncio
async def test_write_job_to_outbox_default_event_type(db_session):
    """Test that write_job_to_outbox uses default event_type."""
    job_id = uuid.uuid4()

    result = await write_job_to_outbox(db_session, job_id=job_id)

    assert result is not None
    assert result.event_type == "enqueue_download"


@pytest.mark.asyncio
async def test_write_job_to_outbox_with_none_payload(db_session):
    """Test that write_job_to_outbox works with None payload."""
    job_id = uuid.uuid4()

    result = await write_job_to_outbox(db_session, job_id=job_id, payload=None)

    assert result is not None
    assert result.payload is None


@pytest.mark.asyncio
async def test_write_job_to_outbox_concurrent_writers_keep_one_pending_row():
    """Two sessions racing on the same job_id: the partial unique index wins.

    Covers the concurrency path that the pre-flight SELECT cannot see. Both
    writers pass their existence check, then the second flush violates
    ``uq_outbox_pending_job_id``. The savepoint in ``write_job_to_outbox`` must
    absorb that IntegrityError so:
      * exactly one pending row survives, and
      * the losing session's transaction is still usable (it can commit and
        query), rather than being poisoned by the failed flush.
    """
    from app.services import outbox_service
    from tests.conftest import TestingSessionLocal

    job_id = uuid.uuid4()

    async with TestingSessionLocal() as session_a, TestingSessionLocal() as session_b:
        # session_a wins the race and commits its pending row.
        first = await write_job_to_outbox(session_a, job_id=job_id)
        assert first is not None
        await session_a.commit()

        # Reproduce the race the pre-flight SELECT cannot see: force session_b's
        # "no pending row yet" check to miss (e.g. because a concurrent writer
        # committed in the gap), so its insert hits the partial unique index.
        real_execute = session_b.execute
        calls = {"n": 0}

        async def _intercept_execute(statement, *args, **kwargs):
            calls["n"] += 1
            if calls["n"] == 1:
                # First call is the existence check: pretend it found nothing.
                class _Stub:
                    def scalars(self):
                        class _S:
                            def one_or_none(self):
                                return None

                        return _S()

                return _Stub()
            return await real_execute(statement, *args, **kwargs)

        session_b.execute = _intercept_execute  # type: ignore[assignment]

        # session_b's flush now trips the index; the savepoint must absorb it.
        second = await write_job_to_outbox(session_b, job_id=job_id)
        assert second is None, "the losing writer must report an idempotent no-op"

        # The enclosing transaction survived the absorbed IntegrityError.
        await session_b.commit()

        remaining = await session_b.execute(
            select(Outbox).where(Outbox.job_id == job_id, Outbox.status == "pending"),
        )
        assert len(remaining.scalars().all()) == 1

        # Both sessions remain usable afterwards.
        for session in (session_a, session_b):
            probe = await session.execute(select(Outbox).where(Outbox.job_id == job_id))
            assert len(probe.scalars().all()) == 1

    assert outbox_service is not None  # keep import referenced

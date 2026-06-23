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

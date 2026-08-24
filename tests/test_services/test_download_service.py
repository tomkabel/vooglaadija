"""Direct DownloadService behavior tests."""

import os
import uuid
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, Mock, patch

import pytest
from sqlalchemy import event, select

from core.models.download_job import DownloadJob
from core.models.failed_job import FailedJob
from core.models.outbox import Outbox
from core.models.user import User
from tests.conftest import test_engine


async def _user(db_session, email: str = "download-service@example.com") -> User:
    user = User(id=uuid.uuid4(), email=email, password_hash="hash")
    db_session.add(user)
    await db_session.commit()
    return user


def _job(user_id: uuid.UUID, **overrides) -> DownloadJob:
    values = {
        "id": uuid.uuid4(),
        "user_id": user_id,
        "url": "https://www.youtube.com/watch?v=dQw4w9WgXcQ",
        "status": "pending",
    }
    values.update(overrides)
    return DownloadJob(**values)


def _failed_job(user_id: uuid.UUID, **overrides) -> FailedJob:
    values = {
        "id": uuid.uuid4(),
        "original_job_id": None,
        "user_id": user_id,
        "url": "https://www.youtube.com/watch?v=failedjob",
        "error_category": "transient",
        "retry_history": "attempt failed",
        "final_error": "final failure",
        "final_error_category": "transient",
        "retry_count": 2,
        "max_retries_at_failure": 3,
        "title": "Failed Video",
        "failed_at": datetime.now(UTC),
    }
    values.update(overrides)
    return FailedJob(**values)


@pytest.mark.asyncio
async def test_create_writes_job_and_pending_outbox(db_session, sample_url):
    """Creating a download writes a user-owned pending job and outbox row."""
    from app.services.download_service import DownloadService

    user = await _user(db_session)
    service = DownloadService(db_session, user.id)

    with patch(
        "app.services.download_service.resolve_video_title", new_callable=AsyncMock
    ) as resolve_title:
        resolve_title.return_value = "Resolved Title"
        job = await service.create(sample_url)

    assert job.user_id == user.id
    assert job.url == sample_url
    assert job.status == "pending"
    assert job.title == "Resolved Title"

    outbox_result = await db_session.execute(select(Outbox).where(Outbox.job_id == job.id))
    assert outbox_result.scalar_one().status == "pending"


@pytest.mark.asyncio
async def test_best_effort_enqueue_marks_pending_outbox_processed_on_success(db_session):
    """Best-effort enqueue transitions the pending outbox row to 'processed' after queue success."""
    from app.services.download_service import DownloadService

    user = await _user(db_session)
    job = _job(user.id)
    outbox = Outbox(
        id=uuid.uuid4(),
        job_id=job.id,
        event_type="enqueue_download",
        status="pending",
    )
    db_session.add_all([job, outbox])
    await db_session.commit()

    service = DownloadService(db_session, user.id)
    with patch("app.services.download_service.enqueue_job", new_callable=AsyncMock) as enqueue:
        await service.best_effort_enqueue(job.id)

    enqueue.assert_awaited_once_with(job.id)
    outbox_result = await db_session.execute(select(Outbox).where(Outbox.job_id == job.id))
    processed = outbox_result.scalar_one()
    assert processed.status == "processed"
    assert processed.processed_at is not None


@pytest.mark.asyncio
async def test_best_effort_enqueue_keeps_pending_outbox_on_queue_failure(db_session):
    """Best-effort enqueue leaves pending outbox recovery rows when queueing fails."""
    from app.services.download_service import DownloadService

    user = await _user(db_session)
    job = _job(user.id)
    outbox = Outbox(
        id=uuid.uuid4(),
        job_id=job.id,
        event_type="enqueue_download",
        status="pending",
    )
    db_session.add_all([job, outbox])
    await db_session.commit()

    service = DownloadService(db_session, user.id)
    with patch(
        "app.services.download_service.enqueue_job",
        new_callable=AsyncMock,
        side_effect=RuntimeError("redis unavailable"),
    ) as enqueue:
        await service.best_effort_enqueue(job.id)

    enqueue.assert_awaited_once_with(job.id)
    outbox_result = await db_session.execute(select(Outbox).where(Outbox.job_id == job.id))
    assert outbox_result.scalar_one().status == "pending"


@pytest.mark.asyncio
async def test_best_effort_enqueue_rolls_back_failed_outbox_cleanup():
    """Best-effort enqueue rolls back when cleanup fails after queue success."""
    from app.services.download_service import DownloadService

    class FailingCleanupSession:
        def __init__(self) -> None:
            self.rolled_back = False
            self._execute_calls = 0

        async def execute(self, _statement):
            self._execute_calls += 1
            if self._execute_calls == 1:
                # First call: snapshot SELECT — return a sentinel.
                class _Result:
                    def scalar_one_or_none(self):
                        return uuid.uuid4()

                return _Result()
            raise RuntimeError("update failed")

        async def commit(self):
            raise AssertionError("commit should not run when cleanup fails")

        async def rollback(self):
            self.rolled_back = True

    db = FailingCleanupSession()
    service = DownloadService(db, uuid.uuid4())
    job_id = uuid.uuid4()

    with patch("app.services.download_service.enqueue_job", new_callable=AsyncMock) as enqueue:
        await service.best_effort_enqueue(job_id)

    enqueue.assert_awaited_once_with(job_id)
    assert db.rolled_back is True


@pytest.mark.asyncio
async def test_list_and_get_enforce_user_isolation(db_session):
    """Listing and getting downloads only return jobs for the service user."""
    from app.services.download_service import DownloadNotFoundError, DownloadService

    user = await _user(db_session, "owner@example.com")
    other = await _user(db_session, "other@example.com")
    owned = _job(user.id, url="https://www.youtube.com/watch?v=owned")
    hidden = _job(other.id, url="https://www.youtube.com/watch?v=hidden")
    db_session.add_all([owned, hidden])
    await db_session.commit()

    service = DownloadService(db_session, user.id)
    page = await service.list(page=1, per_page=20)

    assert page.total == 1
    assert [job.id for job in page.jobs] == [owned.id]
    assert (await service.get(str(owned.id))).id == owned.id
    with pytest.raises(DownloadNotFoundError):
        await service.get(str(hidden.id))


@pytest.mark.asyncio
async def test_retry_resets_failed_or_deferred_only(db_session):
    """Retry resets failed jobs and rejects non-retryable statuses."""
    from app.services.download_service import DownloadService, InvalidDownloadStatusError

    user = await _user(db_session)
    failed = _job(
        user.id,
        status="failed",
        retry_count=2,
        error="failed",
        error_category="transient",
        completed_at=datetime.now(UTC),
    )
    pending = _job(user.id, status="pending")
    db_session.add_all([failed, pending])
    await db_session.commit()

    service = DownloadService(db_session, user.id)
    retried = await service.retry(failed.id)

    assert retried.status == "pending"
    assert retried.retry_count == 0
    assert retried.error is None
    assert retried.error_category is None
    outbox_result = await db_session.execute(select(Outbox).where(Outbox.job_id == failed.id))
    assert outbox_result.scalar_one().status == "pending"
    with pytest.raises(InvalidDownloadStatusError):
        await service.retry(pending.id)


@pytest.mark.asyncio
async def test_resolve_errors_paginates_filters_and_enforces_user_isolation(db_session):
    """Failed-job listing paginates category-filtered rows for only the service user."""
    from app.services.download_service import DownloadService

    user = await _user(db_session, "failed-list@example.com")
    other = await _user(db_session, "failed-list-other@example.com")
    newest = _failed_job(user.id, error_category="transient")
    older = _failed_job(
        user.id,
        error_category="transient",
        failed_at=datetime.now(UTC) - timedelta(minutes=5),
    )
    ignored_category = _failed_job(user.id, error_category="permanent")
    ignored_user = _failed_job(other.id, error_category="transient")
    db_session.add_all([newest, older, ignored_category, ignored_user])
    await db_session.commit()

    page = await DownloadService(db_session, user.id).resolve_errors(
        page=1,
        per_page=1,
        category="transient",
    )

    assert page.total == 2
    assert page.page == 1
    assert page.per_page == 1
    assert [failed.id for failed in page.failed_jobs] == [newest.id]


@pytest.mark.asyncio
async def test_get_file_path_validates_expired_missing_and_unsafe_paths(db_session, tmp_path):
    """File resolution validates status, expiry, path containment, and disk existence."""
    from app.services.download_service import (
        DownloadFileExpiredError,
        DownloadFileMissingError,
        DownloadService,
        UnsafeDownloadPathError,
    )

    user = await _user(db_session)
    downloads_dir = tmp_path / "downloads"
    downloads_dir.mkdir()
    video = downloads_dir / "video.mp4"
    video.write_text("video")
    service = DownloadService(db_session, user.id)

    with patch("app.services.download_service.settings") as mock_settings:
        mock_settings.storage_path = str(tmp_path)
        valid = _job(
            user.id,
            status="completed",
            file_path=str(video),
            file_name="video.mp4",
            expires_at=datetime.now(UTC) + timedelta(hours=1),
        )
        expired = _job(
            user.id,
            status="completed",
            file_path=str(video),
            expires_at=datetime.now(UTC) - timedelta(hours=1),
        )
        unsafe = _job(user.id, status="completed", file_path="/etc/passwd")
        missing = _job(
            user.id,
            status="completed",
            file_path=str(downloads_dir / "missing.mp4"),
        )
        db_session.add_all([valid, expired, unsafe, missing])
        await db_session.commit()

        result = await service.get_file_path(valid.id)
        assert result.path == os.path.realpath(video)
        assert result.filename == "video.mp4"
        with pytest.raises(DownloadFileExpiredError):
            await service.get_file_path(expired.id)
        with pytest.raises(UnsafeDownloadPathError):
            await service.get_file_path(unsafe.id)
        with pytest.raises(DownloadFileMissingError):
            await service.get_file_path(missing.id)


@pytest.mark.asyncio
async def test_delete_supports_rest_and_web_file_policies(db_session, tmp_path):
    """Delete supports route-specific status restrictions and file-delete failure policy."""
    from app.services.download_service import DownloadFileDeleteFailedError, DownloadService

    user = await _user(db_session)
    downloads_dir = tmp_path / "downloads"
    downloads_dir.mkdir()
    rest_file = downloads_dir / "rest.mp4"
    web_file = downloads_dir / "web.mp4"
    rest_file.write_text("rest")
    web_file.write_text("web")
    rest_job = _job(user.id, status="pending", file_path=str(rest_file))
    web_job = _job(user.id, status="completed", file_path=str(web_file))
    db_session.add_all([rest_job, web_job])
    await db_session.commit()

    service = DownloadService(db_session, user.id)
    with patch("app.services.download_service.settings") as mock_settings:
        mock_settings.storage_path = str(tmp_path)
        with patch("app.services.download_service.os.remove", side_effect=OSError("denied")):
            with pytest.raises(DownloadFileDeleteFailedError):
                await service.delete(rest_job.id, fail_on_file_delete=True)
            await service.delete(
                web_job.id,
                allowed_statuses={"completed", "failed", "cancelled"},
                fail_on_file_delete=False,
            )

    result = await db_session.execute(select(DownloadJob).where(DownloadJob.id == web_job.id))
    assert result.scalar_one_or_none() is None


@pytest.mark.asyncio
async def test_bulk_delete_removes_deletable_and_skips_blocked(db_session):
    """Bulk delete removes completed jobs and skips in-progress/unknown ids."""
    from app.services.download_service import DownloadService

    user = await _user(db_session)
    done = _job(user.id, status="completed")
    failed = _job(user.id, status="failed")
    in_progress = _job(user.id, status="processing")
    db_session.add_all([done, failed, in_progress])
    await db_session.commit()

    unknown_id = str(uuid.uuid4())
    service = DownloadService(db_session, user.id)
    result = await service.bulk_delete(
        [str(done.id), str(failed.id), str(in_progress.id), unknown_id],
        allowed_statuses={"completed", "failed", "cancelled"},
        fail_on_file_delete=False,
    )

    assert set(result.deleted_ids) == {str(done.id), str(failed.id)}
    assert str(in_progress.id) in result.skipped_ids
    assert unknown_id in result.skipped_ids
    assert result.requested == 4

    remaining = await db_session.execute(select(DownloadJob).where(DownloadJob.user_id == user.id))
    remaining_ids = {str(job.id) for job in remaining.scalars().all()}
    assert str(done.id) not in remaining_ids
    assert str(failed.id) not in remaining_ids
    assert str(in_progress.id) in remaining_ids


@pytest.mark.asyncio
async def test_single_dlq_replay_handles_original_and_orphan_rows(db_session):
    """Single DLQ replay resets originals and creates jobs for orphan rows."""
    from app.services.download_service import DownloadService

    user = await _user(db_session)
    original = _job(user.id, status="failed", retry_count=2, error="failed")
    original_failed = _failed_job(user.id, original_job_id=original.id)
    orphan_failed = _failed_job(user.id, url="https://www.youtube.com/watch?v=orphan")
    db_session.add_all([original, original_failed, orphan_failed])
    await db_session.commit()

    service = DownloadService(db_session, user.id)
    with patch("core.metrics.DLQ_DEPTH", Mock()):
        replayed_original = await service.replay_failed(original_failed.id)
        replayed_orphan = await service.replay_failed(orphan_failed.id)

    assert replayed_original.id == original.id
    assert replayed_original.status == "pending"
    assert replayed_orphan.url == "https://www.youtube.com/watch?v=orphan"

    failed_result = await db_session.execute(select(FailedJob))
    assert failed_result.scalars().all() == []


@pytest.mark.asyncio
async def test_replay_all_batches_original_lookup_and_preserves_filter(db_session):
    """Replay-all uses one original lookup and preserves user/category filtering."""
    from app.services.download_service import DownloadService

    user = await _user(db_session, "batch@example.com")
    other = await _user(db_session, "batch-other@example.com")
    original_one = _job(user.id, status="failed", retry_count=2, error="one")
    original_two = _job(user.id, status="failed", retry_count=3, error="two")
    db_session.add_all([original_one, original_two])
    db_session.add_all(
        [
            _failed_job(user.id, original_job_id=original_one.id, error_category="transient"),
            _failed_job(user.id, original_job_id=original_two.id, error_category="transient"),
            _failed_job(
                user.id,
                url="https://www.youtube.com/watch?v=new",
                error_category="transient",
            ),
            _failed_job(user.id, error_category="timeout"),
            _failed_job(other.id, error_category="transient"),
        ]
    )
    await db_session.commit()

    download_job_selects = 0

    def count_download_job_selects(_conn, _cursor, statement, _parameters, _context, _executemany):
        nonlocal download_job_selects
        normalized = " ".join(statement.lower().split())
        if normalized.startswith("select") and " from download_jobs " in normalized:
            download_job_selects += 1

    service = DownloadService(db_session, user.id)
    event.listen(test_engine.sync_engine, "before_cursor_execute", count_download_job_selects)
    try:
        result = await service.replay_all_failed(category="transient")
    finally:
        event.remove(test_engine.sync_engine, "before_cursor_execute", count_download_job_selects)

    assert result.replayed == 3
    assert result.total == 3
    assert download_job_selects == 1


@pytest.mark.asyncio
async def test_replay_all_respects_max_batch_limit_for_orphans(db_session):
    """Replay-all processes only the bounded batch and leaves extra DLQ rows queued."""
    from app.services.download_service import DownloadService

    user = await _user(db_session, "batch-limit@example.com")
    failed_jobs = [
        _failed_job(
            user.id,
            url=f"https://www.youtube.com/watch?v=batch-limit-{index}",
            error_category="transient",
        )
        for index in range(3)
    ]
    db_session.add_all(failed_jobs)
    await db_session.commit()

    download_job_selects = 0

    def count_download_job_selects(_conn, _cursor, statement, _parameters, _context, _executemany):
        nonlocal download_job_selects
        normalized = " ".join(statement.lower().split())
        if normalized.startswith("select") and " from download_jobs " in normalized:
            download_job_selects += 1

    service = DownloadService(db_session, user.id)
    event.listen(test_engine.sync_engine, "before_cursor_execute", count_download_job_selects)
    try:
        result = await service.replay_all_failed(category="transient", max_batch=2)
    finally:
        event.remove(test_engine.sync_engine, "before_cursor_execute", count_download_job_selects)

    assert result.replayed == 2
    assert result.total == 2
    assert download_job_selects == 0

    remaining_failed_result = await db_session.execute(select(FailedJob))
    assert len(remaining_failed_result.scalars().all()) == 1

    created_jobs_result = await db_session.execute(
        select(DownloadJob).where(DownloadJob.user_id == user.id)
    )
    created_jobs = created_jobs_result.scalars().all()
    assert len(created_jobs) == 2
    assert {job.status for job in created_jobs} == {"pending"}

    outbox_result = await db_session.execute(select(Outbox))
    assert len(outbox_result.scalars().all()) == 2


@pytest.mark.asyncio
async def test_replay_all_treats_cross_user_original_as_orphan(db_session):
    """Replay-all must not mutate another user's job from malformed DLQ data."""
    from app.services.download_service import DownloadService

    user = await _user(db_session, "malformed-dlq@example.com")
    other = await _user(db_session, "malformed-dlq-other@example.com")
    other_original = _job(
        other.id,
        status="failed",
        retry_count=4,
        error="other user failure",
        error_category="transient",
        completed_at=datetime.now(UTC),
    )
    malformed_failed = _failed_job(
        user.id,
        original_job_id=other_original.id,
        url="https://www.youtube.com/watch?v=cross-user-orphan",
        error_category="transient",
    )
    db_session.add_all([other_original, malformed_failed])
    await db_session.commit()

    download_job_selects = 0

    def count_download_job_selects(_conn, _cursor, statement, _parameters, _context, _executemany):
        nonlocal download_job_selects
        normalized = " ".join(statement.lower().split())
        if normalized.startswith("select") and " from download_jobs " in normalized:
            download_job_selects += 1

    service = DownloadService(db_session, user.id)
    event.listen(test_engine.sync_engine, "before_cursor_execute", count_download_job_selects)
    try:
        result = await service.replay_all_failed(category="transient")
    finally:
        event.remove(test_engine.sync_engine, "before_cursor_execute", count_download_job_selects)

    assert result.replayed == 1
    assert result.total == 1
    assert download_job_selects == 1

    await db_session.refresh(other_original)
    assert other_original.status == "failed"
    assert other_original.retry_count == 4
    assert other_original.error == "other user failure"
    assert other_original.completed_at is not None

    created_result = await db_session.execute(
        select(DownloadJob).where(
            DownloadJob.user_id == user.id,
            DownloadJob.url == "https://www.youtube.com/watch?v=cross-user-orphan",
        )
    )
    created_job = created_result.scalar_one()
    assert created_job.status == "pending"

    failed_result = await db_session.execute(
        select(FailedJob).where(FailedJob.id == malformed_failed.id)
    )
    assert failed_result.scalar_one_or_none() is None

    outbox_result = await db_session.execute(select(Outbox).where(Outbox.job_id == created_job.id))
    assert outbox_result.scalar_one().status == "pending"

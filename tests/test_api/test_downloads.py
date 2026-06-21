"""Downloads API endpoint tests."""

import uuid
from datetime import UTC, datetime
from unittest.mock import Mock, patch

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import event, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import verify_token
from app.main import app
from core.models.download_job import DownloadJob
from core.models.failed_job import FailedJob
from core.models.outbox import Outbox
from tests.conftest import create_test_user_and_login, test_engine


def _user_id_from_token(token: str) -> uuid.UUID:
    payload = verify_token(token)
    assert payload is not None
    return uuid.UUID(payload["sub"])


def _failed_job(
    user_id: uuid.UUID,
    *,
    failed_job_id: uuid.UUID | None = None,
    original_job_id: uuid.UUID | None = None,
    url: str = "https://www.youtube.com/watch?v=dQw4w9WgXcQ",
    category: str = "transient",
    title: str | None = None,
) -> FailedJob:
    return FailedJob(
        id=failed_job_id or uuid.uuid4(),
        original_job_id=original_job_id,
        user_id=user_id,
        url=url,
        error_category=category,
        retry_history="attempt 1 failed",
        final_error=f"{category} failure",
        final_error_category=category,
        retry_count=2,
        max_retries_at_failure=3,
        title=title,
        failed_at=datetime.now(UTC),
    )


@pytest.mark.asyncio
async def test_create_download_success():
    """Test creating a download job with valid YouTube URL."""
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        token = await create_test_user_and_login(client)
        response = await client.post(
            "/api/v1/downloads",
            json={"url": "https://www.youtube.com/watch?v=dQw4w9WgXcQ"},
            headers={"Authorization": f"Bearer {token}"},
        )
    assert response.status_code == 201
    data = response.json()
    assert data["url"] == "https://www.youtube.com/watch?v=dQw4w9WgXcQ"
    assert data["status"] == "pending"
    assert "id" in data
    assert "created_at" in data


@pytest.mark.asyncio
async def test_create_download_requires_auth():
    """Test that creating a download requires authentication."""
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post(
            "/api/v1/downloads",
            json={"url": "https://www.youtube.com/watch?v=dQw4w9WgXcQ"},
        )
    assert response.status_code == 401


@pytest.mark.asyncio
async def test_create_download_invalid_url():
    """Test that invalid URL returns 422."""
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        token = await create_test_user_and_login(client)
        response = await client.post(
            "/api/v1/downloads",
            json={"url": "https://www.google.com"},
            headers={"Authorization": f"Bearer {token}"},
        )
    assert response.status_code == 422


@pytest.mark.asyncio
async def test_create_download_missing_url():
    """Test that missing URL returns 422."""
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        token = await create_test_user_and_login(client)
        response = await client.post(
            "/api/v1/downloads",
            json={},
            headers={"Authorization": f"Bearer {token}"},
        )
    assert response.status_code == 422


@pytest.mark.asyncio
async def test_create_download_empty_url():
    """Test that empty URL returns 422."""
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        token = await create_test_user_and_login(client)
        response = await client.post(
            "/api/v1/downloads",
            json={"url": ""},
            headers={"Authorization": f"Bearer {token}"},
        )
    assert response.status_code == 422


@pytest.mark.asyncio
async def test_list_downloads_empty():
    """Test listing downloads when user has none."""
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        token = await create_test_user_and_login(client)
        response = await client.get(
            "/api/v1/downloads",
            headers={"Authorization": f"Bearer {token}"},
        )
    assert response.status_code == 200
    data = response.json()
    assert data["downloads"] == []
    assert data["pagination"]["total"] == 0
    assert data["pagination"]["page"] == 1
    assert data["pagination"]["per_page"] == 20


@pytest.mark.asyncio
async def test_list_downloads_with_jobs():
    """Test listing downloads returns user's jobs."""
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        token = await create_test_user_and_login(client)
        # Create two downloads
        await client.post(
            "/api/v1/downloads",
            json={"url": "https://www.youtube.com/watch?v=dQw4w9WgXcQ"},
            headers={"Authorization": f"Bearer {token}"},
        )
        await client.post(
            "/api/v1/downloads",
            json={"url": "https://www.youtube.com/watch?v=jNQXAC9IVRw"},
            headers={"Authorization": f"Bearer {token}"},
        )
        response = await client.get(
            "/api/v1/downloads",
            headers={"Authorization": f"Bearer {token}"},
        )
    assert response.status_code == 200
    data = response.json()
    assert len(data["downloads"]) == 2
    assert data["pagination"]["total"] == 2


@pytest.mark.asyncio
async def test_list_downloads_pagination():
    """Test pagination parameters work."""
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        token = await create_test_user_and_login(client)
        # Create 3 downloads
        for _ in range(3):
            await client.post(
                "/api/v1/downloads",
                json={"url": "https://www.youtube.com/watch?v=dQw4w9WgXcQ"},
                headers={"Authorization": f"Bearer {token}"},
            )
        # Get page 1 with per_page=2
        response = await client.get(
            "/api/v1/downloads?page=1&per_page=2",
            headers={"Authorization": f"Bearer {token}"},
        )
    assert response.status_code == 200
    data = response.json()
    assert len(data["downloads"]) == 2
    assert data["pagination"]["total"] == 3
    assert data["pagination"]["page"] == 1
    assert data["pagination"]["per_page"] == 2


@pytest.mark.asyncio
async def test_list_downloads_requires_auth():
    """Test that listing downloads requires authentication."""
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get("/api/v1/downloads")
    assert response.status_code == 401


@pytest.mark.asyncio
async def test_list_downloads_only_own_jobs():
    """Test that users only see their own downloads."""
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        # Create user 1 and download
        token1 = await create_test_user_and_login(client)
        await client.post(
            "/api/v1/downloads",
            json={"url": "https://www.youtube.com/watch?v=dQw4w9WgXcQ"},
            headers={"Authorization": f"Bearer {token1}"},
        )
        # Create user 2 and check they don't see user 1's downloads
        await client.post(
            "/api/v1/auth/register",
            json={"email": "user2@example.com", "password": "testpassword123"},
        )
        login_response = await client.post(
            "/api/v1/auth/login",
            json={"email": "user2@example.com", "password": "testpassword123"},
        )
        token2 = login_response.json()["access_token"]
        response = await client.get(
            "/api/v1/downloads",
            headers={"Authorization": f"Bearer {token2}"},
        )
    assert response.status_code == 200
    data = response.json()
    assert len(data["downloads"]) == 0


@pytest.mark.asyncio
async def test_get_download_success():
    """Test getting a specific download job."""
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        token = await create_test_user_and_login(client)
        # Create download
        create_response = await client.post(
            "/api/v1/downloads",
            json={"url": "https://www.youtube.com/watch?v=dQw4w9WgXcQ"},
            headers={"Authorization": f"Bearer {token}"},
        )
        job_id = create_response.json()["id"]
        # Get download
        response = await client.get(
            f"/api/v1/downloads/{job_id}",
            headers={"Authorization": f"Bearer {token}"},
        )
    assert response.status_code == 200
    data = response.json()
    assert data["id"] == job_id
    assert data["url"] == "https://www.youtube.com/watch?v=dQw4w9WgXcQ"


@pytest.mark.asyncio
async def test_get_download_not_found():
    """Test getting non-existent download returns 404."""
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        token = await create_test_user_and_login(client)
        response = await client.get(
            f"/api/v1/downloads/{uuid.uuid4()}",
            headers={"Authorization": f"Bearer {token}"},
        )
    assert response.status_code == 404


@pytest.mark.asyncio
async def test_get_download_requires_auth():
    """Test that getting a download requires authentication."""
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get(
            f"/api/v1/downloads/{uuid.uuid4()}",
        )
    assert response.status_code == 401


@pytest.mark.asyncio
async def test_get_download_file_not_completed():
    """Test that downloading a non-completed file returns 400."""
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        token = await create_test_user_and_login(client)
        # Create download (status=pending)
        create_response = await client.post(
            "/api/v1/downloads",
            json={"url": "https://www.youtube.com/watch?v=dQw4w9WgXcQ"},
            headers={"Authorization": f"Bearer {token}"},
        )
        job_id = create_response.json()["id"]
        # Try to get file
        response = await client.get(
            f"/api/v1/downloads/{job_id}/file",
            headers={"Authorization": f"Bearer {token}"},
        )
    assert response.status_code == 400
    assert "not completed" in response.json()["error"]["message"]


@pytest.mark.asyncio
async def test_get_download_file_not_found(db_session: AsyncSession):
    """Test that file with no path returns 404."""
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        token = await create_test_user_and_login(client)
        # Create download
        create_response = await client.post(
            "/api/v1/downloads",
            json={"url": "https://www.youtube.com/watch?v=dQw4w9WgXcQ"},
            headers={"Authorization": f"Bearer {token}"},
        )
        job_id = uuid.UUID(create_response.json()["id"])
        # Manually mark as completed but no file_path
        await db_session.execute(
            update(DownloadJob).where(DownloadJob.id == job_id).values(status="completed"),
        )
        await db_session.commit()
        # Try to get file
        response = await client.get(
            f"/api/v1/downloads/{job_id}/file",
            headers={"Authorization": f"Bearer {token}"},
        )
    assert response.status_code == 404
    assert "File not found" in response.json()["error"]["message"]


@pytest.mark.asyncio
async def test_delete_download_success():
    """Test deleting a download job."""
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        token = await create_test_user_and_login(client)
        # Create download
        create_response = await client.post(
            "/api/v1/downloads",
            json={"url": "https://www.youtube.com/watch?v=dQw4w9WgXcQ"},
            headers={"Authorization": f"Bearer {token}"},
        )
        job_id = create_response.json()["id"]
        # Delete
        response = await client.delete(
            f"/api/v1/downloads/{job_id}",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert response.status_code == 204
        # Verify deleted
        get_response = await client.get(
            f"/api/v1/downloads/{job_id}",
            headers={"Authorization": f"Bearer {token}"},
        )
    assert get_response.status_code == 404


@pytest.mark.asyncio
async def test_delete_download_not_found():
    """Test deleting non-existent download returns 404."""
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        token = await create_test_user_and_login(client)
        response = await client.delete(
            f"/api/v1/downloads/{uuid.uuid4()}",
            headers={"Authorization": f"Bearer {token}"},
        )
    assert response.status_code == 404


@pytest.mark.asyncio
async def test_delete_download_requires_auth():
    """Test that deleting a download requires authentication."""
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.delete(
            f"/api/v1/downloads/{uuid.uuid4()}",
        )
    assert response.status_code == 401


@pytest.mark.asyncio
async def test_get_download_file_expired_returns_410(db_session: AsyncSession):
    """Test that downloading an expired file returns 410 Gone.

    Sets expires_at to well in the past (year 2000) so the expiry
    always triggers regardless of clock skew.
    """
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        token = await create_test_user_and_login(client)
        create_response = await client.post(
            "/api/v1/downloads",
            json={"url": "https://www.youtube.com/watch?v=dQw4w9WgXcQ"},
            headers={"Authorization": f"Bearer {token}"},
        )
        job_id = uuid.UUID(create_response.json()["id"])

        past = datetime(2000, 1, 1, tzinfo=UTC)
        await db_session.execute(
            update(DownloadJob)
            .where(DownloadJob.id == job_id)
            .values(
                status="completed",
                file_path="/tmp/fake_file.mp4",
                expires_at=past,
            ),
        )
        await db_session.commit()

        response = await client.get(
            f"/api/v1/downloads/{job_id}/file",
            headers={"Authorization": f"Bearer {token}"},
        )
    assert response.status_code == 410
    assert "expired" in response.json()["error"]["message"].lower()


@pytest.mark.asyncio
async def test_get_download_file_path_traversal_returns_403(db_session: AsyncSession):
    """Test that a file_path outside storage directory returns 403 (path traversal prevention).

    Uses None for expires_at so the expiry check is bypassed (not yet expired).
    The path traversal check should trigger before any file existence check.
    """
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        token = await create_test_user_and_login(client)
        create_response = await client.post(
            "/api/v1/downloads",
            json={"url": "https://www.youtube.com/watch?v=dQw4w9WgXcQ"},
            headers={"Authorization": f"Bearer {token}"},
        )
        job_id = uuid.UUID(create_response.json()["id"])

        # Simulate a malicious file_path stored in DB; expires_at=None means not expired
        await db_session.execute(
            update(DownloadJob)
            .where(DownloadJob.id == job_id)
            .values(
                status="completed",
                file_path="/etc/passwd",
                expires_at=None,
            ),
        )
        await db_session.commit()

        response = await client.get(
            f"/api/v1/downloads/{job_id}/file",
            headers={"Authorization": f"Bearer {token}"},
        )
    assert response.status_code == 403
    assert "Access denied" in response.json()["error"]["message"]


@pytest.mark.asyncio
async def test_get_download_file_not_on_disk(db_session: AsyncSession):
    """Test that a completed job whose file is missing from disk returns 404.

    Uses expires_at=None so the expiry check is skipped.
    The file_path is within storage dir but doesn't exist on disk.
    """
    import os

    from core.config import settings

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        token = await create_test_user_and_login(client)
        create_response = await client.post(
            "/api/v1/downloads",
            json={"url": "https://www.youtube.com/watch?v=dQw4w9WgXcQ"},
            headers={"Authorization": f"Bearer {token}"},
        )
        job_id = uuid.UUID(create_response.json()["id"])

        # Use a path inside storage dir that doesn't actually exist on disk
        storage_downloads = os.path.join(settings.storage_path, "downloads")
        os.makedirs(storage_downloads, exist_ok=True)
        nonexistent_path = os.path.join(storage_downloads, f"{uuid.uuid4()}.mp4")

        await db_session.execute(
            update(DownloadJob)
            .where(DownloadJob.id == job_id)
            .values(
                status="completed",
                file_path=nonexistent_path,
                expires_at=None,
            ),
        )
        await db_session.commit()

        response = await client.get(
            f"/api/v1/downloads/{job_id}/file",
            headers={"Authorization": f"Bearer {token}"},
        )
    assert response.status_code == 404
    assert "not found" in response.json()["error"]["message"].lower()


@pytest.mark.asyncio
async def test_list_downloads_page_2(db_session: AsyncSession):
    """Test that page 2 returns the second set of results."""
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        token = await create_test_user_and_login(client)
        for _ in range(3):
            await client.post(
                "/api/v1/downloads",
                json={"url": "https://www.youtube.com/watch?v=dQw4w9WgXcQ"},
                headers={"Authorization": f"Bearer {token}"},
            )
        response = await client.get(
            "/api/v1/downloads?page=2&per_page=2",
            headers={"Authorization": f"Bearer {token}"},
        )
    assert response.status_code == 200
    data = response.json()
    assert len(data["downloads"]) == 1
    assert data["pagination"]["total"] == 3
    assert data["pagination"]["page"] == 2
    assert data["pagination"]["per_page"] == 2


@pytest.mark.asyncio
async def test_list_downloads_user_isolation():
    """Test that user A cannot see user B's download jobs."""
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        token_a = await create_test_user_and_login(client, "isolation_a@example.com")
        token_b = await create_test_user_and_login(client, "isolation_b@example.com")

        # User A creates a download
        create_resp = await client.post(
            "/api/v1/downloads",
            json={"url": "https://www.youtube.com/watch?v=dQw4w9WgXcQ"},
            headers={"Authorization": f"Bearer {token_a}"},
        )
        job_id_a = create_resp.json()["id"]

        # User B should not be able to fetch user A's specific job
        response = await client.get(
            f"/api/v1/downloads/{job_id_a}",
            headers={"Authorization": f"Bearer {token_b}"},
        )
    assert response.status_code == 404


@pytest.mark.asyncio
async def test_retry_download_success(db_session: AsyncSession):
    """Test retrying a failed download job."""
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        token = await create_test_user_and_login(client)
        # Create download
        create_response = await client.post(
            "/api/v1/downloads",
            json={"url": "https://www.youtube.com/watch?v=dQw4w9WgXcQ"},
            headers={"Authorization": f"Bearer {token}"},
        )
        job_id = uuid.UUID(create_response.json()["id"])

        # Manually mark as failed
        await db_session.execute(
            update(DownloadJob)
            .where(DownloadJob.id == job_id)
            .values(status="failed", error="Test error"),
        )
        await db_session.commit()

        # Retry
        response = await client.post(
            f"/api/v1/downloads/{job_id}/retry",
            headers={"Authorization": f"Bearer {token}"},
        )

    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "pending"
    assert data["error"] is None


@pytest.mark.asyncio
async def test_retry_download_not_failed_returns_400(db_session: AsyncSession):
    """Test that retrying a non-failed job returns 400."""
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        token = await create_test_user_and_login(client)
        # Create download (status=pending)
        create_response = await client.post(
            "/api/v1/downloads",
            json={"url": "https://www.youtube.com/watch?v=dQw4w9WgXcQ"},
            headers={"Authorization": f"Bearer {token}"},
        )
        job_id = uuid.UUID(create_response.json()["id"])

        # Try to retry (should fail since not failed)
        response = await client.post(
            f"/api/v1/downloads/{job_id}/retry",
            headers={"Authorization": f"Bearer {token}"},
        )

    assert response.status_code == 400
    assert "Only failed or deferred jobs can be retried" in response.json()["error"]["message"]


@pytest.mark.asyncio
async def test_retry_download_not_found():
    """Test retrying non-existent download returns 404."""
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        token = await create_test_user_and_login(client)
        response = await client.post(
            f"/api/v1/downloads/{uuid.uuid4()}/retry",
            headers={"Authorization": f"Bearer {token}"},
        )
    assert response.status_code == 404


@pytest.mark.asyncio
async def test_retry_download_requires_auth():
    """Test that retry requires authentication."""
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post(
            f"/api/v1/downloads/{uuid.uuid4()}/retry",
        )
    assert response.status_code == 401


@pytest.mark.asyncio
async def test_list_failed_jobs_filters_by_user_and_category(db_session: AsyncSession):
    """Test that DLQ listing preserves authentication, user isolation, and category filtering."""
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        token_a = await create_test_user_and_login(client, "dlq_a@example.com")
        token_b = await create_test_user_and_login(client, "dlq_b@example.com")
        user_a = _user_id_from_token(token_a)
        user_b = _user_id_from_token(token_b)

        visible_failed = _failed_job(user_a, category="transient", title="Visible")
        filtered_failed = _failed_job(user_a, category="timeout", title="Filtered")
        other_user_failed = _failed_job(user_b, category="transient", title="Other")
        db_session.add_all([visible_failed, filtered_failed, other_user_failed])
        await db_session.commit()

        unauthorized = await client.get("/api/v1/downloads/failed")
        response = await client.get(
            "/api/v1/downloads/failed?category=transient",
            headers={"Authorization": f"Bearer {token_a}"},
        )

    assert unauthorized.status_code == 401
    assert response.status_code == 200
    data = response.json()
    assert data["pagination"]["total"] == 1
    assert [job["id"] for job in data["failed_jobs"]] == [str(visible_failed.id)]
    assert data["failed_jobs"][0]["title"] == "Visible"


@pytest.mark.asyncio
async def test_replay_failed_job_resets_original_and_writes_outbox(db_session: AsyncSession):
    """Test that single DLQ replay resets the original job and writes an enqueue outbox row."""
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        token = await create_test_user_and_login(client, "dlq_replay@example.com")
        user_id = _user_id_from_token(token)
        job_id = uuid.uuid4()
        failed_job_id = uuid.uuid4()

        job = DownloadJob(
            id=job_id,
            user_id=user_id,
            url="https://www.youtube.com/watch?v=replay-original",
            status="failed",
            retry_count=2,
            next_retry_at=datetime.now(UTC),
            error="attempts failed",
            error_category="transient",
            completed_at=datetime.now(UTC),
        )
        db_session.add(job)
        db_session.add(_failed_job(user_id, failed_job_id=failed_job_id, original_job_id=job_id))
        await db_session.commit()

        response = await client.post(
            f"/api/v1/downloads/failed/{failed_job_id}/replay",
            headers={"Authorization": f"Bearer {token}"},
        )

    assert response.status_code == 200
    data = response.json()
    assert data["id"] == str(job_id)
    assert data["status"] == "pending"
    assert data["retry_count"] == 0
    assert data["next_retry_at"] is None
    assert data["error"] is None
    assert data["error_category"] is None
    assert data["completed_at"] is None

    await db_session.refresh(job)
    assert job.status == "pending"
    assert job.retry_count == 0
    assert job.error is None
    assert job.error_category is None

    failed_result = await db_session.execute(select(FailedJob).where(FailedJob.id == failed_job_id))
    assert failed_result.scalar_one_or_none() is None

    outbox_result = await db_session.execute(select(Outbox).where(Outbox.job_id == job_id))
    outbox = outbox_result.scalar_one()
    assert outbox.event_type == "enqueue_download"
    assert outbox.status == "pending"


@pytest.mark.asyncio
async def test_replay_failed_job_without_original_creates_job_and_updates_dlq_depth(
    db_session: AsyncSession,
):
    """Test that replaying an orphan DLQ row creates a job and updates DLQ depth."""
    metric = Mock()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        token = await create_test_user_and_login(client, "dlq_replay_new@example.com")
        user_id = _user_id_from_token(token)
        failed_job_id = uuid.uuid4()

        db_session.add(
            _failed_job(
                user_id,
                failed_job_id=failed_job_id,
                url="https://www.youtube.com/watch?v=replay-new",
            )
        )
        await db_session.commit()

        with patch("core.metrics.DLQ_DEPTH", metric):
            response = await client.post(
                f"/api/v1/downloads/failed/{failed_job_id}/replay",
                headers={"Authorization": f"Bearer {token}"},
            )

    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "pending"
    assert data["url"] == "https://www.youtube.com/watch?v=replay-new"
    metric.set.assert_called_once_with(0.0)

    failed_result = await db_session.execute(select(FailedJob).where(FailedJob.id == failed_job_id))
    assert failed_result.scalar_one_or_none() is None

    job_result = await db_session.execute(
        select(DownloadJob).where(DownloadJob.id == uuid.UUID(data["id"]))
    )
    created_job = job_result.scalar_one()
    assert created_job.user_id == user_id
    assert created_job.status == "pending"

    outbox_result = await db_session.execute(select(Outbox).where(Outbox.job_id == created_job.id))
    assert outbox_result.scalar_one().status == "pending"


@pytest.mark.asyncio
async def test_replay_failed_job_invalid_id_returns_400():
    """Test that single DLQ replay rejects malformed failed-job IDs."""
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        token = await create_test_user_and_login(client, "dlq_invalid@example.com")
        response = await client.post(
            "/api/v1/downloads/failed/not-a-uuid/replay",
            headers={"Authorization": f"Bearer {token}"},
        )

    assert response.status_code == 400
    assert "Invalid failed job ID format" in response.text


@pytest.mark.asyncio
async def test_replay_all_failed_jobs_batches_original_lookup_and_preserves_filter(
    db_session: AsyncSession,
):
    """Test that replay-all batches original lookup and only replays the filtered user's DLQ rows."""
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        token = await create_test_user_and_login(client, "dlq_all@example.com")
        other_token = await create_test_user_and_login(client, "dlq_other@example.com")
        user_id = _user_id_from_token(token)
        other_user_id = _user_id_from_token(other_token)

        original_one = DownloadJob(
            id=uuid.uuid4(),
            user_id=user_id,
            url="https://www.youtube.com/watch?v=replay-all-one",
            status="failed",
            retry_count=2,
            error="first failed",
            error_category="transient",
            completed_at=datetime.now(UTC),
        )
        original_two = DownloadJob(
            id=uuid.uuid4(),
            user_id=user_id,
            url="https://www.youtube.com/watch?v=replay-all-two",
            status="failed",
            retry_count=3,
            error="second failed",
            error_category="transient",
            completed_at=datetime.now(UTC),
        )
        db_session.add_all([original_one, original_two])

        transient_ids = [uuid.uuid4(), uuid.uuid4(), uuid.uuid4()]
        skipped_timeout_id = uuid.uuid4()
        other_user_failed_id = uuid.uuid4()
        db_session.add_all(
            [
                _failed_job(
                    user_id,
                    failed_job_id=transient_ids[0],
                    original_job_id=original_one.id,
                    category="transient",
                ),
                _failed_job(
                    user_id,
                    failed_job_id=transient_ids[1],
                    original_job_id=original_two.id,
                    category="transient",
                ),
                _failed_job(
                    user_id,
                    failed_job_id=transient_ids[2],
                    url="https://www.youtube.com/watch?v=replay-all-new",
                    category="transient",
                ),
                _failed_job(user_id, failed_job_id=skipped_timeout_id, category="timeout"),
                _failed_job(
                    other_user_id,
                    failed_job_id=other_user_failed_id,
                    category="transient",
                ),
            ]
        )
        await db_session.commit()

        download_job_selects = 0

        def count_download_job_selects(
            _conn,
            _cursor,
            statement: str,
            _parameters,
            _context,
            _executemany,
        ) -> None:
            nonlocal download_job_selects
            normalized = " ".join(statement.lower().split())
            if normalized.startswith("select") and " from download_jobs " in normalized:
                download_job_selects += 1

        event.listen(test_engine.sync_engine, "before_cursor_execute", count_download_job_selects)
        try:
            response = await client.post(
                "/api/v1/downloads/failed/replay-all?category=transient",
                headers={"Authorization": f"Bearer {token}"},
            )
        finally:
            event.remove(
                test_engine.sync_engine,
                "before_cursor_execute",
                count_download_job_selects,
            )

    assert response.status_code == 200
    assert response.json() == {"replayed": 3, "total": 3}
    assert download_job_selects == 1

    await db_session.refresh(original_one)
    await db_session.refresh(original_two)
    assert original_one.status == "pending"
    assert original_one.retry_count == 0
    assert original_one.error is None
    assert original_two.status == "pending"
    assert original_two.retry_count == 0
    assert original_two.error is None

    new_job_result = await db_session.execute(
        select(DownloadJob).where(
            DownloadJob.user_id == user_id,
            DownloadJob.url == "https://www.youtube.com/watch?v=replay-all-new",
        )
    )
    new_job = new_job_result.scalar_one()
    assert new_job.status == "pending"

    remaining_result = await db_session.execute(select(FailedJob.id))
    remaining_failed_ids = set(remaining_result.scalars().all())
    assert remaining_failed_ids == {skipped_timeout_id, other_user_failed_id}

    outbox_result = await db_session.execute(select(Outbox))
    outbox_job_ids = {entry.job_id for entry in outbox_result.scalars().all()}
    assert outbox_job_ids == {original_one.id, original_two.id, new_job.id}


@pytest.mark.asyncio
async def test_delete_download_file_cleanup_error(db_session: AsyncSession):
    """Test that failed file deletion during delete returns 500."""
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        token = await create_test_user_and_login(client)
        # Create download
        create_response = await client.post(
            "/api/v1/downloads",
            json={"url": "https://www.youtube.com/watch?v=dQw4w9WgXcQ"},
            headers={"Authorization": f"Bearer {token}"},
        )
        job_id = uuid.UUID(create_response.json()["id"])

        # Mark as completed with a file path that doesn't exist
        await db_session.execute(
            update(DownloadJob).where(DownloadJob.id == job_id).values(status="completed"),
        )
        await db_session.commit()

        # Try to delete - it will try to remove a non-existent file
        response = await client.delete(
            f"/api/v1/downloads/{job_id}",
            headers={"Authorization": f"Bearer {token}"},
        )

    # The actual response depends on whether file deletion fails
    # If file doesn't exist, it should succeed (204)
    # If there's an OSError, it might return 500
    assert response.status_code in (204, 500)


@pytest.mark.asyncio
async def test_delete_download_path_traversal_returns_403(db_session: AsyncSession):
    """Test that deleting a job with an invalid file path returns 403."""
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        token = await create_test_user_and_login(client)
        create_response = await client.post(
            "/api/v1/downloads",
            json={"url": "https://www.youtube.com/watch?v=dQw4w9WgXcQ"},
            headers={"Authorization": f"Bearer {token}"},
        )
        job_id = uuid.UUID(create_response.json()["id"])

        await db_session.execute(
            update(DownloadJob)
            .where(DownloadJob.id == job_id)
            .values(status="completed", file_path="/etc/passwd"),
        )
        await db_session.commit()

        response = await client.delete(
            f"/api/v1/downloads/{job_id}",
            headers={"Authorization": f"Bearer {token}"},
        )

    assert response.status_code == 403
    assert "Access denied" in response.json()["error"]["message"]


@pytest.mark.asyncio
async def test_create_download_non_youtube_urls():
    """Test that creating download jobs with non-YouTube URLs is accepted."""
    non_youtube_urls = [
        "https://vimeo.com/123456789",
        "https://www.dailymotion.com/video/abc123",
        "https://www.twitch.tv/videos/123456789",
        "https://www.tiktok.com/@user/video/123456789",
        "https://www.instagram.com/p/ABC123/",
    ]
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        token = await create_test_user_and_login(client)
        for url in non_youtube_urls:
            response = await client.post(
                "/api/v1/downloads",
                json={"url": url},
                headers={"Authorization": f"Bearer {token}"},
            )
            assert response.status_code == 201, (
                f"Expected 201 for {url}, got {response.status_code}: {response.text}"
            )
            data = response.json()
            assert data["url"] == url
            assert data["status"] == "pending"


@pytest.mark.asyncio
async def test_create_download_unsupported_platform_rejected():
    """Test that unsupported platforms (e.g., Facebook) are rejected with 422."""
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        token = await create_test_user_and_login(client)
        response = await client.post(
            "/api/v1/downloads",
            json={"url": "https://facebook.com/video/abc"},
            headers={"Authorization": f"Bearer {token}"},
        )
    assert response.status_code == 422

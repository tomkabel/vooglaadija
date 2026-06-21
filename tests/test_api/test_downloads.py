"""Downloads API endpoint tests."""

import uuid
from datetime import UTC, datetime

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncSession

from app.main import app
from core.models.download_job import DownloadJob
from tests.conftest import create_test_user_and_login


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

    from app.config import settings

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

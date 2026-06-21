"""Story 1.1 API tests for core model persistence."""

import uuid
from unittest.mock import AsyncMock, patch

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.main import app
from core.models.download_job import DownloadJob
from core.models.outbox import Outbox
from core.models.user import User


@pytest.mark.asyncio
async def test_download_api_persists_jobs_with_core_models(db_session: AsyncSession):
    """The download API should persist users, jobs, and outbox rows via core models."""
    email = f"core-model-{uuid.uuid4().hex}@example.com"
    password = "testpassword123"
    video_url = "https://www.youtube.com/watch?v=dQw4w9WgXcQ"

    with patch(
        "app.api.routes.downloads.resolve_video_title",
        new_callable=AsyncMock,
    ) as mock_resolve_title:
        mock_resolve_title.return_value = "Core Model Video"
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            register_response = await client.post(
                "/api/v1/auth/register",
                json={"email": email, "password": password},
            )
            login_response = await client.post(
                "/api/v1/auth/login",
                json={"email": email, "password": password},
            )
            token = login_response.json()["access_token"]

            create_response = await client.post(
                "/api/v1/downloads",
                json={"url": video_url},
                headers={"Authorization": f"Bearer {token}"},
            )
            list_response = await client.get(
                "/api/v1/downloads",
                headers={"Authorization": f"Bearer {token}"},
            )

    assert register_response.status_code == 201
    assert create_response.status_code == 201
    assert list_response.status_code == 200

    created = create_response.json()
    job_id = uuid.UUID(created["id"])
    assert created["title"] == "Core Model Video"
    assert list_response.json()["pagination"]["total"] == 1

    user_result = await db_session.execute(select(User).where(User.email == email))
    user = user_result.scalars().one()

    job_result = await db_session.execute(select(DownloadJob).where(DownloadJob.id == job_id))
    job = job_result.scalars().one()
    assert job.user_id == user.id
    assert job.url == video_url
    assert job.status == "pending"
    assert job.title == "Core Model Video"

    outbox_result = await db_session.execute(select(Outbox).where(Outbox.job_id == job_id))
    outbox = outbox_result.scalars().one()
    assert outbox.event_type == "enqueue_download"
    assert outbox.status == "pending"


@pytest.mark.asyncio
async def test_download_api_core_model_path_requires_auth():
    """The core-model-backed download API path should reject unauthenticated writes."""
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post(
            "/api/v1/downloads",
            json={"url": "https://www.youtube.com/watch?v=dQw4w9WgXcQ"},
        )

    assert response.status_code == 401

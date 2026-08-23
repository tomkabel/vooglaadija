"""Tests for guest demo access feature.

NOTE: With Clerk handling authentication, the demo login flow has changed.
The demo user is now created via Clerk's test mode or seed script.
These tests verify the seed script and demo user setup.
"""

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import func, select

from app.main import app
from core.models.download_job import DownloadJob
from core.models.user import User
from tests.conftest import TestingSessionLocal

DEMO_EMAIL = "demo@vooglaadija.io"

SEED_JOBS = [
    {
        "url": "https://www.youtube.com/watch?v=dQw4w9WgXcQ",
        "file_name": "Rick Astley - Never Gonna Give You Up.mp4",
        "file_path": "/app/storage/downloads/rick_astley_never_gonna_give_you_up.mp4",
    },
    {
        "url": "https://www.youtube.com/watch?v=jNQXAC9IVRw",
        "file_name": "Me at the zoo.mp4",
        "file_path": "/app/storage/downloads/me_at_the_zoo.mp4",
    },
    {
        "url": "https://www.youtube.com/watch?v=9bZkp7q19f0",
        "file_name": "PSY - GANGNAM STYLE.mp4",
        "file_path": "/app/storage/downloads/psy_gangnam_style.mp4",
    },
    {
        "url": "https://vimeo.com/76979871",
        "file_name": "Vimeo Sample Video.mp4",
        "file_path": "/app/storage/downloads/vimeo_sample.mp4",
    },
    {
        "url": "https://clips.twitch.tv/SmilingPluckySashimiBibleThump",
        "file_name": "Twitch Clip Highlight.mp4",
        "file_path": "/app/storage/downloads/twitch_clip.mp4",
    },
    {
        "url": "https://www.tiktok.com/@khaby.lame/video/7008477449723292934",
        "file_name": "TikTok Khaby Lame.mp4",
        "file_path": "/app/storage/downloads/tiktok_khaby.mp4",
    },
]


class TestDemoLoginRoute:
    """Tests for demo login with Clerk."""

    @pytest.mark.asyncio
    async def test_demo_login_redirects_to_web_login(self):
        """Demo login now redirects to Clerk sign-in page."""
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test", follow_redirects=False
        ) as client:
            response = await client.get("/web/login")

        assert response.status_code == 200
        # Login page now renders Clerk SignIn component
        assert "clerk-signin" in response.text


class TestSeedDemoData:
    """Tests for scripts/seed_demo_data.py idempotency."""

    @pytest.mark.asyncio
    async def test_seed_script_is_idempotent(self):
        """Running seed script twice does not create duplicate users or jobs."""
        from scripts.seed_demo_data import seed_demo_data

        await seed_demo_data()

        async with TestingSessionLocal() as session:
            result = await session.execute(
                select(func.count()).select_from(User).where(User.email == DEMO_EMAIL)
            )
            user_count_first = result.scalar_one()

            result = await session.execute(
                select(func.count())
                .select_from(DownloadJob)
                .join(User, DownloadJob.user_id == User.id)
                .where(User.email == DEMO_EMAIL)
            )
            job_count_first = result.scalar_one()

        assert user_count_first == 1
        assert job_count_first == 8

        await seed_demo_data()

        async with TestingSessionLocal() as session:
            result = await session.execute(
                select(func.count()).select_from(User).where(User.email == DEMO_EMAIL)
            )
            user_count_second = result.scalar_one()

            result = await session.execute(
                select(func.count())
                .select_from(DownloadJob)
                .join(User, DownloadJob.user_id == User.id)
                .where(User.email == DEMO_EMAIL)
            )
            job_count_second = result.scalar_one()

        assert user_count_second == 1
        assert job_count_second == 8

    @pytest.mark.asyncio
    async def test_seed_script_creates_demo_user(self):
        """Seed script creates demo user with correct email."""
        from scripts.seed_demo_data import seed_demo_data

        await seed_demo_data()

        async with TestingSessionLocal() as session:
            result = await session.execute(select(User).where(User.email == DEMO_EMAIL))
            user = result.scalar_one_or_none()

        assert user is not None
        assert user.email == DEMO_EMAIL
        assert user.is_active is True

    @pytest.mark.asyncio
    async def test_seed_script_creates_eight_jobs(self):
        """Seed script creates exactly 8 pending jobs for demo priming."""
        from scripts.seed_demo_data import seed_demo_data

        await seed_demo_data()

        async with TestingSessionLocal() as session:
            result = await session.execute(
                select(DownloadJob)
                .join(User, DownloadJob.user_id == User.id)
                .where(
                    User.email == DEMO_EMAIL,
                    DownloadJob.status == "pending",
                )
            )
            jobs = result.scalars().all()

        assert len(jobs) == 8
        for job in jobs:
            assert job.status == "pending"
            assert job.file_name is None

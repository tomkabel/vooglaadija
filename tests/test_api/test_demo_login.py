"""Tests for guest demo access feature."""

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import func, select

from app.main import app
from app.models.download_job import DownloadJob
from app.models.user import User
from app.services.auth_service import hash_password
from tests.conftest import TestingSessionLocal

DEMO_EMAIL = "demo@vooglaadija.io"
DEMO_PASSWORD = "VooglaadijaDemo2024!"

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
    """Tests for GET /web/demo-login."""

    @pytest.mark.asyncio
    async def test_demo_login_redirects_and_sets_cookies(self):
        """Demo login sets JWT cookies and redirects to /web/downloads."""
        async with TestingSessionLocal() as session:
            demo_user = User(
                id=uuid.uuid4(),
                username="Demo User",
                email=DEMO_EMAIL,
                password_hash=await hash_password(DEMO_PASSWORD),
                is_active=True,
            )
            session.add(demo_user)
            await session.commit()

        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test", follow_redirects=False
        ) as client:
            response = await client.get("/web/demo-login")

        assert response.status_code == 303
        assert response.headers["location"] == "/web/downloads"
        assert "access_token" in response.cookies
        assert "refresh_token" in response.cookies
        assert response.cookies.get("access_token") != ""

    @pytest.mark.asyncio
    async def test_demo_login_inactive_user_returns_500(self):
        """Demo login returns 500 for inactive demo user."""
        async with TestingSessionLocal() as session:
            demo_user = User(
                id=uuid.uuid4(),
                username="Demo User Inactive",
                email=DEMO_EMAIL,
                password_hash=await hash_password(DEMO_PASSWORD),
                is_active=False,
            )
            session.add(demo_user)
            await session.commit()

        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test", follow_redirects=False
        ) as client:
            response = await client.get("/web/demo-login")

        assert response.status_code == 403

    @pytest.mark.asyncio
    async def test_demo_login_user_not_found_returns_500(self):
        """Demo login returns 500 when demo user is not seeded."""
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test", follow_redirects=False
        ) as client:
            response = await client.get("/web/demo-login")

        assert response.status_code == 500


class TestDemoLoginProtectedAccess:
    """Test that demo login grants access to protected pages."""

    @pytest.mark.asyncio
    async def test_demo_user_can_access_dashboard_after_login(self):
        """Demo user can access /web/downloads after demo login."""
        demo_user_id = uuid.uuid4()

        async with TestingSessionLocal() as session:
            demo_user = User(
                id=demo_user_id,
                username="Demo User",
                email=DEMO_EMAIL,
                password_hash=await hash_password(DEMO_PASSWORD),
                is_active=True,
            )
            session.add(demo_user)

            now = datetime.now(UTC)
            for job_data in SEED_JOBS:
                job = DownloadJob(
                    id=uuid.uuid4(),
                    user_id=demo_user_id,
                    url=job_data["url"],
                    status="completed",
                    file_name=job_data["file_name"],
                    file_path=job_data["file_path"],
                    completed_at=now,
                    expires_at=now + timedelta(hours=72),
                )
                session.add(job)
            await session.commit()

        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test", follow_redirects=False
        ) as client:
            login_response = await client.get("/web/demo-login")
            assert login_response.status_code == 303

            access_token = login_response.cookies.get("access_token", "")

            dashboard_response = await client.get(
                "/web/downloads",
                cookies={"access_token": access_token},
            )

        assert dashboard_response.status_code == 200


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
        """Seed script creates exactly 8 completed jobs (6 platforms)."""
        from scripts.seed_demo_data import seed_demo_data

        await seed_demo_data()

        async with TestingSessionLocal() as session:
            result = await session.execute(
                select(DownloadJob)
                .join(User, DownloadJob.user_id == User.id)
                .where(
                    User.email == DEMO_EMAIL,
                    DownloadJob.status == "completed",
                )
            )
            jobs = result.scalars().all()

        assert len(jobs) == 8
        for job in jobs:
            assert job.status == "completed"
            assert job.file_name is not None

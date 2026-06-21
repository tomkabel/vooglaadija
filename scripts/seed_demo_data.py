#!/usr/bin/env python3
"""
Seed demo data: demo user + 8 pending download jobs.

Jobs are created with status="pending" so they appear "just submitted" on the dashboard.
When the demo user logs in via /web/demo-login, the login handler enqueues them to Redis
with a 200ms stagger, causing live pending→processing→completed transitions via SSE.

Idempotent — skips if demo user already exists.
Run with: python scripts/seed_demo_data.py
"""

import asyncio
import os
import sys
import uuid
from datetime import UTC, datetime, timedelta

# Ensure project root is on path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sqlalchemy import select

from app.database import get_async_session_factory
from core.models.download_job import DownloadJob
from core.models.user import User
from app.services.auth_service import hash_password

DEMO_EMAIL = "demo@vooglaadija.io"
DEMO_PASSWORD = "VooglaadijaDemo2024!"

SEED_JOBS = [
    {
        "url": "https://www.youtube.com/watch?v=dQw4w9WgXcQ",
        "file_name": "Rick Astley - Never Gonna Give You Up.mp4",
        "file_path": "/app/storage/downloads/rick_astley_never_gonna_give_you_up.mp4",
        "platform": "YouTube",
    },
    {
        "url": "https://www.youtube.com/watch?v=jNQXAC9IVRw",
        "file_name": "Me at the zoo.mp4",
        "file_path": "/app/storage/downloads/me_at_the_zoo.mp4",
        "platform": "YouTube",
    },
    {
        "url": "https://www.youtube.com/watch?v=9bZkp7q19f0",
        "file_name": "PSY - GANGNAM STYLE.mp4",
        "file_path": "/app/storage/downloads/psy_gangnam_style.mp4",
        "platform": "YouTube",
    },
    {
        "url": "https://vimeo.com/76979871",
        "file_name": "Vimeo Sample Video.mp4",
        "file_path": "/app/storage/downloads/vimeo_sample.mp4",
        "platform": "Vimeo",
    },
    {
        "url": "https://www.dailymotion.com/video/x84sh87",
        "file_name": "Dailymotion Demo.mp4",
        "file_path": "/app/storage/downloads/dailymotion_demo.mp4",
        "platform": "Dailymotion",
    },
    {
        "url": "https://clips.twitch.tv/SmilingPluckySashimiBibleThump",
        "file_name": "Twitch Clip Highlight.mp4",
        "file_path": "/app/storage/downloads/twitch_clip.mp4",
        "platform": "Twitch",
    },
    {
        "url": "https://www.tiktok.com/@khaby.lame/video/7008477449723292934",
        "file_name": "TikTok Khaby Lame.mp4",
        "file_path": "/app/storage/downloads/tiktok_khaby.mp4",
        "platform": "TikTok",
    },
    {
        "url": "https://www.instagram.com/reel/DGcoPAktJAT/",
        "file_name": "Instagram Growth Reel.mp4",
        "file_path": "/app/storage/downloads/instagram_reel.mp4",
        "platform": "Instagram",
    },
]


async def seed_demo_data() -> None:
    """Create demo user and pre-seeded jobs if they don't exist."""
    session_factory = get_async_session_factory()

    async with session_factory() as session:
        # Check if demo user already exists
        result = await session.execute(select(User).where(User.email == DEMO_EMAIL))
        existing_user = result.scalar_one_or_none()

        if existing_user is not None:
            print(f"Demo user already exists ({DEMO_EMAIL}), skipping seed.")
            return

        # Create demo user
        now = datetime.now(UTC)
        demo_user = User(
            id=uuid.uuid4(),
            username="Demo User",
            email=DEMO_EMAIL,
            password_hash=await hash_password(DEMO_PASSWORD),
            is_active=True,
        )
        session.add(demo_user)
        await session.flush()

        # Create 8 pending download jobs (no outbox entries — priming happens on login)
        for job_data in SEED_JOBS:
            job = DownloadJob(
                id=uuid.uuid4(),
                user_id=demo_user.id,
                url=job_data["url"],
                status="pending",
                file_name=None,
                file_path=None,
                completed_at=None,
                expires_at=now + timedelta(hours=72),
                retry_count=0,
                max_retries=3,
            )
            session.add(job)

        await session.commit()
        print(f"Seeded demo user ({DEMO_EMAIL}) with {len(SEED_JOBS)} pending jobs.")


if __name__ == "__main__":
    asyncio.run(seed_demo_data())

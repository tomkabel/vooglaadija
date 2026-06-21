"""Story 3.2 guardrails for extracted web download routes."""

import json
import uuid
from collections import OrderedDict
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest
from fastapi.routing import APIRoute
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select

from app.api.routes.sse import _emit_initial_snapshot
from app.main import app
from core.models.download_job import DownloadJob
from tests.conftest import TestingSessionLocal
from tests.test_api.test_web_routes import do_login, do_register, get_csrf_from_response

DOWNLOAD_ROUTE_MODULE = "app.api.routes.web.web_downloads"
DOWNLOAD_SERVICE_MODULE = "app.services.download_service"
PROJECT_ROOT = Path(__file__).resolve().parents[1]


def test_web_downloads_module_contains_only_download_route_paths():
    """The web_downloads module registers only web download route paths."""
    from app.api.routes.web import web_downloads

    route_paths = {
        route.path for route in web_downloads.router.routes if isinstance(route, APIRoute)
    }

    assert route_paths == {
        "/downloads",
        "/downloads/full",
        "/downloads/{job_id}",
        "/downloads/{job_id}/file",
    }


def test_web_downloads_module_stays_below_story_size_limit():
    """The extracted web_downloads module stays under the Story 3.2 size limit."""
    source_path = PROJECT_ROOT / "app/api/routes/web/web_downloads.py"
    non_comment_lines = [
        line
        for line in source_path.read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    ]

    assert len(non_comment_lines) < 250


def test_web_download_routes_are_registered_once_on_aggregate_router():
    """The aggregate app registers each extracted web download route exactly once."""
    observed_routes = [
        (method, route.path, route.endpoint.__module__)
        for route in app.routes
        if isinstance(route, APIRoute)
        for method in route.methods
        if route.path
        in {
            "/web/downloads",
            "/web/downloads/full",
            "/web/downloads/{job_id}",
            "/web/downloads/{job_id}/file",
        }
    ]

    assert sorted(observed_routes) == sorted(
        [
            ("GET", "/web/downloads", DOWNLOAD_ROUTE_MODULE),
            ("POST", "/web/downloads", DOWNLOAD_ROUTE_MODULE),
            ("POST", "/web/downloads/full", DOWNLOAD_ROUTE_MODULE),
            ("DELETE", "/web/downloads/{job_id}", DOWNLOAD_ROUTE_MODULE),
            ("GET", "/web/downloads/{job_id}/file", DOWNLOAD_ROUTE_MODULE),
        ]
    )


def test_web_downloads_module_does_not_own_non_download_routes():
    """The web_downloads module does not register auth, settings, chaos, or slides paths."""
    from app.api.routes.web import web_downloads

    route_paths = {
        route.path for route in web_downloads.router.routes if isinstance(route, APIRoute)
    }

    assert "/login" not in route_paths
    assert "/register" not in route_paths
    assert "/settings" not in route_paths
    assert "/settings/username" not in route_paths
    assert "/settings/delete-account" not in route_paths
    assert "/settings/password" not in route_paths
    assert "/chaos-lab" not in route_paths
    assert "/chaos-lab/status" not in route_paths
    assert "/slides" not in route_paths


def test_downloads_stream_stays_owned_by_sse_router():
    """The aggregate app keeps /web/downloads/stream owned by the SSE route module."""
    observed_routes = [
        (method, route.path, route.endpoint.__module__)
        for route in app.routes
        if isinstance(route, APIRoute)
        for method in route.methods
        if route.path == "/web/downloads/stream"
    ]

    assert observed_routes == [("GET", "/web/downloads/stream", "app.api.routes.sse")]


@pytest.mark.asyncio
async def test_web_downloads_smoke_flow_create_list_sse_and_delete(sample_url):
    """The extracted download routes preserve create, list, SSE reachability, and delete flow."""
    email = f"story32_smoke_{uuid.uuid4().hex[:8]}@example.com"
    password = "securepassword123"

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test", follow_redirects=False
    ) as client:
        await do_register(client, email, password)
        csrf_token = await do_login(client, email, password)

        login_response = await client.post(
            "/web/login",
            data={"email": email, "password": password},
            headers={"X-CSRF-Token": csrf_token},
        )
        access_token = login_response.cookies.get("access_token", "")
        csrf_token = (
            login_response.cookies.get("csrf_token")
            or client.cookies.get("csrf_token")
            or csrf_token
        )

        with (
            patch(
                f"{DOWNLOAD_SERVICE_MODULE}.resolve_video_title", new_callable=AsyncMock
            ) as title,
            patch(f"{DOWNLOAD_SERVICE_MODULE}.enqueue_job", new_callable=AsyncMock) as enqueue,
        ):
            title.return_value = "Story 3.2 smoke video"
            enqueue.return_value = None

            create_response = await client.post(
                "/web/downloads",
                data={"url": sample_url},
                headers={"HX-Request": "true", "X-CSRF-Token": csrf_token},
                cookies={"access_token": access_token},
            )

        assert create_response.status_code == 200
        assert "Story 3.2 smoke video" in create_response.text

        async with TestingSessionLocal() as session:
            job_result = await session.execute(
                select(DownloadJob).where(DownloadJob.url == sample_url)
            )
            job = job_result.scalars().one()
            job.status = "completed"
            await session.commit()
            job_id = str(job.id)
            user_id = job.user_id

        list_response = await client.get(
            "/web/downloads",
            cookies={"access_token": access_token},
        )
        csrf_token = get_csrf_from_response(list_response) or csrf_token
        sse_events = await _emit_initial_snapshot(TestingSessionLocal, user_id, OrderedDict())
        sse_route = next(
            route
            for route in app.routes
            if isinstance(route, APIRoute) and route.path == "/web/downloads/stream"
        )

        delete_response = await client.delete(
            f"/web/downloads/{job_id}",
            headers={"HX-Request": "true", "X-CSRF-Token": csrf_token},
            cookies={"access_token": access_token},
        )

    assert list_response.status_code == 200
    assert "Story 3.2 smoke video" in list_response.text
    assert sse_route.endpoint.__module__ == "app.api.routes.sse"
    assert any(json.loads(str(event.data))["id"] == job_id for event in sse_events)
    assert delete_response.status_code == 200

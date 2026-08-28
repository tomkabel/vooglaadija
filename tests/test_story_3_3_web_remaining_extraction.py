"""Story 3.3 guardrails for remaining extracted web routes and helpers."""

import re
import uuid
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest
from fastapi.routing import APIRoute
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select

from app.main import app
from core.models.download_job import DownloadJob
from core.models.user import User
from tests.conftest import TestingSessionLocal
from tests.test_api.test_web_routes import do_login, do_register, get_csrf_from_response
from tests.test_route_introspection import iter_api_routes

pytestmark = pytest.mark.slow


DASHBOARD_ROUTE_MODULE = "app.api.routes.web.web_dashboard"
SETTINGS_ROUTE_MODULE = "app.api.routes.web.web_settings"
PROJECT_ROOT = Path(__file__).resolve().parents[1]


def _route_paths(router) -> set[str]:
    """Return concrete route paths from a FastAPI router."""
    return {route.path for route in router.routes if isinstance(route, APIRoute)}


def _non_comment_non_blank_lines(path: Path) -> list[str]:
    """Return source lines that count toward story size limits."""
    return [
        line
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    ]


def test_web_dashboard_module_contains_only_dashboard_route_paths():
    """The web_dashboard module registers only chaos lab and slides route paths."""
    from app.api.routes.web import web_dashboard

    route_paths = _route_paths(web_dashboard.router)

    assert route_paths == {"/chaos-lab", "/chaos-lab/status", "/slides"}
    assert "/login" not in route_paths
    assert "/downloads" not in route_paths
    assert "/settings" not in route_paths


def test_web_settings_module_contains_only_settings_route_paths():
    """The web_settings module registers only settings and account route paths."""
    from app.api.routes.web import web_settings

    route_paths = _route_paths(web_settings.router)

    assert route_paths == {"/settings", "/settings/username", "/settings/delete-account"}
    assert "/login" not in route_paths
    assert "/downloads" not in route_paths
    assert "/settings/password" not in route_paths


def test_settings_password_remains_owned_by_web_auth():
    """The password settings route remains owned by the auth route module."""
    from app.api.routes.web import web_auth, web_dashboard, web_settings

    assert "/settings/password" in _route_paths(web_auth.router)
    assert "/settings/password" not in _route_paths(web_dashboard.router)
    assert "/settings/password" not in _route_paths(web_settings.router)


def test_moved_web_routes_are_registered_once_on_aggregate_router():
    """The aggregate app registers each moved web route exactly once."""
    observed_routes = [
        (method, route.path, route.endpoint.__module__)
        for route in iter_api_routes(app)
        for method in route.methods
        if route.path
        in {
            "/web/chaos-lab",
            "/web/chaos-lab/status",
            "/web/slides",
            "/web/settings",
            "/web/settings/username",
            "/web/settings/delete-account",
        }
    ]

    assert sorted(observed_routes) == sorted(
        [
            ("GET", "/web/chaos-lab", DASHBOARD_ROUTE_MODULE),
            ("GET", "/web/chaos-lab/status", DASHBOARD_ROUTE_MODULE),
            ("GET", "/web/slides", DASHBOARD_ROUTE_MODULE),
            ("GET", "/web/settings", SETTINGS_ROUTE_MODULE),
            ("POST", "/web/settings/username", SETTINGS_ROUTE_MODULE),
            ("POST", "/web/settings/delete-account", SETTINGS_ROUTE_MODULE),
        ]
    )


def test_web_aggregate_router_stays_thin_and_has_no_route_decorators():
    """The web package aggregate remains a thin router with no route decorators."""
    source_path = PROJECT_ROOT / "app/api/routes/web/__init__.py"
    source = source_path.read_text(encoding="utf-8")

    assert len(_non_comment_non_blank_lines(source_path)) < 50
    assert re.search(r"@router\.(get|post|delete|put|patch)", source) is None


def test_web_route_modules_stay_below_story_size_limit():
    """Every web route module stays below the Story 3.3 physical line limit."""
    route_module_paths = [
        PROJECT_ROOT / "app/api/routes/web/__init__.py",
        PROJECT_ROOT / "app/api/routes/web/web_auth.py",
        PROJECT_ROOT / "app/api/routes/web/web_dashboard.py",
        PROJECT_ROOT / "app/api/routes/web/web_downloads.py",
        PROJECT_ROOT / "app/api/routes/web/web_settings.py",
    ]

    oversized_modules = {
        path.relative_to(PROJECT_ROOT).as_posix(): len(
            path.read_text(encoding="utf-8").splitlines()
        )
        for path in route_module_paths
        if len(path.read_text(encoding="utf-8").splitlines()) > 300
    }

    assert oversized_modules == {}


def test_package_local_web_helpers_own_no_routes():
    """The package-local web_helpers module owns helpers but no router or route decorators."""
    source_path = PROJECT_ROOT / "app/api/routes/web/web_helpers.py"
    source = source_path.read_text(encoding="utf-8")

    assert "APIRouter(" not in source
    assert re.search(r"@router\.(get|post|delete|put|patch)", source) is None


def test_settings_module_does_not_import_helpers_through_aggregate():
    """The moved settings module imports helpers directly and does not mutate helper globals."""
    source_path = PROJECT_ROOT / "app/api/routes/web/web_settings.py"
    source = source_path.read_text(encoding="utf-8")

    assert "from app.api.routes.web import web_helpers" not in source
    assert "web_helpers.settings =" not in source


@pytest.mark.asyncio
async def test_settings_page_username_update_and_delete_account_smoke_flow():
    """The moved settings routes preserve page, username update, and account deletion flow."""
    email = f"story33_settings_{uuid.uuid4().hex[:8]}@example.com"
    password = "securepassword123"

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test", follow_redirects=False
    ) as client:
        await do_register(client, email, password)
        csrf_token = await do_login(client, email, password)
        access_token = client.cookies.get("__Host-access_token", "")

        settings_response = await client.get(
            "/web/settings", cookies={"__Host-access_token": access_token}
        )
        csrf_token = get_csrf_from_response(settings_response) or csrf_token

        username_response = await client.post(
            "/web/settings/username",
            data={"username": "  story33-user  "},
            headers={"X-CSRF-Token": csrf_token},
            cookies={"__Host-access_token": access_token},
        )

        settings_after_update = await client.get(
            "/web/settings", cookies={"__Host-access_token": access_token}
        )
        csrf_token = get_csrf_from_response(settings_after_update) or csrf_token

        delete_response = await client.post(
            "/web/settings/delete-account",
            data={"password": password, "confirm_text": "DELETE"},
            headers={"X-CSRF-Token": csrf_token},
            cookies={"__Host-access_token": access_token},
        )

    assert settings_response.status_code == 200
    assert username_response.status_code == 303
    assert username_response.headers["location"] == "/web/settings?updated=username"
    assert "story33-user" in settings_after_update.text
    assert delete_response.status_code == 303
    assert delete_response.headers["location"] == "/web/login?account_deleted=1"


@pytest.mark.asyncio
async def test_settings_username_htmx_error_returns_fragment():
    """The moved username route keeps returning an HTMX validation fragment for short names."""
    email = f"story33_usererr_{uuid.uuid4().hex[:8]}@example.com"
    password = "securepassword123"

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test", follow_redirects=False
    ) as client:
        await do_register(client, email, password)
        csrf_token = await do_login(client, email, password)
        access_token = client.cookies.get("__Host-access_token", "")

        settings_response = await client.get(
            "/web/settings", cookies={"__Host-access_token": access_token}
        )
        csrf_token = get_csrf_from_response(settings_response) or csrf_token

        username_response = await client.post(
            "/web/settings/username",
            data={"username": "ab"},
            headers={"HX-Request": "true", "X-CSRF-Token": csrf_token},
            cookies={"__Host-access_token": access_token},
        )

    assert username_response.status_code == 400
    assert "Username must be at least 3 characters" in username_response.text
    assert "error-box" in username_response.text


@pytest.mark.asyncio
async def test_delete_account_cleanup_failure_preserves_user_and_jobs(tmp_path):
    """The moved delete-account route aborts deletion when download cleanup fails."""
    email = f"story33_delfail_{uuid.uuid4().hex[:8]}@example.com"
    password = "securepassword123"

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test", follow_redirects=False
    ) as client:
        await do_register(client, email, password)
        csrf_token = await do_login(client, email, password)
        access_token = client.cookies.get("__Host-access_token", "")

        async with TestingSessionLocal() as session:
            user_result = await session.execute(select(User).where(User.email == email))
            user = user_result.scalar_one()
            job = DownloadJob(
                id=uuid.uuid4(),
                user_id=user.id,
                url="https://www.youtube.com/watch?v=dQw4w9WgXcQ",
                status="completed",
                file_path=str(tmp_path / ".." / "etc" / "passwd"),
            )
            session.add(job)
            await session.commit()
            user_id = user.id
            job_id = job.id

        settings_response = await client.get(
            "/web/settings", cookies={"__Host-access_token": access_token}
        )
        csrf_token = get_csrf_from_response(settings_response) or csrf_token

        with patch("app.services.user_service.settings") as mock_settings:
            mock_settings.storage_path = str(tmp_path)
            delete_response = await client.post(
                "/web/settings/delete-account",
                data={"password": password, "confirm_text": "DELETE"},
                headers={"X-CSRF-Token": csrf_token},
                cookies={"__Host-access_token": access_token},
            )

    async with TestingSessionLocal() as session:
        user_after = await session.get(User, user_id)
        job_after = await session.get(DownloadJob, job_id)

    assert delete_response.status_code == 303
    assert delete_response.headers["location"] == "/web/settings?error=file_cleanup"
    assert user_after is not None
    assert job_after is not None


@pytest.mark.asyncio
async def test_chaos_lab_returns_404_when_feature_flag_disabled():
    """The moved chaos lab page keeps returning 404 when its feature flag is disabled."""
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test", follow_redirects=False
    ) as client:
        with patch(f"{DASHBOARD_ROUTE_MODULE}.settings") as mock_settings:
            mock_settings.feature_chaos_api_enabled = False

            page_response = await client.get("/web/chaos-lab")
            status_response = await client.get("/web/chaos-lab/status")

    assert page_response.status_code == 404
    assert status_response.status_code == 404


@pytest.mark.asyncio
async def test_chaos_status_renders_enabled_partial():
    """The moved chaos status route renders the enabled status partial."""
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test", follow_redirects=False
    ) as client:
        with (
            patch(f"{DASHBOARD_ROUTE_MODULE}.settings") as mock_settings,
            patch(
                f"{DASHBOARD_ROUTE_MODULE}.get_all_chaos_status", new_callable=AsyncMock
            ) as mock_status,
        ):
            mock_settings.feature_chaos_api_enabled = True
            mock_status.return_value = {
                "circuit_breaker_open": True,
                "worker_crash": False,
                "db_failover": False,
                "throttle_spike": False,
                "slow_processing": True,
            }

            response = await client.get("/web/chaos-lab/status")

    assert response.status_code == 200
    assert "Active scenarios" in response.text
    assert "Circuit Breaker" in response.text
    assert "ACTIVE" in response.text
    assert "SLOWED" in response.text
    mock_status.assert_awaited_once()


@pytest.mark.asyncio
async def test_slides_route_renders_presentation_page():
    """The moved slides route keeps rendering the presentation template."""
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test", follow_redirects=False
    ) as client:
        response = await client.get("/web/slides")

    assert response.status_code == 200
    assert "Vooglaadija" in response.text

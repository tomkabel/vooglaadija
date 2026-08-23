"""Story 3.1 guardrails for extracted web auth routes."""

import uuid
from pathlib import Path

import pytest
from fastapi.routing import APIRoute
from httpx import ASGITransport, AsyncClient

from app.main import app
from tests.test_route_introspection import iter_api_routes


def _csrf_cookie(response) -> str:
    """Return the CSRF cookie value from a web response."""
    token = response.cookies.get("csrf_token")
    assert token is not None
    return token


def test_web_module_is_package_after_auth_extraction():
    """The web routes module is a package with no sibling web.py module."""
    assert not Path("app/api/routes/web.py").exists()
    assert Path("app/api/routes/web/__init__.py").is_file()


def test_web_auth_module_contains_only_auth_route_paths():
    """The web_auth module registers auth routes and no download or settings-page routes."""
    from app.api.routes.web import web_auth

    route_paths = {route.path for route in web_auth.router.routes if isinstance(route, APIRoute)}

    assert route_paths == {
        "/login",
        "/register",
        "/demo-login",
        "/logout",
        "/settings/password",
    }
    assert "/downloads" not in route_paths
    assert "/settings" not in route_paths
    assert "/settings/username" not in route_paths
    assert "/settings/delete-account" not in route_paths


def test_web_auth_routes_are_registered_once_on_aggregate_router():
    """The aggregate application registers each extracted web auth route exactly once."""
    observed_routes = [
        (method, route.path, route.endpoint.__module__)
        for route in iter_api_routes(app)
        for method in route.methods
        if route.path
        in {
            "/web/login",
            "/web/register",
            "/web/demo-login",
            "/web/logout",
            "/web/settings/password",
        }
    ]

    assert sorted(observed_routes) == sorted(
        [
            ("GET", "/web/login", "app.api.routes.web.web_auth"),
            ("POST", "/web/login", "app.api.routes.web.web_auth"),
            ("GET", "/web/register", "app.api.routes.web.web_auth"),
            ("POST", "/web/register", "app.api.routes.web.web_auth"),
            ("POST", "/web/demo-login", "app.api.routes.web.web_auth"),
            ("POST", "/web/logout", "app.api.routes.web.web_auth"),
            ("POST", "/web/settings/password", "app.api.routes.web.web_auth"),
        ]
    )


@pytest.mark.asyncio
async def test_web_auth_smoke_flow_register_login_validate_csrf_and_logout():
    """The extracted auth routes preserve the full register-login-CSRF-logout smoke flow."""
    email = f"story31_smoke_{uuid.uuid4().hex[:8]}@example.com"
    password = "securepassword123"

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="https://test", follow_redirects=False
    ) as client:
        register_page = await client.get("/web/register")
        register_csrf = _csrf_cookie(register_page)

        register_response = await client.post(
            "/web/register",
            data={
                "email": email,
                "password": password,
                "password_confirm": password,
            },
            headers={"X-CSRF-Token": register_csrf},
        )

        login_page = await client.get("/web/login")
        login_csrf = _csrf_cookie(login_page)

        login_response = await client.post(
            "/web/login",
            data={"email": email, "password": password},
            headers={"X-CSRF-Token": login_csrf},
        )
        active_csrf = _csrf_cookie(login_response)

        dashboard_response = await client.get("/web/downloads")
        dashboard_csrf = _csrf_cookie(dashboard_response)

        logout_response = await client.post(
            "/web/logout",
            headers={"X-CSRF-Token": dashboard_csrf},
        )

    assert register_response.status_code == 303
    assert register_response.headers["location"] == "/web/downloads"
    assert "__Host-access_token" in register_response.cookies
    assert "__Host-refresh_token" in register_response.cookies

    assert login_response.status_code == 303
    assert login_response.headers["location"] == "/web/downloads"
    assert "__Host-access_token" in login_response.cookies
    assert "__Host-refresh_token" in login_response.cookies
    assert active_csrf != login_csrf

    assert dashboard_response.status_code == 200
    assert dashboard_csrf

    assert logout_response.status_code == 303
    assert logout_response.headers["location"] == "/web/login?logged_out=1"

"""Auth endpoint tests for Clerk integration."""

import pytest
from httpx import ASGITransport, AsyncClient

from app.main import app

MOCK_CLERK_TOKEN = "mock-token-user_test123456789"


@pytest.mark.asyncio
async def test_me_authenticated_returns_user():
    """Test that valid Clerk token returns user data."""
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get(
            "/api/v1/auth/me",
            headers={"Authorization": f"Bearer {MOCK_CLERK_TOKEN}"},
        )
    assert response.status_code == 200
    data = response.json()
    assert data["email"] == "user_0test123456789@example.com"
    assert "id" in data


@pytest.mark.asyncio
async def test_me_no_token_fails():
    """Test that missing token returns 401."""
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get("/api/v1/auth/me")
    assert response.status_code == 401


@pytest.mark.asyncio
async def test_me_invalid_token_fails():
    """Test that invalid token returns 401."""
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get(
            "/api/v1/auth/me",
            headers={"Authorization": "Bearer invalid-token"},
        )
    assert response.status_code == 401


@pytest.mark.asyncio
async def test_login_redirects_to_web_login():
    """Test that POST /login redirects to web login page."""
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post("/api/v1/auth/login")
    assert response.status_code == 303
    assert "/web/login" in response.headers["location"]


@pytest.mark.asyncio
async def test_register_redirects_to_web_register():
    """Test that POST /register redirects to web register page."""
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post("/api/v1/auth/register")
    assert response.status_code == 303
    assert "/web/register" in response.headers["location"]


@pytest.mark.asyncio
async def test_logout_redirects_to_web_login():
    """Test that POST /logout redirects to web login page."""
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post("/api/v1/auth/logout")
    assert response.status_code == 303
    assert "/web/login" in response.headers["location"]

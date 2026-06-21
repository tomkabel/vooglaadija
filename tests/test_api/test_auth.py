"""Auth endpoint tests."""

import uuid

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select

from app.main import app
from app.services.auth_service import verify_password
from core.models.user import User
from tests.conftest import TestingSessionLocal


@pytest.mark.asyncio
async def test_register_creates_user():
    """Test that valid registration creates a user and returns 201."""
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post(
            "/api/v1/auth/register",
            json={"email": "test@example.com", "password": "testpassword123"},
        )
    assert response.status_code == 201
    data = response.json()
    assert data["email"] == "test@example.com"
    assert "id" in data


@pytest.mark.asyncio
async def test_register_persists_default_username_and_hashed_password():
    """Test REST registration persists UserService defaults and password hashing."""
    local_part = f"api_user_service_{uuid.uuid4().hex[:8]}"
    email = f"{local_part}@example.com"
    password = "testpassword123"

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post(
            "/api/v1/auth/register",
            json={"email": email, "password": password},
        )

    async with TestingSessionLocal() as session:
        result = await session.execute(select(User).where(User.email == email))
        user = result.scalar_one()

    assert response.status_code == 201
    assert user.username == local_part
    assert user.password_hash != password
    assert await verify_password(password, user.password_hash) is True


@pytest.mark.asyncio
async def test_register_duplicate_email_fails():
    """Test that registering with existing email returns 409."""
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        # First registration
        await client.post(
            "/api/v1/auth/register",
            json={"email": "duplicate@example.com", "password": "testpassword123"},
        )
        # Second registration with same email
        response = await client.post(
            "/api/v1/auth/register",
            json={"email": "duplicate@example.com", "password": "testpassword123"},
        )
    assert response.status_code == 409
    assert "Email already registered" in response.json()["error"]["message"]


@pytest.mark.asyncio
async def test_register_invalid_email_fails():
    """Test that invalid email format returns 422."""
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post(
            "/api/v1/auth/register",
            json={"email": "not-an-email", "password": "testpassword123"},
        )
    assert response.status_code == 422


@pytest.mark.asyncio
async def test_register_short_password_fails():
    """Test that password shorter than 8 chars returns 422."""
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post(
            "/api/v1/auth/register",
            json={"email": "test@example.com", "password": "short"},
        )
    assert response.status_code == 422


@pytest.mark.asyncio
async def test_register_missing_fields_fails():
    """Test that missing fields return 422."""
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post(
            "/api/v1/auth/register",
            json={"email": "test@example.com"},
        )
    assert response.status_code == 422


@pytest.mark.asyncio
async def test_login_success_returns_tokens():
    """Test that valid credentials return access and refresh tokens."""
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        # Register first
        await client.post(
            "/api/v1/auth/register",
            json={"email": "login@example.com", "password": "testpassword123"},
        )
        # Login
        response = await client.post(
            "/api/v1/auth/login",
            json={"email": "login@example.com", "password": "testpassword123"},
        )
    assert response.status_code == 200
    data = response.json()
    assert "access_token" in data
    assert "refresh_token" in data
    assert data["token_type"] == "bearer"


@pytest.mark.asyncio
async def test_login_wrong_password_fails():
    """Test that wrong password returns 401."""
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        # Register first
        await client.post(
            "/api/v1/auth/register",
            json={"email": "wrongpass@example.com", "password": "testpassword123"},
        )
        # Login with wrong password
        response = await client.post(
            "/api/v1/auth/login",
            json={"email": "wrongpass@example.com", "password": "wrongpassword"},
        )
    assert response.status_code == 401
    assert "Incorrect email or password" in response.json()["error"]["message"]


@pytest.mark.asyncio
async def test_login_nonexistent_user_fails():
    """Test that unknown email returns 401."""
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post(
            "/api/v1/auth/login",
            json={"email": "nonexistent@example.com", "password": "testpassword123"},
        )
    assert response.status_code == 401
    assert "Incorrect email or password" in response.json()["error"]["message"]


@pytest.mark.asyncio
async def test_login_missing_fields_fails():
    """Test that missing fields return 422."""
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post(
            "/api/v1/auth/login",
            json={"email": "test@example.com"},
        )
    assert response.status_code == 422


@pytest.mark.asyncio
async def test_refresh_valid_token_returns_new_access():
    """Test that valid refresh token returns new access token."""
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        # Register and login
        await client.post(
            "/api/v1/auth/register",
            json={"email": "refresh@example.com", "password": "testpassword123"},
        )
        login_response = await client.post(
            "/api/v1/auth/login",
            json={"email": "refresh@example.com", "password": "testpassword123"},
        )
        refresh_token = login_response.json()["refresh_token"]

        # Refresh
        response = await client.post(
            "/api/v1/auth/refresh",
            json={"refresh_token": refresh_token},
        )
    assert response.status_code == 200
    data = response.json()
    assert "access_token" in data
    assert data["token_type"] == "bearer"


@pytest.mark.asyncio
async def test_refresh_invalid_token_fails():
    """Test that invalid refresh token returns 401."""
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post(
            "/api/v1/auth/refresh",
            json={"refresh_token": "invalid-token"},
        )
    assert response.status_code == 401


@pytest.mark.asyncio
async def test_me_authenticated_returns_user():
    """Test that valid token returns user data."""
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        # Register and login
        await client.post(
            "/api/v1/auth/register",
            json={"email": "me@example.com", "password": "testpassword123"},
        )
        login_response = await client.post(
            "/api/v1/auth/login",
            json={"email": "me@example.com", "password": "testpassword123"},
        )
        access_token = login_response.json()["access_token"]

        # Get me
        response = await client.get(
            "/api/v1/auth/me",
            headers={"Authorization": f"Bearer {access_token}"},
        )
    assert response.status_code == 200
    data = response.json()
    assert data["email"] == "me@example.com"
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

"""Auth endpoint tests."""

import uuid
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from fastapi import HTTPException
from httpx import ASGITransport, AsyncClient
from jose import JWTError, jwt
from sqlalchemy import select

from app import auth
from app.api.dependencies import get_current_user_from_cookie
from app.main import app
from app.services.auth_service import verify_password
from core.models.user import User
from tests.conftest import TestingSessionLocal

CURRENT_SECRET = "current-api-secret-key-for-rotation-tests-32chars"
PREVIOUS_SECRET = "previous-api-secret-key-for-rotation-tests-32chars"


class RotationSettings:
    """Minimal settings object for API auth rotation tests."""

    secret_key = CURRENT_SECRET
    secret_key_previous = PREVIOUS_SECRET
    access_token_expire_minutes = 15
    refresh_token_expire_days = 7


def _previous_key_refresh_token(
    user_id: str,
    *,
    issued_at: datetime | None = None,
    expires_at: datetime | None = None,
    token_version: int = 1,
) -> str:
    issued_at = issued_at or datetime.now(UTC)
    expires_at = expires_at or datetime.now(UTC) + timedelta(days=1)
    return jwt.encode(
        {
            "sub": user_id,
            "user_id": user_id,
            "exp": expires_at,
            "type": auth.REFRESH_TOKEN_TYPE,
            "iat": issued_at,
            "jti": "previous-key-api-refresh-jti",
            "ver": token_version,
        },
        PREVIOUS_SECRET,
        algorithm=auth.ALGORITHM,
    )


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
async def test_refresh_rejects_jti_already_reserved(monkeypatch):
    """A refresh token whose jti is already reserved (replay/concurrent use) is rejected."""

    from app.services import token_blacklist

    async def _already_reserved(_token_jti: str, ttl_seconds: int = 0) -> bool:
        return False

    monkeypatch.setattr(token_blacklist, "reserve_token_jti", _already_reserved)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        await client.post(
            "/api/v1/auth/register",
            json={"email": "reuse@example.com", "password": "testpassword123"},
        )
        login_response = await client.post(
            "/api/v1/auth/login",
            json={"email": "reuse@example.com", "password": "testpassword123"},
        )
        refresh_token = login_response.json()["refresh_token"]

        response = await client.post(
            "/api/v1/auth/refresh",
            json={"refresh_token": refresh_token},
        )
    assert response.status_code == 401
    assert "already been used" in response.json()["error"]["message"]


@pytest.mark.asyncio
async def test_refresh_accepts_previous_key_token_during_rotation(monkeypatch):
    """Test that a recent previous-key refresh token yields current-key replacements."""
    monkeypatch.setattr(auth, "settings", RotationSettings())

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        register_response = await client.post(
            "/api/v1/auth/register",
            json={"email": "old-refresh@example.com", "password": "testpassword123"},
        )
        user_id = register_response.json()["id"]
        old_refresh_token = _previous_key_refresh_token(
            user_id,
            issued_at=datetime.now(UTC) - timedelta(hours=1),
        )

        response = await client.post(
            "/api/v1/auth/refresh",
            json={"refresh_token": old_refresh_token},
        )

    assert response.status_code == 200
    data = response.json()
    assert data["token_type"] == "bearer"

    access_payload = jwt.decode(
        data["access_token"],
        CURRENT_SECRET,
        algorithms=[auth.ALGORITHM],
    )
    refresh_payload = jwt.decode(
        data["refresh_token"],
        CURRENT_SECRET,
        algorithms=[auth.ALGORITHM],
    )

    assert access_payload["sub"] == user_id
    assert access_payload["type"] == auth.ACCESS_TOKEN_TYPE
    assert refresh_payload["sub"] == user_id
    assert refresh_payload["type"] == auth.REFRESH_TOKEN_TYPE

    for token in (data["access_token"], data["refresh_token"]):
        try:
            jwt.decode(token, PREVIOUS_SECRET, algorithms=[auth.ALGORITHM])
        except JWTError:
            pass
        else:
            raise AssertionError("refreshed token unexpectedly decoded with previous key")


@pytest.mark.asyncio
async def test_refresh_rejects_previous_key_token_after_rotation_window(monkeypatch):
    """Test that an old previous-key refresh token is rejected after 24 hours."""
    monkeypatch.setattr(auth, "settings", RotationSettings())

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        register_response = await client.post(
            "/api/v1/auth/register",
            json={"email": "stale-refresh@example.com", "password": "testpassword123"},
        )
        old_refresh_token = _previous_key_refresh_token(
            register_response.json()["id"],
            issued_at=datetime.now(UTC) - timedelta(hours=25),
        )

        response = await client.post(
            "/api/v1/auth/refresh",
            json={"refresh_token": old_refresh_token},
        )

    assert response.status_code == 401
    assert "Invalid or expired refresh token" in response.json()["error"]["message"]


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
async def test_me_rejects_refresh_token_for_bearer_auth(db_session):
    """Protected API routes must reject refresh tokens."""
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        email = f"refresh-as-access-{uuid.uuid4().hex[:8]}@example.com"
        await client.post(
            "/api/v1/auth/register",
            json={"email": email, "password": "testpassword123"},
        )
        login_response = await client.post(
            "/api/v1/auth/login",
            json={"email": email, "password": "testpassword123"},
        )
        refresh_token = login_response.json()["refresh_token"]

        response = await client.get(
            "/api/v1/auth/me",
            headers={"Authorization": f"Bearer {refresh_token}"},
        )

    assert response.status_code == 401
    assert "Could not validate credentials" in response.json()["error"]["message"]


@pytest.mark.asyncio
async def test_cookie_auth_rejects_refresh_token(db_session):
    """Cookie-backed protected routes must also reject refresh tokens."""
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        email = f"cookie-refresh-{uuid.uuid4().hex[:8]}@example.com"
        await client.post(
            "/api/v1/auth/register",
            json={"email": email, "password": "testpassword123"},
        )

    result = await db_session.execute(select(User).where(User.email == email))
    user = result.scalar_one()
    request = SimpleNamespace(cookies={"__Host-access_token": auth.create_refresh_token(user.id)})

    with pytest.raises(HTTPException) as exc_info:
        await get_current_user_from_cookie(db_session, request, None)

    assert exc_info.value.status_code == 401
    assert exc_info.value.detail == "Could not validate credentials"


@pytest.mark.asyncio
async def test_logout_blacklists_both_token_cookies():
    """Logout should await revocation for both access and refresh tokens."""
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        await client.post(
            "/api/v1/auth/register",
            json={"email": "logout-blacklist@example.com", "password": "testpassword123"},
        )
        await client.post(
            "/api/v1/auth/login",
            json={"email": "logout-blacklist@example.com", "password": "testpassword123"},
        )

        with patch(
            "app.services.token_blacklist.blacklist_token", new_callable=AsyncMock
        ) as mock_blacklist:
            response = await client.post("/api/v1/auth/logout")

    assert response.status_code == 303
    assert mock_blacklist.await_count == 2


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

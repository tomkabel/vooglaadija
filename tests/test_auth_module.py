"""Tests for JWT token creation and verification."""

from datetime import UTC, datetime, timedelta
from unittest.mock import MagicMock

from jose import jwt

from app.auth import (
    ALGORITHM,
    clear_token_cookies,
    create_access_token,
    create_refresh_token,
    set_token_cookies,
    verify_token,
)
from core.config import settings


class TestCreateAccessToken:
    def test_returns_valid_jwt(self):
        token = create_access_token("user-123")
        payload = verify_token(token)
        assert payload is not None
        assert payload["sub"] == "user-123"
        assert "exp" in payload

    def test_different_tokens_for_different_users(self):
        t1 = create_access_token("user-1")
        t2 = create_access_token("user-2")
        assert t1 != t2
        assert verify_token(t1)["sub"] == "user-1"
        assert verify_token(t2)["sub"] == "user-2"


class TestCreateRefreshToken:
    def test_includes_refresh_type(self):
        token = create_refresh_token("user-123")
        payload = verify_token(token)
        assert payload is not None
        assert payload["type"] == "refresh"
        assert payload["sub"] == "user-123"

    def test_differs_from_access_token(self):
        access = create_access_token("user-123")
        refresh = create_refresh_token("user-123")
        assert access != refresh
        assert verify_token(access)["type"] == "access"
        assert verify_token(refresh)["type"] == "refresh"


class TestVerifyToken:
    def test_valid_token(self):
        token = create_access_token("user-123")
        payload = verify_token(token)
        assert payload is not None
        assert payload["sub"] == "user-123"

    def test_invalid_token_returns_none(self):
        assert verify_token("not.a.valid.token") is None
        assert verify_token("") is None

    def test_expired_token_returns_none(self):
        past = datetime.now(UTC) - timedelta(hours=1)
        token = jwt.encode(
            {"sub": "user-123", "exp": past}, settings.secret_key, algorithm=ALGORITHM
        )
        assert verify_token(token) is None

    def test_wrong_secret_returns_none(self):
        token = jwt.encode(
            {"sub": "user-123"},
            "wrong-secret-key-that-is-at-least-32-chars-long",
            algorithm=ALGORITHM,
        )
        assert verify_token(token) is None


class TestSetTokenCookies:
    """Tests for set_token_cookies function."""

    def test_set_token_cookies_sets_access_token(self):
        """Test that set_token_cookies sets the access token cookie."""
        response = MagicMock()
        access_token = create_access_token("user-123")
        refresh_token = create_refresh_token("user-123")

        set_token_cookies(response, access_token, refresh_token)

        access_token_call = None
        for call in response.set_cookie.call_args_list:
            # TESTING defaults set cookie_secure=False → unprefixed names.
            if call.kwargs.get("key") == "access_token":
                access_token_call = call
                break

        assert access_token_call is not None
        assert access_token_call.kwargs["value"] == access_token
        assert access_token_call.kwargs["httponly"] is True
        assert access_token_call.kwargs["samesite"] == "lax"
        assert "max_age" in access_token_call.kwargs

    def test_set_token_cookies_sets_refresh_token(self):
        """Test that set_token_cookies sets the refresh token cookie."""
        response = MagicMock()
        access_token = create_access_token("user-123")
        refresh_token = create_refresh_token("user-123")

        set_token_cookies(response, access_token, refresh_token)

        refresh_token_call = None
        for call in response.set_cookie.call_args_list:
            if call.kwargs.get("key") == "refresh_token":
                refresh_token_call = call
                break

        assert refresh_token_call is not None
        assert refresh_token_call.kwargs["value"] == refresh_token
        assert refresh_token_call.kwargs["httponly"] is True
        assert "max_age" in refresh_token_call.kwargs


class TestClearTokenCookies:
    """Tests for clear_token_cookies function."""

    def test_secure_config_uses_host_prefix(self, monkeypatch):
        """cookie_secure=True must select the __Host- cookie names.

        Regression (finding): the prefix was forced off only in TESTING;
        with COOKIE_SECURE=false (plain-HTTP dev) the Secure flag was still
        forced on while the __Host- name made browsers drop the cookie.
        """
        from core.config import settings

        monkeypatch.setattr(settings, "cookie_secure", True)
        response = MagicMock()

        set_token_cookies(response, "a", "r")

        keys = [call.kwargs.get("key") for call in response.set_cookie.call_args_list]
        assert "__Host-access_token" in keys
        assert "__Host-refresh_token" in keys
        for call in response.set_cookie.call_args_list:
            assert call.kwargs["secure"] is True

    def test_clear_token_cookies_deletes_access_token(self):
        """Test that clear_token_cookies deletes the access token cookie."""
        response = MagicMock()

        clear_token_cookies(response)

        delete_call = None
        for call in response.delete_cookie.call_args_list:
            if call.kwargs.get("key") == "access_token":
                delete_call = call
                break

        assert delete_call is not None

    def test_clear_token_cookies_deletes_refresh_token(self):
        """Test that clear_token_cookies deletes the refresh token cookie."""
        response = MagicMock()

        clear_token_cookies(response)

        delete_call = None
        for call in response.delete_cookie.call_args_list:
            if call.kwargs.get("key") == "refresh_token":
                delete_call = call
                break

        assert delete_call is not None

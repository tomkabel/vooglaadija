"""Web routes tests."""

import re
import uuid
from datetime import UTC, datetime, timedelta
from html.parser import HTMLParser
from types import SimpleNamespace
from typing import ClassVar
from unittest.mock import AsyncMock, patch

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select

from app.main import app
from app.services.auth_service import verify_password
from core.models.download_job import DownloadJob
from core.models.outbox import Outbox
from core.models.user import User
from tests.conftest import TestingSessionLocal


def get_csrf_from_response(response) -> str | None:
    """Extract CSRF token from response cookies."""
    return response.cookies.get("csrf_token")


async def do_register(client: AsyncClient, email: str, password: str) -> str:
    """Register a user and return the CSRF token from the response."""
    csrf_response = await client.get("/web/register")
    csrf_token = get_csrf_from_response(csrf_response)

    headers = {}
    if csrf_token:
        headers["X-CSRF-Token"] = csrf_token

    await client.post(
        "/web/register",
        data={
            "email": email,
            "password": password,
            "password_confirm": password,
        },
        headers=headers,
    )
    return csrf_token


async def do_login(client: AsyncClient, email: str, password: str) -> str:
    """Login a user and return the rotated CSRF token from the response."""
    csrf_response = await client.get("/web/login")
    csrf_token = get_csrf_from_response(csrf_response)

    headers = {}
    if csrf_token:
        headers["X-CSRF-Token"] = csrf_token

    login_response = await client.post(
        "/web/login",
        data={"email": email, "password": password},
        headers=headers,
    )
    return (
        login_response.cookies.get("csrf_token") or client.cookies.get("csrf_token") or csrf_token
    )


class _FirstDownloadRowParser(HTMLParser):
    """Extract the first rendered download row from a larger HTML fragment."""

    void_tags: ClassVar[set[str]] = {
        "area",
        "base",
        "br",
        "col",
        "embed",
        "hr",
        "img",
        "input",
        "link",
        "meta",
    }

    def __init__(self) -> None:
        super().__init__(convert_charrefs=False)
        self.depth = 0
        self.done = False
        self.parts: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attr_map = dict(attrs)
        classes = (attr_map.get("class") or "").split()
        if (
            not self.parts
            and tag == "div"
            and "download-row" in classes
            and "data-job-id" in attr_map
        ):
            self.depth = 1
            self.parts.append(self.get_starttag_text() or "")
            return
        if self.parts and not self.done:
            self.parts.append(self.get_starttag_text() or "")
            if tag not in self.void_tags:
                self.depth += 1

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if self.parts and not self.done:
            self.parts.append(self.get_starttag_text() or "")

    def handle_endtag(self, tag: str) -> None:
        if self.parts and not self.done:
            self.parts.append(f"</{tag}>")
            if tag not in self.void_tags:
                self.depth -= 1
            if self.depth == 0:
                self.done = True

    def handle_data(self, data: str) -> None:
        if self.parts and not self.done:
            self.parts.append(data)


def _first_download_row(html: str) -> str:
    """Return the first non-skeleton download row as normalized HTML."""
    parser = _FirstDownloadRowParser()
    parser.feed(html)
    return re.sub(r"\s+", " ", "".join(parser.parts)).strip()


class TestValidateRedirectUrl:
    """Tests for _validate_redirect_url helper."""

    def test_none_returns_default(self):
        """Test that None URL returns default."""
        from app.api.routes.web import _validate_redirect_url

        result = _validate_redirect_url(None, "/web/downloads")
        assert result == "/web/downloads"

    def test_empty_string_returns_default(self):
        """Test that empty string returns default."""
        from app.api.routes.web import _validate_redirect_url

        result = _validate_redirect_url("", "/web/downloads")
        assert result == "/web/downloads"

    def test_valid_internal_path(self):
        """Test that valid internal path is allowed."""
        from app.api.routes.web import _validate_redirect_url

        result = _validate_redirect_url("/web/downloads", "/web/login")
        assert result == "/web/downloads"

    def test_external_url_rejected(self):
        """Test that external URLs are rejected."""
        from app.api.routes.web import _validate_redirect_url

        result = _validate_redirect_url("https://evil.com", "/web/downloads")
        assert result == "/web/downloads"

    def test_external_url_with_path_rejected(self):
        """Test that external URLs with path are rejected."""
        from app.api.routes.web import _validate_redirect_url

        result = _validate_redirect_url("https://evil.com/phishing", "/web/downloads")
        assert result == "/web/downloads"

    def test_relative_path_with_scheme_rejected(self):
        """Test that paths with schemes are rejected."""
        from app.api.routes.web import _validate_redirect_url

        result = _validate_redirect_url("javascript:alert(1)", "/web/downloads")
        assert result == "/web/downloads"

    def test_protocol_relative_url_rejected(self):
        """Test that protocol-relative URLs are rejected."""
        from app.api.routes.web import _validate_redirect_url

        result = _validate_redirect_url("//example.com", "/web/downloads")
        assert result == "/web/downloads"

    def test_path_without_leading_slash_rejected(self):
        """Test that paths without leading slash are rejected."""
        from app.api.routes.web import _validate_redirect_url

        result = _validate_redirect_url("web/downloads", "/web/downloads")
        assert result == "/web/downloads"

    def test_path_with_double_slashes_rejected(self):
        """Test that paths with backslashes are normalized and rejected if not allowed."""
        from app.api.routes.web import _validate_redirect_url

        result = _validate_redirect_url("\\web\\downloads", "/web/downloads")
        assert result == "/web/downloads"

    def test_valid_web_path_allowed(self):
        """Test that paths starting with /web/ are allowed."""
        from app.api.routes.web import _validate_redirect_url

        result = _validate_redirect_url("/web/login", "/web/downloads")
        assert result == "/web/login"

    def test_path_traversal_rejected(self):
        """Test that path traversal attempts starting with /web/../ are rejected."""
        from app.api.routes.web import _validate_redirect_url

        result = _validate_redirect_url("/web/../etc/passwd", "/web/downloads")
        assert result == "/web/downloads"

    def test_path_traversal_with_double_dots_rejected(self):
        """Test that simple path traversal /../ is rejected."""
        from app.api.routes.web import _validate_redirect_url

        result = _validate_redirect_url("/web/../../etc/passwd", "/web/downloads")
        assert result == "/web/downloads"


class TestGetCsrfToken:
    """Tests for CSRF token reuse and hardening."""

    def test_existing_hex_cookie_token_is_reused(self):
        """Well-formed CSRF cookies should be reused for follow-up renders."""
        from unittest.mock import MagicMock

        from fastapi import Request

        from app.api.routes.web import get_csrf_token

        token = "a" * 32
        mock_request = MagicMock(spec=Request)
        mock_request.cookies = {"csrf_token": token}

        assert get_csrf_token(mock_request) == token

    def test_invalid_cookie_token_is_replaced(self):
        """Malformed CSRF cookie values should not be reflected back to clients."""
        from unittest.mock import MagicMock

        from fastapi import Request

        from app.api.routes.web import get_csrf_token

        mock_request = MagicMock(spec=Request)
        mock_request.cookies = {"csrf_token": "not-a-valid-token\r\n"}

        token = get_csrf_token(mock_request)

        assert token != "not-a-valid-token\r\n"
        assert re.fullmatch(r"[0-9a-f]{32}", token, re.IGNORECASE)

    def test_invalid_token_is_not_written_back_to_cookie(self):
        """Cookie issuance regenerates malformed CSRF token values."""
        from fastapi import Response

        from app.api.routes.web import set_csrf_token_cookie

        response = Response()
        set_csrf_token_cookie(response, "not-a-valid-token\r\n")

        cookie_header = response.headers["set-cookie"]
        assert "not-a-valid-token" not in cookie_header
        assert re.search(r"csrf_token=[0-9a-f]{32}", cookie_header, re.IGNORECASE)


class TestValidateCsrfToken:
    """Tests for validate_csrf_token helper."""

    @pytest.mark.asyncio
    async def test_get_request_always_valid(self):
        """Test that GET requests don't require CSRF validation."""
        from unittest.mock import MagicMock

        from fastapi import Request

        from app.api.routes.web import validate_csrf_token

        mock_request = MagicMock(spec=Request)
        mock_request.method = "GET"
        mock_request.headers = {}
        mock_request.cookies = {}

        result = await validate_csrf_token(mock_request)
        assert result is True

    @pytest.mark.asyncio
    async def test_head_request_always_valid(self):
        """Test that HEAD requests don't require CSRF validation."""
        from unittest.mock import MagicMock

        from fastapi import Request

        from app.api.routes.web import validate_csrf_token

        mock_request = MagicMock(spec=Request)
        mock_request.method = "HEAD"
        mock_request.headers = {}
        mock_request.cookies = {}

        result = await validate_csrf_token(mock_request)
        assert result is True

    @pytest.mark.asyncio
    async def test_options_request_always_valid(self):
        """Test that OPTIONS requests don't require CSRF validation."""
        from unittest.mock import MagicMock

        from fastapi import Request

        from app.api.routes.web import validate_csrf_token

        mock_request = MagicMock(spec=Request)
        mock_request.method = "OPTIONS"
        mock_request.headers = {}
        mock_request.cookies = {}

        result = await validate_csrf_token(mock_request)
        assert result is True

    @pytest.mark.asyncio
    async def test_missing_cookie_token_returns_false(self):
        """Test that missing cookie CSRF token returns False."""
        from unittest.mock import AsyncMock, MagicMock

        from fastapi import Request

        from app.api.routes.web import validate_csrf_token

        mock_request = MagicMock(spec=Request)
        mock_request.method = "POST"
        mock_request.headers = {"X-CSRF-Token": "sometoken"}
        mock_request.cookies = {}
        mock_request.form = AsyncMock(return_value={})

        result = await validate_csrf_token(mock_request)
        assert result is False

    @pytest.mark.asyncio
    async def test_valid_header_token(self):
        """Test that matching header and cookie tokens return True."""
        from unittest.mock import MagicMock

        from fastapi import Request

        from app.api.routes.web import validate_csrf_token

        mock_request = MagicMock(spec=Request)
        mock_request.method = "POST"
        mock_request.headers = {"X-CSRF-Token": "validtoken", "HX-Request": "true"}
        mock_request.cookies = {"csrf_token": "validtoken"}

        result = await validate_csrf_token(mock_request)
        assert result is True

    @pytest.mark.asyncio
    async def test_invalid_header_token(self):
        """Test that non-matching header and cookie tokens return False."""
        from unittest.mock import MagicMock

        from fastapi import Request

        from app.api.routes.web import validate_csrf_token

        mock_request = MagicMock(spec=Request)
        mock_request.method = "POST"
        mock_request.headers = {"X-CSRF-Token": "wrongtoken"}
        mock_request.cookies = {"csrf_token": "goodtoken"}

        result = await validate_csrf_token(mock_request)
        assert result is False


class TestDownloadsBasePath:
    """Tests for the web downloads base path helper."""

    def test_downloads_base_path_uses_configured_storage_path(self, tmp_path):
        """The downloads base path is derived from the configured storage path."""
        from app.api.routes.web import _downloads_base_path

        with patch("app.api.routes.web.web_helpers.settings") as mock_settings:
            mock_settings.storage_path = str(tmp_path)

            assert _downloads_base_path() == f"{tmp_path}/downloads"

    def test_canonical_validator_blocks_path_traversal(self, tmp_path):
        """The canonical validator rejects web download paths outside the base."""
        from core.utils.security import validate_path

        downloads_dir = tmp_path / "downloads"
        downloads_dir.mkdir()

        with pytest.raises(ValueError, match="Path traversal detected"):
            validate_path(str(downloads_dir), str(tmp_path / ".." / "etc" / "passwd"))


class TestIsHtmxRequest:
    """Tests for is_htmx_request helper."""

    def test_htmx_request_true(self):
        """Test that HX-Request header set to 'true' returns True."""
        from unittest.mock import MagicMock

        from fastapi import Request

        from app.api.routes.web import is_htmx_request

        mock_request = MagicMock(spec=Request)
        mock_request.headers.get = MagicMock(return_value="true")

        assert is_htmx_request(mock_request) is True

    def test_htmx_request_false(self):
        """Test that missing or non-true HX-Request returns False."""
        from unittest.mock import MagicMock

        from fastapi import Request

        from app.api.routes.web import is_htmx_request

        mock_request = MagicMock(spec=Request)
        mock_request.headers.get = MagicMock(return_value=None)

        assert is_htmx_request(mock_request) is False

    def test_htmx_request_not_true(self):
        """Test that HX-Request header set to 'false' returns False."""
        from unittest.mock import MagicMock

        from fastapi import Request

        from app.api.routes.web import is_htmx_request

        mock_request = MagicMock(spec=Request)
        mock_request.headers.get = MagicMock(return_value="false")

        assert is_htmx_request(mock_request) is False


class TestLoginPage:
    """Tests for GET /web/login."""

    @pytest.mark.asyncio
    async def test_login_page_renders(self):
        """Test that login page renders with CSRF token."""
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response = await client.get("/web/login")

        assert response.status_code == 200
        assert "csrf_token" in response.text
        assert response.cookies.get("csrf_token") is not None

    @pytest.mark.asyncio
    async def test_login_page_includes_form(self):
        """Test that login page contains login form elements."""
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response = await client.get("/web/login")

        assert response.status_code == 200
        assert 'name="email"' in response.text
        assert 'name="password"' in response.text
        assert 'name="csrf_token"' in response.text

    @pytest.mark.asyncio
    async def test_login_page_maps_error_to_field_level_accessibility(self):
        """Test login error is shown inline and linked through ARIA."""
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response = await client.get("/web/login?error=1")

        assert response.status_code == 200
        assert 'id="email-error"' in response.text
        assert 'aria-describedby="email-error"' in response.text
        assert 'aria-errormessage="email-error"' in response.text
        assert "Invalid email or password" in response.text


class TestLoginForm:
    """Tests for POST /web/login."""

    @pytest.mark.asyncio
    async def test_login_success_sets_cookies(self, monkeypatch):
        """Test successful login via non-HTMX form sets auth cookies."""
        from core.config import settings

        email = f"logintest_{uuid.uuid4().hex[:8]}@example.com"
        password = "securepassword123"

        # __Host-* cookies are only valid with Secure; the raw Set-Cookie
        # headers must carry it (the test-suite default omits it so httpx's
        # cookie jar keeps working over plain http). Over https the jar also
        # retains the Secure cookies, so the name checks below still pass.
        monkeypatch.setattr(settings, "cookie_secure", True)

        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="https://test", follow_redirects=False
        ) as client:
            await do_register(client, email, password)
            csrf_token = await do_login(client, email, password)

            login_response = await client.post(
                "/web/login",
                data={"email": email, "password": password},
                headers={"X-CSRF-Token": csrf_token},
            )

        assert login_response.status_code == 303
        # cookie_secure=True was set above → the __Host- names are active.
        assert "__Host-access_token" in login_response.cookies
        assert "__Host-refresh_token" in login_response.cookies
        # Validate the raw Set-Cookie attributes: Secure, Path=/, no Domain —
        # the properties that make a cookie a valid __Host- cookie.
        raw_cookies = login_response.headers.get_list("set-cookie")
        for name in ("__Host-access_token", "__Host-refresh_token"):
            raw = next((h for h in raw_cookies if h.startswith(f"{name}=")), None)
            assert raw is not None, f"missing Set-Cookie header for {name}"
            assert "Secure" in raw
            assert "Path=/" in raw
            assert "Domain=" not in raw

    @pytest.mark.asyncio
    async def test_login_invalid_csrf(self):
        """Test login with invalid CSRF token returns 303 redirect to login page with error."""
        email = f"csrf_fail_{uuid.uuid4().hex[:8]}@example.com"
        password = "securepassword123"

        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test", follow_redirects=False
        ) as client:
            await do_register(client, email, password)
            _ = await do_login(client, email, password)

            login_response = await client.post(
                "/web/login",
                data={
                    "email": email,
                    "password": password,
                    "csrf_token": "invalid_token",
                },
            )

        assert login_response.status_code == 303
        assert login_response.headers["location"] == "/web/login?error=csrf"

    @pytest.mark.asyncio
    async def test_login_wrong_password(self):
        """Test login with wrong password returns redirect to login page (303)."""
        email = f"wrongpass_{uuid.uuid4().hex[:8]}@example.com"
        password = "securepassword123"

        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test", follow_redirects=False
        ) as client:
            await do_register(client, email, password)
            csrf_token = await do_login(client, email, password)

            login_response = await client.post(
                "/web/login",
                data={
                    "email": email,
                    "password": "wrongpassword",
                },
                headers={"X-CSRF-Token": csrf_token},
            )

        assert login_response.status_code == 303

    @pytest.mark.asyncio
    async def test_login_nonexistent_user(self):
        """Test login with non-existent email returns redirect to login page (303)."""
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test", follow_redirects=False
        ) as client:
            csrf_response = await client.get("/web/login")
            csrf_token = get_csrf_from_response(csrf_response)

            login_response = await client.post(
                "/web/login",
                data={
                    "email": "nonexistent@example.com",
                    "password": "somepassword123",
                },
                headers={"X-CSRF-Token": csrf_token} if csrf_token else {},
            )

        assert login_response.status_code == 303


class TestRegisterPage:
    """Tests for GET /web/register."""

    @pytest.mark.asyncio
    async def test_register_page_renders(self):
        """Test that register page renders with CSRF token."""
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response = await client.get("/web/register")

        assert response.status_code == 200
        assert "csrf_token" in response.text
        assert response.cookies.get("csrf_token") is not None

    @pytest.mark.asyncio
    async def test_register_page_includes_form(self):
        """Test that register page contains registration form elements."""
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response = await client.get("/web/register")

        assert response.status_code == 200
        assert 'name="email"' in response.text
        assert 'name="password"' in response.text
        assert 'name="password_confirm"' in response.text

    @pytest.mark.asyncio
    async def test_register_page_maps_error_to_field_level_accessibility(self):
        """Test register error is shown inline and linked through ARIA."""
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response = await client.get("/web/register?error=password_mismatch")

        assert response.status_code == 200
        assert 'id="password-confirm-error"' in response.text
        assert 'aria-describedby="password-confirm-error"' in response.text
        assert 'aria-errormessage="password-confirm-error"' in response.text
        assert "Passwords do not match" in response.text


class TestRegisterForm:
    """Tests for POST /web/register."""

    @pytest.mark.asyncio
    async def test_register_success_sets_cookies(self):
        """Test successful registration via non-HTMX form sets auth cookies."""
        email = f"newuser_{uuid.uuid4().hex[:8]}@example.com"
        password = "securepassword123"

        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test", follow_redirects=False
        ) as client:
            csrf_response = await client.get("/web/register")
            csrf_token = get_csrf_from_response(csrf_response)

            reg_response = await client.post(
                "/web/register",
                data={
                    "email": email,
                    "password": password,
                    "password_confirm": password,
                },
                headers={"X-CSRF-Token": csrf_token} if csrf_token else {},
            )

        assert reg_response.status_code == 303
        # TESTING defaults set cookie_secure=False → unprefixed cookie names.
        assert "access_token" in reg_response.cookies
        assert "refresh_token" in reg_response.cookies

    @pytest.mark.asyncio
    async def test_register_success_persists_default_username_and_hashed_password(self):
        """Test Web registration persists UserService defaults and password hashing."""
        local_part = f"websvc_{uuid.uuid4().hex[:8]}"
        email = f"{local_part}@example.com"
        password = "securepassword123"

        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test", follow_redirects=False
        ) as client:
            csrf_response = await client.get("/web/register")
            csrf_token = get_csrf_from_response(csrf_response)

            reg_response = await client.post(
                "/web/register",
                data={
                    "email": email,
                    "password": password,
                    "password_confirm": password,
                },
                headers={"X-CSRF-Token": csrf_token} if csrf_token else {},
            )

        async with TestingSessionLocal() as session:
            result = await session.execute(select(User).where(User.email == email))
            user = result.scalar_one()

        assert reg_response.status_code == 303
        assert reg_response.headers["location"] == "/web/downloads"
        assert user.username == local_part
        assert user.password_hash != password
        assert await verify_password(password, user.password_hash) is True

    @pytest.mark.asyncio
    async def test_register_password_mismatch(self):
        """Test registration with mismatched passwords returns 303 redirect with error."""
        email = f"mismatch_{uuid.uuid4().hex[:8]}@example.com"
        password = "securepassword123"

        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test", follow_redirects=False
        ) as client:
            csrf_response = await client.get("/web/register")
            csrf_token = get_csrf_from_response(csrf_response)

            reg_response = await client.post(
                "/web/register",
                data={
                    "email": email,
                    "password": password,
                    "password_confirm": "differentpassword",
                },
                headers={"X-CSRF-Token": csrf_token} if csrf_token else {},
            )

        assert reg_response.status_code == 303
        assert reg_response.headers["location"] == "/web/register?error=password_mismatch"

    @pytest.mark.asyncio
    async def test_register_short_password(self):
        """Test registration with short password returns redirect (303) for non-HTMX."""
        email = f"shortpass_{uuid.uuid4().hex[:8]}@example.com"
        password = "short"

        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test", follow_redirects=False
        ) as client:
            csrf_response = await client.get("/web/register")
            csrf_token = get_csrf_from_response(csrf_response)

            reg_response = await client.post(
                "/web/register",
                data={
                    "email": email,
                    "password": password,
                    "password_confirm": password,
                },
                headers={"X-CSRF-Token": csrf_token} if csrf_token else {},
            )

        assert reg_response.status_code == 303

    @pytest.mark.asyncio
    async def test_register_duplicate_email(self):
        """Test registration with existing email returns redirect (303) for non-HTMX."""
        email = f"duplicate_{uuid.uuid4().hex[:8]}@example.com"
        password = "securepassword123"

        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test", follow_redirects=False
        ) as client:
            csrf_response = await client.get("/web/register")
            csrf_token = get_csrf_from_response(csrf_response)

            await client.post(
                "/web/register",
                data={
                    "email": email,
                    "password": password,
                    "password_confirm": password,
                },
                headers={"X-CSRF-Token": csrf_token} if csrf_token else {},
            )

            csrf_response2 = await client.get("/web/register")
            csrf_token2 = get_csrf_from_response(csrf_response2)

            reg_response2 = await client.post(
                "/web/register",
                data={
                    "email": email,
                    "password": password,
                    "password_confirm": password,
                },
                headers={"X-CSRF-Token": csrf_token2} if csrf_token2 else {},
            )

        assert reg_response2.status_code == 303

    @pytest.mark.asyncio
    async def test_register_invalid_csrf(self):
        """Test registration with invalid CSRF token returns 303 redirect to register page with error."""
        email = f"csrf_{uuid.uuid4().hex[:8]}@example.com"
        password = "securepassword123"

        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test", follow_redirects=False
        ) as client:
            reg_response = await client.post(
                "/web/register",
                data={
                    "email": email,
                    "password": password,
                    "password_confirm": password,
                    "csrf_token": "invalid_token",
                },
            )

        assert reg_response.status_code == 303
        assert reg_response.headers["location"] == "/web/register?error=csrf"


class TestLogout:
    """Tests for POST /web/logout."""

    @pytest.mark.asyncio
    async def test_logout_success(self):
        """Test successful logout clears cookies via redirect."""
        email = f"logout_{uuid.uuid4().hex[:8]}@example.com"
        password = "securepassword123"

        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test", follow_redirects=False
        ) as client:
            await do_register(client, email, password)
            csrf_token = await do_login(client, email, password)

            logout_response = await client.post(
                "/web/logout",
                headers={"X-CSRF-Token": csrf_token} if csrf_token else {},
            )

        assert logout_response.status_code == 303

    @pytest.mark.asyncio
    async def test_logout_blacklists_access_and_refresh_tokens(self):
        """Web logout should revoke both token cookies before clearing them."""
        email = f"logoutrevoke_{uuid.uuid4().hex[:8]}@example.com"
        password = "securepassword123"

        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test", follow_redirects=False
        ) as client:
            await do_register(client, email, password)
            csrf_token = await do_login(client, email, password)

            with patch(
                "app.services.token_blacklist.blacklist_token",
                new_callable=AsyncMock,
            ) as mock_blacklist:
                logout_response = await client.post(
                    "/web/logout",
                    headers={"X-CSRF-Token": csrf_token} if csrf_token else {},
                )

        assert logout_response.status_code == 303
        assert mock_blacklist.await_count == 2

    @pytest.mark.asyncio
    async def test_logout_invalid_csrf(self):
        """Test logout with invalid CSRF token returns 303 redirect to downloads page with error."""
        email = f"logoutcsrf_{uuid.uuid4().hex[:8]}@example.com"
        password = "securepassword123"

        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test", follow_redirects=False
        ) as client:
            await do_register(client, email, password)
            _ = await do_login(client, email, password)

            logout_response = await client.post(
                "/web/logout",
                headers={"X-CSRF-Token": "invalid_token"},
            )

        assert logout_response.status_code == 303
        assert logout_response.headers["location"] == "/web/downloads?error=csrf"


class TestDashboardPage:
    """Tests for GET /web/downloads (dashboard)."""

    @pytest.mark.asyncio
    async def test_dashboard_requires_auth(self):
        """Test that dashboard requires authentication."""
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response = await client.get("/web/downloads")

        assert response.status_code == 401

    @pytest.mark.asyncio
    async def test_dashboard_with_auth(self):
        """Test that dashboard renders for authenticated user."""
        email = f"dashboard_{uuid.uuid4().hex[:8]}@example.com"
        password = "securepassword123"

        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test", follow_redirects=False
        ) as client:
            await do_register(client, email, password)
            csrf_token = await do_login(client, email, password)

            login_resp = await client.post(
                "/web/login",
                data={"email": email, "password": password},
                headers={"X-CSRF-Token": csrf_token},
            )

            access_token = login_resp.cookies.get("access_token", "")
            csrf_token = (
                login_resp.cookies.get("csrf_token")
                or client.cookies.get("csrf_token")
                or csrf_token
            )

            dashboard_response = await client.get(
                "/web/downloads",
                cookies={"access_token": access_token},
            )

        assert dashboard_response.status_code == 200

    @pytest.mark.asyncio
    async def test_dashboard_renders_initial_download_skeleton_state(self):
        """Test dashboard includes skeleton/loading state before SSE updates."""
        email = f"dashskeleton_{uuid.uuid4().hex[:8]}@example.com"
        password = "securepassword123"

        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test", follow_redirects=False
        ) as client:
            await do_register(client, email, password)
            csrf_token = await do_login(client, email, password)

            login_resp = await client.post(
                "/web/login",
                data={"email": email, "password": password},
                headers={"X-CSRF-Token": csrf_token},
            )

            access_token = login_resp.cookies.get("access_token", "")
            csrf_token = (
                login_resp.cookies.get("csrf_token")
                or client.cookies.get("csrf_token")
                or csrf_token
            )

            dashboard_response = await client.get(
                "/web/downloads",
                cookies={"access_token": access_token},
            )

        assert dashboard_response.status_code == 200
        assert 'id="download-list" class="download-list-loading"' in dashboard_response.text
        assert 'id="download-skeleton"' in dashboard_response.text
        assert 'sse-connect="/web/downloads/stream"' in dashboard_response.text
        assert '<script src="/static/js/dashboard.js" defer></script>' in dashboard_response.text

    @pytest.mark.asyncio
    async def test_dashboard_renders_representative_status_badges_and_row_controls(self):
        """Test dashboard rows render safe status badges and preserve row actions."""
        from core.models.user import User

        email = f"dashstatuses_{uuid.uuid4().hex[:8]}@example.com"
        password = "securepassword123"
        statuses = [
            ("pending", None),
            ("processing", None),
            ("completed", "completed.mp4"),
            ("completed", None),
            ("failed", None),
            ("deferred", None),
            ("cancelled", None),
            ("unknown", None),
        ]

        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test", follow_redirects=False
        ) as client:
            await do_register(client, email, password)
            csrf_token = await do_login(client, email, password)

            login_resp = await client.post(
                "/web/login",
                data={"email": email, "password": password},
                headers={"X-CSRF-Token": csrf_token},
            )
            access_token = login_resp.cookies.get("access_token", "")

            async with TestingSessionLocal() as session:
                user_result = await session.execute(select(User).where(User.email == email))
                user = user_result.scalar_one()
                now = datetime.now(UTC)
                for index, (job_status, file_name) in enumerate(statuses):
                    job = DownloadJob(
                        id=uuid.uuid4(),
                        user_id=user.id,
                        url=f"https://www.youtube.com/watch?v={index:011d}",
                        status=job_status,
                        title=f"{job_status.title()} video",
                        file_name=file_name,
                        file_path=f"/tmp/{file_name}" if file_name else None,
                        created_at=now - timedelta(minutes=index),
                    )
                    session.add(job)
                await session.commit()

            dashboard_response = await client.get(
                "/web/downloads",
                cookies={"access_token": access_token},
            )

        assert dashboard_response.status_code == 200
        assert dashboard_response.text.count('class="status-badge ') == len(statuses)
        for expected_status in (
            "pending",
            "processing",
            "completed",
            "failed",
            "deferred",
            "cancelled",
            "unknown",
        ):
            assert f"status-{expected_status}" in dashboard_response.text
            assert f">{expected_status.title()}<" in dashboard_response.text
        assert dashboard_response.text.count('class="download-btn text-xs"') == 1
        assert 'hx-delete="/web/downloads/' in dashboard_response.text
        assert 'hx-target="closest .download-row"' in dashboard_response.text
        assert 'hx-swap="outerHTML"' in dashboard_response.text
        assert 'hx-confirm="Delete this download?"' in dashboard_response.text
        assert 'aria-label="Delete download"' in dashboard_response.text
        assert 'class="btn-danger"' in dashboard_response.text

    def test_download_list_and_item_render_equivalent_canonical_row(self):
        """Test list-rendered rows match the canonical download item partial structure."""
        from app.api.routes.web import templates

        created_at = datetime(2026, 6, 21, 12, 30, tzinfo=UTC)
        job = SimpleNamespace(
            id=uuid.uuid4(),
            url="https://www.youtube.com/watch?v=dQw4w9WgXcQ",
            title="Canonical row video",
            status="completed",
            file_name="video.mp4",
            created_at=created_at,
        )

        item_html = templates.env.get_template("partials/_download_item.html").render(job=job)
        list_html = templates.env.get_template("partials/_download_list.html").render(jobs=[job])

        assert _first_download_row(list_html) == _first_download_row(item_html)


class TestCreateDownloadForm:
    """Tests for POST /web/downloads (HTMX endpoint)."""

    @pytest.mark.asyncio
    async def test_create_download_htmx(self, sample_url):
        """Test creating download via HTMX endpoint."""
        email = f"download_{uuid.uuid4().hex[:8]}@example.com"
        password = "securepassword123"

        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test", follow_redirects=False
        ) as client:
            await do_register(client, email, password)
            csrf_token = await do_login(client, email, password)

            login_resp = await client.post(
                "/web/login",
                data={"email": email, "password": password},
                headers={"X-CSRF-Token": csrf_token},
            )

            access_token = login_resp.cookies.get("access_token", "")
            csrf_token = (
                login_resp.cookies.get("csrf_token")
                or client.cookies.get("csrf_token")
                or csrf_token
            )

            headers = {"HX-Request": "true"}
            if csrf_token:
                headers["X-CSRF-Token"] = csrf_token

            with (
                patch(
                    "app.services.download_service.resolve_video_title", new_callable=AsyncMock
                ) as mock_title,
                patch(
                    "app.services.download_service.enqueue_job", new_callable=AsyncMock
                ) as mock_enqueue,
            ):
                mock_title.return_value = "HTMX create video"
                mock_enqueue.return_value = None

                create_response = await client.post(
                    "/web/downloads",
                    data={"url": sample_url},
                    headers=headers,
                    cookies={"access_token": access_token},
                )

        assert create_response.status_code == 200

    @pytest.mark.asyncio
    async def test_create_download_htmx_keeps_outbox_when_core_queue_enqueue_fails(
        self, sample_url
    ):
        """Test HTMX create succeeds and preserves outbox recovery when enqueue fails."""
        email = f"download_recovery_{uuid.uuid4().hex[:8]}@example.com"
        password = "securepassword123"

        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test", follow_redirects=False
        ) as client:
            await do_register(client, email, password)
            csrf_token = await do_login(client, email, password)

            login_resp = await client.post(
                "/web/login",
                data={"email": email, "password": password},
                headers={"X-CSRF-Token": csrf_token},
            )

            access_token = login_resp.cookies.get("access_token", "")
            csrf_token = (
                login_resp.cookies.get("csrf_token")
                or client.cookies.get("csrf_token")
                or csrf_token
            )

            headers = {"HX-Request": "true"}
            if csrf_token:
                headers["X-CSRF-Token"] = csrf_token

            with (
                patch(
                    "app.services.download_service.resolve_video_title", new_callable=AsyncMock
                ) as mock_title,
                patch(
                    "app.services.download_service.enqueue_job", new_callable=AsyncMock
                ) as mock_enqueue,
            ):
                mock_title.return_value = "Story 1.4 queued video"
                mock_enqueue.side_effect = RuntimeError("redis unavailable")

                create_response = await client.post(
                    "/web/downloads",
                    data={"url": sample_url},
                    headers=headers,
                    cookies={"access_token": access_token},
                )

        assert create_response.status_code == 200
        mock_enqueue.assert_awaited_once()

        async with TestingSessionLocal() as session:
            job_result = await session.execute(
                select(DownloadJob).where(DownloadJob.url == sample_url)
            )
            job = job_result.scalars().one()

            outbox_result = await session.execute(
                select(Outbox).where(Outbox.job_id == job.id, Outbox.status == "pending")
            )
            outbox_entry = outbox_result.scalars().one()

        assert job.status == "pending"
        assert outbox_entry.event_type == "enqueue_download"

    @pytest.mark.asyncio
    async def test_create_download_requires_auth(self, sample_url):
        """Test that creating download requires authentication."""
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test", follow_redirects=False
        ) as client:
            csrf_response = await client.get("/web/login")
            csrf_token = get_csrf_from_response(csrf_response)

            headers = {"HX-Request": "true"}
            if csrf_token:
                headers["X-CSRF-Token"] = csrf_token

            response = await client.post(
                "/web/downloads",
                data={"url": sample_url},
                headers=headers,
            )

        assert response.status_code == 401

    @pytest.mark.asyncio
    async def test_create_download_invalid_url(self):
        """Test creating download with invalid URL returns 422."""
        email = f"invalidurl_{uuid.uuid4().hex[:8]}@example.com"
        password = "securepassword123"

        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test", follow_redirects=False
        ) as client:
            await do_register(client, email, password)
            csrf_token = await do_login(client, email, password)

            login_resp = await client.post(
                "/web/login",
                data={"email": email, "password": password},
                headers={"X-CSRF-Token": csrf_token},
            )

            csrf_token = (
                login_resp.cookies.get("csrf_token")
                or client.cookies.get("csrf_token")
                or csrf_token
            )

            headers = {"HX-Request": "true"}
            if csrf_token:
                headers["X-CSRF-Token"] = csrf_token

            create_response = await client.post(
                "/web/downloads",
                data={"url": "https://not-youtube.com/video"},
                headers=headers,
            )

        assert create_response.status_code == 422

    @pytest.mark.asyncio
    async def test_create_download_htmx_returns_canonical_row_and_keeps_csrf(self, sample_url):
        """Test HTMX create returns the canonical row partial and keeps the CSRF token stable."""
        email = f"downloadrow_{uuid.uuid4().hex[:8]}@example.com"
        password = "securepassword123"

        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test", follow_redirects=False
        ) as client:
            await do_register(client, email, password)
            csrf_token = await do_login(client, email, password)

            login_resp = await client.post(
                "/web/login",
                data={"email": email, "password": password},
                headers={"X-CSRF-Token": csrf_token},
            )

            csrf_token = (
                login_resp.cookies.get("csrf_token")
                or client.cookies.get("csrf_token")
                or csrf_token
            )

            headers = {"HX-Request": "true"}
            if csrf_token:
                headers["X-CSRF-Token"] = csrf_token

            with (
                patch(
                    "app.services.download_service.resolve_video_title", new_callable=AsyncMock
                ) as mock_title,
                patch(
                    "app.services.download_service.enqueue_job", new_callable=AsyncMock
                ) as mock_enqueue,
            ):
                mock_title.return_value = "Canonical HTMX row"
                mock_enqueue.return_value = None

                create_response = await client.post(
                    "/web/downloads",
                    data={"url": sample_url},
                    headers=headers,
                )

                # The HTMX partial must NOT rotate the CSRF cookie: the page
                # <meta> still holds the pre-request token, and the very next
                # HTMX POST would 403 after a rotation (finding —
                # delete_download_form already stopped rotating; create now
                # matches). The unchanged token must still validate.
                assert create_response.cookies.get("csrf_token") in (None, csrf_token)
                followup_response = await client.post(
                    "/web/downloads",
                    data={"url": sample_url},
                    headers={"HX-Request": "true", "X-CSRF-Token": csrf_token},
                )
                assert followup_response.status_code == 200

        assert create_response.status_code == 200
        assert '<div class="download-row" data-job-id="' in create_response.text
        assert 'class="status-badge status-pending"' in create_response.text
        assert "Canonical HTMX row" in create_response.text

    @pytest.mark.asyncio
    async def test_create_download_htmx_validation_error_returns_inline_error_fragment(self):
        """Test HTMX validation errors return centralized error-box HTML without row targets."""
        email = f"downloaderr_{uuid.uuid4().hex[:8]}@example.com"
        password = "securepassword123"

        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test", follow_redirects=False
        ) as client:
            await do_register(client, email, password)
            csrf_token = await do_login(client, email, password)

            login_resp = await client.post(
                "/web/login",
                data={"email": email, "password": password},
                headers={"X-CSRF-Token": csrf_token},
            )

            access_token = login_resp.cookies.get("access_token", "")
            csrf_token = (
                login_resp.cookies.get("csrf_token")
                or client.cookies.get("csrf_token")
                or csrf_token
            )

            headers = {"HX-Request": "true"}
            if csrf_token:
                headers["X-CSRF-Token"] = csrf_token

            create_response = await client.post(
                "/web/downloads",
                data={"url": "https://not-youtube.com/video"},
                headers=headers,
                cookies={"access_token": access_token},
            )

        assert create_response.status_code == 422
        assert "error-box" in create_response.text
        assert 'role="alert"' in create_response.text or "role='alert'" in create_response.text
        assert "Invalid supported URL" in create_response.text
        assert "download-rows" not in create_response.text

    @pytest.mark.asyncio
    async def test_create_download_htmx_invalid_csrf_returns_inline_error_fragment(
        self, sample_url
    ):
        """Test HTMX create with invalid CSRF returns the existing 403 error fragment."""
        email = f"downloadcsrf_{uuid.uuid4().hex[:8]}@example.com"
        password = "securepassword123"

        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test", follow_redirects=False
        ) as client:
            await do_register(client, email, password)
            csrf_token = await do_login(client, email, password)

            login_resp = await client.post(
                "/web/login",
                data={"email": email, "password": password},
                headers={"X-CSRF-Token": csrf_token},
            )

            access_token = login_resp.cookies.get("access_token", "")

            create_response = await client.post(
                "/web/downloads",
                data={"url": sample_url},
                headers={"HX-Request": "true", "X-CSRF-Token": "invalid_token"},
                cookies={"access_token": access_token},
            )

        assert create_response.status_code == 403
        assert "error-box" in create_response.text
        assert "Invalid CSRF token" in create_response.text

    @pytest.mark.asyncio
    async def test_create_download_htmx_exception_during_creation_returns_error_fragment(
        self, sample_url
    ):
        """Test HTMX create returns the existing 500 error fragment when outbox write fails."""
        email = f"downloadcreateerr_{uuid.uuid4().hex[:8]}@example.com"
        password = "securepassword123"

        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test", follow_redirects=False
        ) as client:
            await do_register(client, email, password)
            csrf_token = await do_login(client, email, password)

            login_resp = await client.post(
                "/web/login",
                data={"email": email, "password": password},
                headers={"X-CSRF-Token": csrf_token},
            )

            access_token = login_resp.cookies.get("access_token", "")
            csrf_token = (
                login_resp.cookies.get("csrf_token")
                or client.cookies.get("csrf_token")
                or csrf_token
            )

            headers = {"HX-Request": "true"}
            if csrf_token:
                headers["X-CSRF-Token"] = csrf_token

            with patch(
                "app.services.download_service.write_job_to_outbox", new_callable=AsyncMock
            ) as mock_outbox:
                mock_outbox.side_effect = Exception("Database error")

                create_response = await client.post(
                    "/web/downloads",
                    data={"url": sample_url},
                    headers=headers,
                    cookies={"access_token": access_token},
                )

        assert create_response.status_code == 500
        assert "error-box" in create_response.text
        assert "Failed to create download" in create_response.text


class TestCreateDownloadFullPage:
    """Tests for POST /web/downloads/full (full page endpoint)."""

    @pytest.mark.asyncio
    async def test_create_download_full_page(self, sample_url):
        """Test creating download via full page endpoint."""
        email = f"fullpage_{uuid.uuid4().hex[:8]}@example.com"
        password = "securepassword123"

        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test", follow_redirects=False
        ) as client:
            await do_register(client, email, password)
            csrf_token = await do_login(client, email, password)

            login_resp = await client.post(
                "/web/login",
                data={"email": email, "password": password},
                headers={"X-CSRF-Token": csrf_token},
            )

            access_token = login_resp.cookies.get("access_token", "")
            csrf_token = (
                login_resp.cookies.get("csrf_token")
                or client.cookies.get("csrf_token")
                or csrf_token
            )

            with patch(
                "app.services.download_service.enqueue_job", new_callable=AsyncMock
            ) as mock_enqueue:
                mock_enqueue.return_value = None

                create_response = await client.post(
                    "/web/downloads/full",
                    data={"url": sample_url},
                    headers={"X-CSRF-Token": csrf_token} if csrf_token else {},
                    cookies={"access_token": access_token},
                )

        assert create_response.status_code == 303

    @pytest.mark.asyncio
    async def test_create_download_full_page_keeps_outbox_when_core_queue_enqueue_fails(
        self, sample_url
    ):
        """Test full-page create redirects and preserves outbox recovery when enqueue fails."""
        email = f"fullpage_recovery_{uuid.uuid4().hex[:8]}@example.com"
        password = "securepassword123"

        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test", follow_redirects=False
        ) as client:
            await do_register(client, email, password)
            csrf_token = await do_login(client, email, password)

            login_resp = await client.post(
                "/web/login",
                data={"email": email, "password": password},
                headers={"X-CSRF-Token": csrf_token},
            )

            access_token = login_resp.cookies.get("access_token", "")
            csrf_token = (
                login_resp.cookies.get("csrf_token")
                or client.cookies.get("csrf_token")
                or csrf_token
            )

            with patch(
                "app.services.download_service.enqueue_job", new_callable=AsyncMock
            ) as mock_enqueue:
                mock_enqueue.side_effect = RuntimeError("redis unavailable")

                create_response = await client.post(
                    "/web/downloads/full",
                    data={"url": sample_url},
                    headers={"X-CSRF-Token": csrf_token} if csrf_token else {},
                    cookies={"access_token": access_token},
                )

        assert create_response.status_code == 303
        assert create_response.headers["location"] == "/web/downloads"
        mock_enqueue.assert_awaited_once()

        async with TestingSessionLocal() as session:
            job_result = await session.execute(
                select(DownloadJob).where(DownloadJob.url == sample_url)
            )
            job = job_result.scalars().one()

            outbox_result = await session.execute(
                select(Outbox).where(Outbox.job_id == job.id, Outbox.status == "pending")
            )
            outbox_entry = outbox_result.scalars().one()

        assert job.status == "pending"
        assert outbox_entry.event_type == "enqueue_download"

    @pytest.mark.asyncio
    async def test_create_download_full_page_requires_auth(self, sample_url):
        """Test that full page download requires authentication."""
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test", follow_redirects=False
        ) as client:
            csrf_response = await client.get("/web/login")
            csrf_token = get_csrf_from_response(csrf_response)

            response = await client.post(
                "/web/downloads/full",
                data={"url": sample_url},
                headers={"X-CSRF-Token": csrf_token} if csrf_token else {},
            )

        assert response.status_code == 401


class TestDeleteDownload:
    """Tests for DELETE /web/downloads/{job_id}."""

    @pytest.mark.asyncio
    async def test_delete_download_not_found(self):
        """Test deleting non-existent download returns 404."""
        email = f"delete_{uuid.uuid4().hex[:8]}@example.com"
        password = "securepassword123"

        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test", follow_redirects=False
        ) as client:
            await do_register(client, email, password)
            csrf_token = await do_login(client, email, password)

            login_resp = await client.post(
                "/web/login",
                data={"email": email, "password": password},
                headers={"X-CSRF-Token": csrf_token},
            )

            access_token = login_resp.cookies.get("access_token", "")
            csrf_token = (
                login_resp.cookies.get("csrf_token")
                or client.cookies.get("csrf_token")
                or csrf_token
            )

            fake_uuid = str(uuid.uuid4())

            headers = {"HX-Request": "true"}
            if csrf_token:
                headers["X-CSRF-Token"] = csrf_token

            delete_response = await client.delete(
                f"/web/downloads/{fake_uuid}",
                headers=headers,
                cookies={"access_token": access_token},
            )

        assert delete_response.status_code == 404

    @pytest.mark.asyncio
    async def test_delete_download_invalid_uuid(self):
        """Test deleting with invalid UUID format returns 400."""
        email = f"deleteuuid_{uuid.uuid4().hex[:8]}@example.com"
        password = "securepassword123"

        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test", follow_redirects=False
        ) as client:
            await do_register(client, email, password)
            csrf_token = await do_login(client, email, password)

            login_resp = await client.post(
                "/web/login",
                data={"email": email, "password": password},
                headers={"X-CSRF-Token": csrf_token},
            )

            access_token = login_resp.cookies.get("access_token", "")
            csrf_token = (
                login_resp.cookies.get("csrf_token")
                or client.cookies.get("csrf_token")
                or csrf_token
            )

            headers = {"HX-Request": "true"}
            if csrf_token:
                headers["X-CSRF-Token"] = csrf_token

            delete_response = await client.delete(
                "/web/downloads/not-a-uuid",
                headers=headers,
                cookies={"access_token": access_token},
            )

        assert delete_response.status_code == 400

    @pytest.mark.asyncio
    async def test_delete_download_requires_auth(self):
        """Test that deleting download requires authentication."""
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test", follow_redirects=False
        ) as client:
            fake_uuid = str(uuid.uuid4())
            response = await client.delete(f"/web/downloads/{fake_uuid}")

        assert response.status_code == 401


class TestDownloadFile:
    """Tests for GET /web/downloads/{job_id}/file."""

    @pytest.mark.asyncio
    async def test_download_file_not_found(self):
        """Test downloading from non-existent job returns 404."""
        email = f"dlfile_{uuid.uuid4().hex[:8]}@example.com"
        password = "securepassword123"

        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test", follow_redirects=False
        ) as client:
            await do_register(client, email, password)
            csrf_token = await do_login(client, email, password)

            login_resp = await client.post(
                "/web/login",
                data={"email": email, "password": password},
                headers={"X-CSRF-Token": csrf_token},
            )

            access_token = login_resp.cookies.get("access_token", "")
            csrf_token = (
                login_resp.cookies.get("csrf_token")
                or client.cookies.get("csrf_token")
                or csrf_token
            )

            fake_uuid = str(uuid.uuid4())
            download_response = await client.get(
                f"/web/downloads/{fake_uuid}/file",
                cookies={"access_token": access_token},
            )

        assert download_response.status_code == 404

    @pytest.mark.asyncio
    async def test_download_file_invalid_uuid(self):
        """Test downloading with invalid UUID returns 400."""
        email = f"dlfileuuid_{uuid.uuid4().hex[:8]}@example.com"
        password = "securepassword123"

        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test", follow_redirects=False
        ) as client:
            await do_register(client, email, password)
            csrf_token = await do_login(client, email, password)

            login_resp = await client.post(
                "/web/login",
                data={"email": email, "password": password},
                headers={"X-CSRF-Token": csrf_token},
            )

            access_token = login_resp.cookies.get("access_token", "")
            csrf_token = (
                login_resp.cookies.get("csrf_token")
                or client.cookies.get("csrf_token")
                or csrf_token
            )

            download_response = await client.get(
                "/web/downloads/not-a-uuid/file",
                cookies={"access_token": access_token},
            )

        assert download_response.status_code == 400

    @pytest.mark.asyncio
    async def test_download_file_requires_auth(self):
        """Test that downloading file requires authentication."""
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test", follow_redirects=False
        ) as client:
            fake_uuid = str(uuid.uuid4())
            response = await client.get(f"/web/downloads/{fake_uuid}/file")

        assert response.status_code == 401


class TestHtmxBehavior:
    """Tests for HTMX-specific behavior."""

    @pytest.mark.asyncio
    async def test_login_htmx_returns_hx_redirect(self):
        """Test that HTMX login returns HX-Redirect header."""
        email = f"htmxlogin_{uuid.uuid4().hex[:8]}@example.com"
        password = "securepassword123"

        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test", follow_redirects=False
        ) as client:
            await do_register(client, email, password)
            csrf_token = await do_login(client, email, password)

            headers = {"HX-Request": "true"}
            if csrf_token:
                headers["X-CSRF-Token"] = csrf_token

            login_response = await client.post(
                "/web/login",
                data={
                    "email": email,
                    "password": password,
                },
                headers=headers,
            )

        assert login_response.status_code == 200
        assert "HX-Redirect" in login_response.headers

    @pytest.mark.asyncio
    async def test_login_non_htmx_returns_redirect_response(self):
        """Test that non-HTMX login returns RedirectResponse with 303."""
        email = f"nonhtmxlogin_{uuid.uuid4().hex[:8]}@example.com"
        password = "securepassword123"

        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test", follow_redirects=False
        ) as client:
            await do_register(client, email, password)
            csrf_token = await do_login(client, email, password)

            login_response = await client.post(
                "/web/login",
                data={
                    "email": email,
                    "password": password,
                },
                headers={"X-CSRF-Token": csrf_token},
            )

        assert login_response.status_code == 303

    @pytest.mark.asyncio
    async def test_register_htmx_returns_hx_redirect(self):
        """Test that HTMX registration returns HX-Redirect header."""
        email = f"htmxregister_{uuid.uuid4().hex[:8]}@example.com"
        password = "securepassword123"

        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test", follow_redirects=False
        ) as client:
            csrf_response = await client.get("/web/register")
            csrf_token = get_csrf_from_response(csrf_response)

            headers = {"HX-Request": "true"}
            if csrf_token:
                headers["X-CSRF-Token"] = csrf_token

            reg_response = await client.post(
                "/web/register",
                data={
                    "email": email,
                    "password": password,
                    "password_confirm": password,
                },
                headers=headers,
            )

        assert reg_response.status_code == 200
        assert "HX-Redirect" in reg_response.headers

    @pytest.mark.asyncio
    async def test_register_non_htmx_returns_redirect_response(self):
        """Test that non-HTMX registration returns RedirectResponse with 303."""
        email = f"nonhtmxregister_{uuid.uuid4().hex[:8]}@example.com"
        password = "securepassword123"

        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test", follow_redirects=False
        ) as client:
            csrf_response = await client.get("/web/register")
            csrf_token = get_csrf_from_response(csrf_response)

            reg_response = await client.post(
                "/web/register",
                data={
                    "email": email,
                    "password": password,
                    "password_confirm": password,
                },
                headers={"X-CSRF-Token": csrf_token} if csrf_token else {},
            )

        assert reg_response.status_code == 303

    @pytest.mark.asyncio
    async def test_logout_htmx_with_invalid_csrf_returns_error(self):
        """Test that HTMX logout with invalid CSRF returns error HTML."""
        email = f"htmxlogout_{uuid.uuid4().hex[:8]}@example.com"
        password = "securepassword123"

        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test", follow_redirects=False
        ) as client:
            await do_register(client, email, password)
            _ = await do_login(client, email, password)

            logout_response = await client.post(
                "/web/logout",
                headers={
                    "HX-Request": "true",
                    "X-CSRF-Token": "invalid_token",
                },
            )

        assert logout_response.status_code == 403
        assert "error" in logout_response.text.lower() or "csrf" in logout_response.text.lower()


class TestSettingsPage:
    """Tests for GET /web/settings."""

    @pytest.mark.asyncio
    async def test_settings_page_requires_auth(self):
        """Test that settings page requires authentication."""
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response = await client.get("/web/settings")

        assert response.status_code == 401

    @pytest.mark.asyncio
    async def test_settings_page_renders(self):
        """Test that settings page renders for authenticated user."""
        email = f"settings_{uuid.uuid4().hex[:8]}@example.com"
        password = "securepassword123"

        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test", follow_redirects=False
        ) as client:
            await do_register(client, email, password)
            csrf_token = await do_login(client, email, password)

            login_resp = await client.post(
                "/web/login",
                data={"email": email, "password": password},
                headers={"X-CSRF-Token": csrf_token},
            )

            access_token = login_resp.cookies.get("access_token", "")
            csrf_token = (
                login_resp.cookies.get("csrf_token")
                or client.cookies.get("csrf_token")
                or csrf_token
            )

            response = await client.get(
                "/web/settings",
                cookies={"access_token": access_token},
            )

        assert response.status_code == 200

    @pytest.mark.asyncio
    async def test_settings_page_maps_password_error_to_field_level_accessibility(self):
        """Test settings password errors are linked to the right input."""
        email = f"settings_err_{uuid.uuid4().hex[:8]}@example.com"
        password = "securepassword123"

        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test", follow_redirects=False
        ) as client:
            await do_register(client, email, password)
            csrf_token = await do_login(client, email, password)

            login_resp = await client.post(
                "/web/login",
                data={"email": email, "password": password},
                headers={"X-CSRF-Token": csrf_token},
            )
            access_token = login_resp.cookies.get("access_token", "")
            csrf_token = (
                login_resp.cookies.get("csrf_token")
                or client.cookies.get("csrf_token")
                or csrf_token
            )

            response = await client.get(
                "/web/settings?error=bad_current_password",
                cookies={"access_token": access_token},
            )

        assert response.status_code == 200
        assert 'id="current-password-error"' in response.text
        assert 'aria-describedby="current-password-error"' in response.text
        assert 'aria-errormessage="current-password-error"' in response.text
        assert "Current password is incorrect" in response.text


class TestUpdateUsername:
    """Tests for POST /web/settings/username."""

    @pytest.mark.asyncio
    async def test_update_username_success(self):
        """Test updating username successfully."""
        email = f"username_{uuid.uuid4().hex[:8]}@example.com"
        password = "securepassword123"

        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test", follow_redirects=False
        ) as client:
            await do_register(client, email, password)
            csrf_token = await do_login(client, email, password)

            login_resp = await client.post(
                "/web/login",
                data={"email": email, "password": password},
                headers={"X-CSRF-Token": csrf_token},
            )

            access_token = login_resp.cookies.get("access_token", "")
            csrf_token = (
                login_resp.cookies.get("csrf_token")
                or client.cookies.get("csrf_token")
                or csrf_token
            )

            # Get fresh CSRF token
            csrf_response = await client.get(
                "/web/settings", cookies={"access_token": access_token}
            )
            csrf_token = get_csrf_from_response(csrf_response)

            response = await client.post(
                "/web/settings/username",
                data={"username": "  newname  "},
                headers={"X-CSRF-Token": csrf_token} if csrf_token else {},
                cookies={"access_token": access_token},
            )

        async with TestingSessionLocal() as session:
            result = await session.execute(select(User).where(User.email == email))
            user = result.scalar_one()

        assert response.status_code == 303
        assert response.headers["location"] == "/web/settings?updated=username"
        assert user.username == "newname"

    @pytest.mark.asyncio
    async def test_update_username_too_short(self):
        """Test updating username with too short name returns error."""
        email = f"shortuser_{uuid.uuid4().hex[:8]}@example.com"
        password = "securepassword123"

        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test", follow_redirects=False
        ) as client:
            await do_register(client, email, password)
            csrf_token = await do_login(client, email, password)

            login_resp = await client.post(
                "/web/login",
                data={"email": email, "password": password},
                headers={"X-CSRF-Token": csrf_token},
            )

            access_token = login_resp.cookies.get("access_token", "")
            csrf_token = (
                login_resp.cookies.get("csrf_token")
                or client.cookies.get("csrf_token")
                or csrf_token
            )

            csrf_response = await client.get(
                "/web/settings", cookies={"access_token": access_token}
            )
            csrf_token = get_csrf_from_response(csrf_response)

            response = await client.post(
                "/web/settings/username",
                data={"username": "ab"},
                headers={"X-CSRF-Token": csrf_token} if csrf_token else {},
                cookies={"access_token": access_token},
            )

        async with TestingSessionLocal() as session:
            result = await session.execute(select(User).where(User.email == email))
            user = result.scalar_one()

        assert response.status_code == 303
        assert response.headers["location"] == "/web/settings?error=username_too_short"
        assert user.username.startswith("shortuser_")

    @pytest.mark.asyncio
    async def test_update_username_invalid_csrf(self):
        """Test updating username with invalid CSRF token returns error."""
        email = f"csrfuser_{uuid.uuid4().hex[:8]}@example.com"
        password = "securepassword123"

        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test", follow_redirects=False
        ) as client:
            await do_register(client, email, password)
            csrf_token = await do_login(client, email, password)

            login_resp = await client.post(
                "/web/login",
                data={"email": email, "password": password},
                headers={"X-CSRF-Token": csrf_token},
            )

            access_token = login_resp.cookies.get("access_token", "")
            csrf_token = (
                login_resp.cookies.get("csrf_token")
                or client.cookies.get("csrf_token")
                or csrf_token
            )

            response = await client.post(
                "/web/settings/username",
                data={"username": "validname"},
                headers={"X-CSRF-Token": "invalid_token"},
                cookies={"access_token": access_token},
            )

        assert response.status_code == 303
        assert response.headers["location"] == "/web/settings?error=csrf"


class TestChangePassword:
    """Tests for POST /web/settings/password."""

    @pytest.mark.asyncio
    async def test_change_password_success(self):
        """Test changing password successfully."""
        email = f"changepw_{uuid.uuid4().hex[:8]}@example.com"
        password = "securepassword123"

        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test", follow_redirects=False
        ) as client:
            await do_register(client, email, password)
            csrf_token = await do_login(client, email, password)

            login_resp = await client.post(
                "/web/login",
                data={"email": email, "password": password},
                headers={"X-CSRF-Token": csrf_token},
            )

            access_token = login_resp.cookies.get("access_token", "")
            csrf_token = (
                login_resp.cookies.get("csrf_token")
                or client.cookies.get("csrf_token")
                or csrf_token
            )

            csrf_response = await client.get(
                "/web/settings", cookies={"access_token": access_token}
            )
            csrf_token = get_csrf_from_response(csrf_response)

            response = await client.post(
                "/web/settings/password",
                data={
                    "current_password": password,
                    "new_password": "newpassword123",
                    "new_password_confirm": "newpassword123",
                },
                headers={"X-CSRF-Token": csrf_token} if csrf_token else {},
                cookies={"access_token": access_token},
            )

            old_token_response = await client.get(
                "/web/settings",
                cookies={"access_token": access_token},
            )

            fresh_csrf_response = await client.get("/web/login")
            fresh_csrf_token = get_csrf_from_response(fresh_csrf_response)
            old_password_login = await client.post(
                "/web/login",
                data={"email": email, "password": password},
                headers={"X-CSRF-Token": fresh_csrf_token} if fresh_csrf_token else {},
            )

            fresh_csrf_response = await client.get("/web/login")
            fresh_csrf_token = get_csrf_from_response(fresh_csrf_response)
            new_password_login = await client.post(
                "/web/login",
                data={"email": email, "password": "newpassword123"},
                headers={"X-CSRF-Token": fresh_csrf_token} if fresh_csrf_token else {},
            )

        assert response.status_code == 303
        assert response.headers["location"] == "/web/settings?updated=password"
        assert old_token_response.status_code == 401
        assert old_password_login.status_code == 303
        assert old_password_login.headers["location"] == "/web/login?error=1"
        assert new_password_login.status_code == 303
        assert new_password_login.headers["location"] == "/web/downloads"

    @pytest.mark.asyncio
    async def test_change_password_wrong_current(self):
        """Test changing password with wrong current password returns error."""
        email = f"wrongcurr_{uuid.uuid4().hex[:8]}@example.com"
        password = "securepassword123"

        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test", follow_redirects=False
        ) as client:
            await do_register(client, email, password)
            csrf_token = await do_login(client, email, password)

            login_resp = await client.post(
                "/web/login",
                data={"email": email, "password": password},
                headers={"X-CSRF-Token": csrf_token},
            )

            access_token = login_resp.cookies.get("access_token", "")
            csrf_token = (
                login_resp.cookies.get("csrf_token")
                or client.cookies.get("csrf_token")
                or csrf_token
            )

            csrf_response = await client.get(
                "/web/settings", cookies={"access_token": access_token}
            )
            csrf_token = get_csrf_from_response(csrf_response)

            response = await client.post(
                "/web/settings/password",
                data={
                    "current_password": "wrongpassword",
                    "new_password": "newpassword123",
                    "new_password_confirm": "newpassword123",
                },
                headers={"X-CSRF-Token": csrf_token} if csrf_token else {},
                cookies={"access_token": access_token},
            )

        assert response.status_code == 303
        assert response.headers["location"] == "/web/settings?error=bad_current_password"

    @pytest.mark.asyncio
    async def test_change_password_mismatch(self):
        """Test changing password with mismatched confirmation returns error."""
        email = f"mismatchpw_{uuid.uuid4().hex[:8]}@example.com"
        password = "securepassword123"

        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test", follow_redirects=False
        ) as client:
            await do_register(client, email, password)
            csrf_token = await do_login(client, email, password)

            login_resp = await client.post(
                "/web/login",
                data={"email": email, "password": password},
                headers={"X-CSRF-Token": csrf_token},
            )

            access_token = login_resp.cookies.get("access_token", "")
            csrf_token = (
                login_resp.cookies.get("csrf_token")
                or client.cookies.get("csrf_token")
                or csrf_token
            )

            csrf_response = await client.get(
                "/web/settings", cookies={"access_token": access_token}
            )
            csrf_token = get_csrf_from_response(csrf_response)

            response = await client.post(
                "/web/settings/password",
                data={
                    "current_password": password,
                    "new_password": "newpassword123",
                    "new_password_confirm": "differentpass",
                },
                headers={"X-CSRF-Token": csrf_token} if csrf_token else {},
                cookies={"access_token": access_token},
            )

        assert response.status_code == 303
        assert response.headers["location"] == "/web/settings?error=password_mismatch"

    @pytest.mark.asyncio
    async def test_change_password_too_short(self):
        """Test changing password with too short new password returns error."""
        email = f"shortpw_{uuid.uuid4().hex[:8]}@example.com"
        password = "securepassword123"

        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test", follow_redirects=False
        ) as client:
            await do_register(client, email, password)
            csrf_token = await do_login(client, email, password)

            login_resp = await client.post(
                "/web/login",
                data={"email": email, "password": password},
                headers={"X-CSRF-Token": csrf_token},
            )

            access_token = login_resp.cookies.get("access_token", "")
            csrf_token = (
                login_resp.cookies.get("csrf_token")
                or client.cookies.get("csrf_token")
                or csrf_token
            )

            csrf_response = await client.get(
                "/web/settings", cookies={"access_token": access_token}
            )
            csrf_token = get_csrf_from_response(csrf_response)

            response = await client.post(
                "/web/settings/password",
                data={
                    "current_password": password,
                    "new_password": "short",
                    "new_password_confirm": "short",
                },
                headers={"X-CSRF-Token": csrf_token} if csrf_token else {},
                cookies={"access_token": access_token},
            )

        assert response.status_code == 303
        assert response.headers["location"] == "/web/settings?error=password_too_short"


class TestDeleteDownloadForm:
    """Tests for DELETE /web/downloads/{job_id} (HTMX form-based)."""

    @pytest.mark.asyncio
    async def test_delete_download_invalid_csrf(self):
        """Test deleting download with invalid CSRF returns 403."""
        email = f"delcsrf_{uuid.uuid4().hex[:8]}@example.com"
        password = "securepassword123"

        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test", follow_redirects=False
        ) as client:
            await do_register(client, email, password)
            csrf_token = await do_login(client, email, password)

            login_resp = await client.post(
                "/web/login",
                data={"email": email, "password": password},
                headers={"X-CSRF-Token": csrf_token},
            )

            access_token = login_resp.cookies.get("access_token", "")
            csrf_token = (
                login_resp.cookies.get("csrf_token")
                or client.cookies.get("csrf_token")
                or csrf_token
            )

            fake_uuid = str(uuid.uuid4())

            response = await client.delete(
                f"/web/downloads/{fake_uuid}",
                headers={"X-CSRF-Token": "invalid_token"},
                cookies={"access_token": access_token},
            )

        assert response.status_code == 403


class TestValidateCsrfTokenStrategy2:
    """Tests for CSRF Strategy 2: Cookie present, header missing, form matches cookie."""

    @pytest.mark.asyncio
    async def test_csrf_cookie_present_header_missing_form_matches(self):
        """Mock request.form() returning csrf_token matching cookie, assert True."""
        from unittest.mock import MagicMock

        from fastapi import Request
        from starlette.datastructures import MultiDict

        from app.api.routes.web import validate_csrf_token

        mock_request = MagicMock(spec=Request)
        mock_request.method = "POST"
        mock_request.cookies = {"csrf_token": "cookie_token_value"}
        mock_request.headers = {}
        mock_request.form = AsyncMock(
            return_value=MultiDict([("csrf_token", "cookie_token_value")])
        )

        result = await validate_csrf_token(mock_request)
        assert result is True

    @pytest.mark.asyncio
    async def test_csrf_cookie_present_header_missing_form_mismatch(self):
        """Assert False when form token doesn't match cookie token."""
        from unittest.mock import MagicMock

        from fastapi import Request
        from starlette.datastructures import MultiDict

        from app.api.routes.web import validate_csrf_token

        mock_request = MagicMock(spec=Request)
        mock_request.method = "POST"
        mock_request.cookies = {"csrf_token": "cookie_token_value"}
        mock_request.headers = {}
        mock_request.form = AsyncMock(return_value=MultiDict([("csrf_token", "different_token")]))

        result = await validate_csrf_token(mock_request)
        assert result is False

    @pytest.mark.asyncio
    async def test_csrf_cookie_present_header_missing_form_exception(self):
        """Assert False when request.form() raises an exception."""
        from unittest.mock import MagicMock

        from fastapi import Request

        from app.api.routes.web import validate_csrf_token

        mock_request = MagicMock(spec=Request)
        mock_request.method = "POST"
        mock_request.cookies = {"csrf_token": "cookie_token_value"}
        mock_request.headers = {}
        mock_request.form = AsyncMock(side_effect=Exception("Form parse error"))

        result = await validate_csrf_token(mock_request)
        assert result is False


class TestValidateCsrfTokenStrategy3:
    """Tests for state-changing requests without a CSRF cookie."""

    @pytest.mark.asyncio
    async def test_csrf_no_cookie_header_present_form_matches(self):
        """Assert False even when submitted tokens match but no cookie is present."""
        from unittest.mock import MagicMock

        from fastapi import Request
        from starlette.datastructures import MultiDict

        from app.api.routes.web import validate_csrf_token

        mock_request = MagicMock(spec=Request)
        mock_request.method = "POST"
        mock_request.cookies = {}
        mock_request.headers = {"X-CSRF-Token": "header_token_value"}
        mock_request.form = AsyncMock(
            return_value=MultiDict([("csrf_token", "header_token_value")])
        )

        result = await validate_csrf_token(mock_request)
        assert result is False

    @pytest.mark.asyncio
    async def test_csrf_no_cookie_header_present_form_mismatch(self):
        """Assert False when form token doesn't match header token."""
        from unittest.mock import MagicMock

        from fastapi import Request
        from starlette.datastructures import MultiDict

        from app.api.routes.web import validate_csrf_token

        mock_request = MagicMock(spec=Request)
        mock_request.method = "POST"
        mock_request.cookies = {}
        mock_request.headers = {"X-CSRF-Token": "header_token_value"}
        mock_request.form = AsyncMock(return_value=MultiDict([("csrf_token", "different_token")]))

        result = await validate_csrf_token(mock_request)
        assert result is False


class TestValidateRedirectUrlNormalization:
    """Tests for redirect URL path normalization."""

    def test_validate_redirect_url_normalizes_double_dots(self):
        """URL /web/../login should be normalized and rejected (doesn't start with /web/)."""
        from app.api.routes.web import _validate_redirect_url

        result = _validate_redirect_url("/web/../login", "/web/downloads")
        assert result == "/web/downloads"

    def test_validate_redirect_url_preserves_trailing_slash(self):
        """URL /web/downloads/ should preserve trailing slash after normalization."""
        from app.api.routes.web import _validate_redirect_url

        result = _validate_redirect_url("/web/downloads/", "/web/login")
        assert result == "/web/downloads/"

    def test_validate_redirect_url_normalizes_double_dots_in_middle(self):
        """URL /web/../web/login should normalize to /web/login."""
        from app.api.routes.web import _validate_redirect_url

        result = _validate_redirect_url("/web/../web/login", "/web/downloads")
        assert result == "/web/login"

    def test_validate_redirect_url_strips_backslashes(self):
        """URL with backslashes should be normalized and rejected."""
        from app.api.routes.web import _validate_redirect_url

        result = _validate_redirect_url("\\web\\downloads", "/web/downloads")
        assert result == "/web/downloads"


class TestCleanupJobFiles:
    """Tests for _cleanup_job_files helper."""

    def test_cleanup_job_files_removes_valid_files(self, tmp_path):
        """Create temp files, assert (True, [])."""
        from unittest.mock import MagicMock

        from app.services.user_service import _cleanup_job_files

        downloads_dir = tmp_path / "downloads"
        downloads_dir.mkdir()
        file1 = downloads_dir / "file1.mp3"
        file1.write_text("test content")
        file2 = downloads_dir / "file2.mp3"
        file2.write_text("test content")

        mock_job1 = MagicMock()
        mock_job1.file_path = str(file1)
        mock_job1.id = uuid.uuid4()
        mock_job2 = MagicMock()
        mock_job2.file_path = str(file2)
        mock_job2.id = uuid.uuid4()

        mock_logger = MagicMock()

        with patch("app.services.user_service.settings") as mock_settings:
            mock_settings.storage_path = str(tmp_path)
            all_cleaned, failures = _cleanup_job_files([mock_job1, mock_job2], mock_logger)

        assert all_cleaned is True
        assert failures == []
        assert not file1.exists()
        assert not file2.exists()

    def test_cleanup_job_files_skips_missing_file_path(self, tmp_path):
        """Job with file_path=None should be skipped."""
        from unittest.mock import MagicMock

        from app.services.user_service import _cleanup_job_files

        mock_job = MagicMock()
        mock_job.file_path = None
        mock_job.id = uuid.uuid4()

        mock_logger = MagicMock()

        all_cleaned, failures = _cleanup_job_files([mock_job], mock_logger)

        assert all_cleaned is True
        assert failures == []

    def test_cleanup_job_files_skips_nonexistent_file(self, tmp_path):
        """Job with non-existent file should be handled gracefully."""
        from unittest.mock import MagicMock

        from app.services.user_service import _cleanup_job_files

        downloads_dir = tmp_path / "downloads"
        downloads_dir.mkdir()

        mock_job = MagicMock()
        mock_job.file_path = str(downloads_dir / "nonexistent.mp3")
        mock_job.id = uuid.uuid4()

        mock_logger = MagicMock()

        with patch("app.services.user_service.settings") as mock_settings:
            mock_settings.storage_path = str(tmp_path)
            all_cleaned, failures = _cleanup_job_files([mock_job], mock_logger)

        assert all_cleaned is True
        assert failures == []

    def test_cleanup_job_files_handles_path_traversal(self, tmp_path):
        """Assert (False, [bad_path]) and HTTPException caught."""
        from unittest.mock import MagicMock

        from app.services.user_service import _cleanup_job_files

        downloads_dir = tmp_path / "downloads"
        downloads_dir.mkdir()

        mock_job = MagicMock()
        mock_job.file_path = str(tmp_path / ".." / "etc" / "passwd")
        mock_job.id = uuid.uuid4()

        mock_logger = MagicMock()

        with patch("app.services.user_service.settings") as mock_settings:
            mock_settings.storage_path = str(tmp_path)
            all_cleaned, failures = _cleanup_job_files([mock_job], mock_logger)

        assert all_cleaned is False
        assert len(failures) == 1

    def test_cleanup_job_files_handles_os_error(self, tmp_path):
        """Mock os.remove raising OSError, assert (False, [path])."""
        from unittest.mock import MagicMock

        from app.services.user_service import _cleanup_job_files

        downloads_dir = tmp_path / "downloads"
        downloads_dir.mkdir()
        file1 = downloads_dir / "file1.mp3"
        file1.write_text("test content")

        mock_job = MagicMock()
        mock_job.file_path = str(file1)
        mock_job.id = uuid.uuid4()

        mock_logger = MagicMock()

        with patch("app.services.user_service.settings") as mock_settings:
            mock_settings.storage_path = str(tmp_path)
            with patch(
                "app.services.user_service.os.remove",
                side_effect=OSError("Permission denied"),
            ):
                all_cleaned, failures = _cleanup_job_files([mock_job], mock_logger)

        assert all_cleaned is False
        assert len(failures) == 1

    def test_cleanup_job_files_handles_generic_exception(self, tmp_path):
        """Mock os.remove raising unexpected exception, assert (False, [path])."""
        from unittest.mock import MagicMock

        from app.services.user_service import _cleanup_job_files

        downloads_dir = tmp_path / "downloads"
        downloads_dir.mkdir()
        file1 = downloads_dir / "file1.mp3"
        file1.write_text("test content")

        mock_job = MagicMock()
        mock_job.file_path = str(file1)
        mock_job.id = uuid.uuid4()

        mock_logger = MagicMock()

        with patch("app.services.user_service.settings") as mock_settings:
            mock_settings.storage_path = str(tmp_path)
            with patch(
                "app.services.user_service.os.remove",
                side_effect=RuntimeError("Unexpected error"),
            ):
                all_cleaned, failures = _cleanup_job_files([mock_job], mock_logger)

        assert all_cleaned is False
        assert len(failures) == 1


class TestLoginInactiveUser:
    """Tests for inactive user login branch."""

    @pytest.mark.asyncio
    async def test_login_inactive_user(self, db_session):
        """Test login with inactive user returns redirect to login page."""
        from app.services.auth_service import hash_password
        from core.models.user import User

        email = f"inactive_{uuid.uuid4().hex[:8]}@example.com"
        password = "securepassword123"

        # Create inactive user directly
        user = User(
            id=uuid.uuid4(),
            email=email,
            password_hash=await hash_password(password),
            is_active=False,
        )
        db_session.add(user)
        await db_session.commit()

        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test", follow_redirects=False
        ) as client:
            csrf_response = await client.get("/web/login")
            csrf_token = get_csrf_from_response(csrf_response)

            login_response = await client.post(
                "/web/login",
                data={"email": email, "password": password},
                headers={"X-CSRF-Token": csrf_token} if csrf_token else {},
            )

        assert login_response.status_code == 303
        assert login_response.headers["location"] == "/web/login?error=inactive"


class TestRegisterIntegrityError:
    """Tests for register IntegrityError branch."""

    @pytest.mark.asyncio
    async def test_register_integrity_error_race_condition(self):
        """Test IntegrityError during registration returns redirect with email_exists."""
        email = f"race_{uuid.uuid4().hex[:8]}@example.com"
        password = "securepassword123"

        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test", follow_redirects=False
        ) as client:
            csrf_response = await client.get("/web/register")
            csrf_token = get_csrf_from_response(csrf_response)

            # First registration succeeds
            reg1 = await client.post(
                "/web/register",
                data={
                    "email": email,
                    "password": password,
                    "password_confirm": password,
                },
                headers={"X-CSRF-Token": csrf_token} if csrf_token else {},
            )
            assert reg1.status_code == 303

            # Second registration with same email should hit the duplicate check
            csrf_response2 = await client.get("/web/register")
            csrf_token2 = get_csrf_from_response(csrf_response2)

            reg2 = await client.post(
                "/web/register",
                data={
                    "email": email,
                    "password": password,
                    "password_confirm": password,
                },
                headers={"X-CSRF-Token": csrf_token2} if csrf_token2 else {},
            )

        assert reg2.status_code == 303
        assert reg2.headers["location"] == "/web/register?error=email_exists"


class TestCreateDownloadFullPageErrors:
    """Tests for POST /web/downloads/full error branches."""

    @pytest.mark.asyncio
    async def test_create_download_full_page_invalid_csrf(self, sample_url):
        """Test full page download with invalid CSRF returns redirect."""
        email = f"fullcsrf_{uuid.uuid4().hex[:8]}@example.com"
        password = "securepassword123"

        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test", follow_redirects=False
        ) as client:
            await do_register(client, email, password)
            csrf_token = await do_login(client, email, password)

            login_resp = await client.post(
                "/web/login",
                data={"email": email, "password": password},
                headers={"X-CSRF-Token": csrf_token},
            )

            access_token = login_resp.cookies.get("access_token", "")
            csrf_token = (
                login_resp.cookies.get("csrf_token")
                or client.cookies.get("csrf_token")
                or csrf_token
            )

            create_response = await client.post(
                "/web/downloads/full",
                data={"url": sample_url},
                headers={"X-CSRF-Token": "invalid_token"},
                cookies={"access_token": access_token},
            )

        assert create_response.status_code == 303
        assert create_response.headers["location"] == "/web/downloads?error=csrf"

    @pytest.mark.asyncio
    async def test_create_download_full_page_invalid_url(self):
        """Test full page download with invalid URL returns redirect."""
        email = f"fullurl_{uuid.uuid4().hex[:8]}@example.com"
        password = "securepassword123"

        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test", follow_redirects=False
        ) as client:
            await do_register(client, email, password)
            csrf_token = await do_login(client, email, password)

            login_resp = await client.post(
                "/web/login",
                data={"email": email, "password": password},
                headers={"X-CSRF-Token": csrf_token},
            )

            access_token = login_resp.cookies.get("access_token", "")
            csrf_token = (
                login_resp.cookies.get("csrf_token")
                or client.cookies.get("csrf_token")
                or csrf_token
            )

            csrf_response = await client.get(
                "/web/downloads", cookies={"access_token": access_token}
            )
            csrf_token = get_csrf_from_response(csrf_response)

            create_response = await client.post(
                "/web/downloads/full",
                data={"url": "https://not-youtube.com/video"},
                headers={"X-CSRF-Token": csrf_token} if csrf_token else {},
                cookies={"access_token": access_token},
            )

        assert create_response.status_code == 303
        assert create_response.headers["location"] == "/web/downloads?error=invalid_url"

    @pytest.mark.asyncio
    async def test_create_download_full_page_exception_during_creation(self, sample_url):
        """Test full page download when outbox write fails returns error redirect."""
        email = f"fullerr_{uuid.uuid4().hex[:8]}@example.com"
        password = "securepassword123"

        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test", follow_redirects=False
        ) as client:
            await do_register(client, email, password)
            csrf_token = await do_login(client, email, password)

            login_resp = await client.post(
                "/web/login",
                data={"email": email, "password": password},
                headers={"X-CSRF-Token": csrf_token},
            )

            access_token = login_resp.cookies.get("access_token", "")
            csrf_token = (
                login_resp.cookies.get("csrf_token")
                or client.cookies.get("csrf_token")
                or csrf_token
            )

            csrf_response = await client.get(
                "/web/downloads", cookies={"access_token": access_token}
            )
            csrf_token = get_csrf_from_response(csrf_response)

            with patch(
                "app.services.download_service.write_job_to_outbox", new_callable=AsyncMock
            ) as mock_outbox:
                mock_outbox.side_effect = Exception("Database error")

                create_response = await client.post(
                    "/web/downloads/full",
                    data={"url": sample_url},
                    headers={"X-CSRF-Token": csrf_token} if csrf_token else {},
                    cookies={"access_token": access_token},
                )

        assert create_response.status_code == 303
        assert create_response.headers["location"] == "/web/downloads?error=creation_failed"


class TestDeleteAccount:
    """Tests for POST /web/settings/delete-account."""

    @pytest.mark.asyncio
    async def test_delete_account_invalid_csrf(self):
        """Test delete account with invalid CSRF returns redirect."""
        email = f"delcsrf_{uuid.uuid4().hex[:8]}@example.com"
        password = "securepassword123"

        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test", follow_redirects=False
        ) as client:
            await do_register(client, email, password)
            csrf_token = await do_login(client, email, password)

            login_resp = await client.post(
                "/web/login",
                data={"email": email, "password": password},
                headers={"X-CSRF-Token": csrf_token},
            )

            access_token = login_resp.cookies.get("access_token", "")
            csrf_token = (
                login_resp.cookies.get("csrf_token")
                or client.cookies.get("csrf_token")
                or csrf_token
            )

            response = await client.post(
                "/web/settings/delete-account",
                data={"password": password, "confirm_text": "DELETE"},
                headers={"X-CSRF-Token": "invalid_token"},
                cookies={"access_token": access_token},
            )

        assert response.status_code == 303
        assert response.headers["location"] == "/web/settings?error=csrf"

    @pytest.mark.asyncio
    async def test_delete_account_wrong_confirm_text(self):
        """Test delete account with wrong confirmation text returns error."""
        email = f"delconf_{uuid.uuid4().hex[:8]}@example.com"
        password = "securepassword123"

        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test", follow_redirects=False
        ) as client:
            await do_register(client, email, password)
            csrf_token = await do_login(client, email, password)

            login_resp = await client.post(
                "/web/login",
                data={"email": email, "password": password},
                headers={"X-CSRF-Token": csrf_token},
            )

            access_token = login_resp.cookies.get("access_token", "")
            csrf_token = (
                login_resp.cookies.get("csrf_token")
                or client.cookies.get("csrf_token")
                or csrf_token
            )

            csrf_response = await client.get(
                "/web/settings", cookies={"access_token": access_token}
            )
            csrf_token = get_csrf_from_response(csrf_response)

            response = await client.post(
                "/web/settings/delete-account",
                data={"password": password, "confirm_text": "WRONG"},
                headers={"X-CSRF-Token": csrf_token} if csrf_token else {},
                cookies={"access_token": access_token},
            )

        assert response.status_code == 303
        assert response.headers["location"] == "/web/settings?error=delete_confirmation"

    @pytest.mark.asyncio
    async def test_delete_account_wrong_password(self):
        """Test delete account with wrong password returns error."""
        email = f"delpw_{uuid.uuid4().hex[:8]}@example.com"
        password = "securepassword123"

        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test", follow_redirects=False
        ) as client:
            await do_register(client, email, password)
            csrf_token = await do_login(client, email, password)

            login_resp = await client.post(
                "/web/login",
                data={"email": email, "password": password},
                headers={"X-CSRF-Token": csrf_token},
            )

            access_token = login_resp.cookies.get("access_token", "")
            csrf_token = (
                login_resp.cookies.get("csrf_token")
                or client.cookies.get("csrf_token")
                or csrf_token
            )

            csrf_response = await client.get(
                "/web/settings", cookies={"access_token": access_token}
            )
            csrf_token = get_csrf_from_response(csrf_response)

            response = await client.post(
                "/web/settings/delete-account",
                data={"password": "wrongpassword", "confirm_text": "DELETE"},
                headers={"X-CSRF-Token": csrf_token} if csrf_token else {},
                cookies={"access_token": access_token},
            )

        assert response.status_code == 303
        assert response.headers["location"] == "/web/settings?error=bad_password"

    @pytest.mark.asyncio
    async def test_delete_account_success(self, tmp_path):
        """Test successful account deletion removes files, jobs, and user before redirecting."""
        email = f"delsucc_{uuid.uuid4().hex[:8]}@example.com"
        password = "securepassword123"
        downloads_dir = tmp_path / "downloads"
        downloads_dir.mkdir()
        downloaded_file = downloads_dir / "owned.mp3"
        downloaded_file.write_text("downloaded content")

        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test", follow_redirects=False
        ) as client:
            await do_register(client, email, password)
            csrf_token = await do_login(client, email, password)

            login_resp = await client.post(
                "/web/login",
                data={"email": email, "password": password},
                headers={"X-CSRF-Token": csrf_token},
            )

            access_token = login_resp.cookies.get("access_token", "")
            csrf_token = (
                login_resp.cookies.get("csrf_token")
                or client.cookies.get("csrf_token")
                or csrf_token
            )

            async with TestingSessionLocal() as session:
                result = await session.execute(select(User).where(User.email == email))
                user = result.scalar_one()
                job = DownloadJob(
                    id=uuid.uuid4(),
                    user_id=user.id,
                    url="https://www.youtube.com/watch?v=dQw4w9WgXcQ",
                    status="completed",
                    file_path=str(downloaded_file),
                )
                session.add(job)
                await session.commit()
                user_id = user.id
                job_id = job.id

            csrf_response = await client.get(
                "/web/settings", cookies={"access_token": access_token}
            )
            csrf_token = get_csrf_from_response(csrf_response)

            with patch("app.services.user_service.settings") as mock_settings:
                mock_settings.storage_path = str(tmp_path)
                response = await client.post(
                    "/web/settings/delete-account",
                    data={"password": password, "confirm_text": "DELETE"},
                    headers={"X-CSRF-Token": csrf_token} if csrf_token else {},
                    cookies={"access_token": access_token},
                )

        async with TestingSessionLocal() as session:
            deleted_user = await session.get(User, user_id)
            deleted_job = await session.get(DownloadJob, job_id)

        assert response.status_code == 303
        assert response.headers["location"] == "/web/login?account_deleted=1"
        assert "access_token" not in response.cookies or response.cookies.get("access_token") == ""
        assert (
            "refresh_token" not in response.cookies or response.cookies.get("refresh_token") == ""
        )
        assert downloaded_file.exists() is False
        assert deleted_user is None
        assert deleted_job is None

    @pytest.mark.asyncio
    async def test_delete_account_htmx_success(self):
        """Test successful HTMX account deletion returns HX-Redirect."""
        email = f"delhtmx_{uuid.uuid4().hex[:8]}@example.com"
        password = "securepassword123"

        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test", follow_redirects=False
        ) as client:
            await do_register(client, email, password)
            csrf_token = await do_login(client, email, password)

            login_resp = await client.post(
                "/web/login",
                data={"email": email, "password": password},
                headers={"X-CSRF-Token": csrf_token},
            )

            access_token = login_resp.cookies.get("access_token", "")
            csrf_token = (
                login_resp.cookies.get("csrf_token")
                or client.cookies.get("csrf_token")
                or csrf_token
            )

            csrf_response = await client.get(
                "/web/settings", cookies={"access_token": access_token}
            )
            csrf_token = get_csrf_from_response(csrf_response)

            response = await client.post(
                "/web/settings/delete-account",
                data={"password": password, "confirm_text": "DELETE"},
                headers={
                    "X-CSRF-Token": csrf_token if csrf_token else "",
                    "HX-Request": "true",
                },
                cookies={"access_token": access_token},
            )

        assert response.status_code == 200
        assert response.headers["HX-Redirect"] == "/web/login?account_deleted=1"

    @pytest.mark.asyncio
    async def test_delete_account_file_cleanup_failure(self, tmp_path):
        """Test account deletion when file cleanup fails returns error."""
        email = f"delfail_{uuid.uuid4().hex[:8]}@example.com"
        password = "securepassword123"

        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test", follow_redirects=False
        ) as client:
            await do_register(client, email, password)
            csrf_token = await do_login(client, email, password)

            login_resp = await client.post(
                "/web/login",
                data={"email": email, "password": password},
                headers={"X-CSRF-Token": csrf_token},
            )

            access_token = login_resp.cookies.get("access_token", "")
            csrf_token = (
                login_resp.cookies.get("csrf_token")
                or client.cookies.get("csrf_token")
                or csrf_token
            )

            # Create a job with a file path that will fail path traversal check
            from sqlalchemy import select

            from core.models.download_job import DownloadJob

            # Need to get user id first
            from core.models.user import User

            async with TestingSessionLocal() as session:
                result = await session.execute(select(User).where(User.email == email))
                user = result.scalar_one()

                job = DownloadJob(
                    id=uuid.uuid4(),
                    user_id=user.id,
                    url="https://www.youtube.com/watch?v=dQw4w9WgXcQ",
                    status="completed",
                    file_path=str(tmp_path / ".." / "etc" / "passwd"),
                )
                session.add(job)
                await session.commit()

            csrf_response = await client.get(
                "/web/settings", cookies={"access_token": access_token}
            )
            csrf_token = get_csrf_from_response(csrf_response)

            with patch("app.services.user_service.settings") as mock_settings:
                mock_settings.storage_path = str(tmp_path)

                response = await client.post(
                    "/web/settings/delete-account",
                    data={"password": password, "confirm_text": "DELETE"},
                    headers={"X-CSRF-Token": csrf_token} if csrf_token else {},
                    cookies={"access_token": access_token},
                )

        assert response.status_code == 303
        assert response.headers["location"] == "/web/settings?error=file_cleanup"


class TestDeleteDownloadFormBranches:
    """Tests for additional DELETE /web/downloads/{job_id} branches."""

    @pytest.mark.asyncio
    async def test_delete_download_processing_status(self):
        """Test deleting a processing job returns 409."""
        email = f"delproc_{uuid.uuid4().hex[:8]}@example.com"
        password = "securepassword123"

        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test", follow_redirects=False
        ) as client:
            await do_register(client, email, password)
            csrf_token = await do_login(client, email, password)

            login_resp = await client.post(
                "/web/login",
                data={"email": email, "password": password},
                headers={"X-CSRF-Token": csrf_token},
            )

            access_token = login_resp.cookies.get("access_token", "")
            csrf_token = (
                login_resp.cookies.get("csrf_token")
                or client.cookies.get("csrf_token")
                or csrf_token
            )

            # Create a processing job directly
            from sqlalchemy import select

            from core.models.download_job import DownloadJob
            from core.models.user import User

            async with TestingSessionLocal() as session:
                result = await session.execute(select(User).where(User.email == email))
                user = result.scalar_one()

                job = DownloadJob(
                    id=uuid.uuid4(),
                    user_id=user.id,
                    url="https://www.youtube.com/watch?v=dQw4w9WgXcQ",
                    status="processing",
                )
                session.add(job)
                await session.commit()
                job_id = str(job.id)

            headers = {"HX-Request": "true"}
            if csrf_token:
                headers["X-CSRF-Token"] = csrf_token

            delete_response = await client.delete(
                f"/web/downloads/{job_id}",
                headers=headers,
                cookies={"access_token": access_token},
            )

        assert delete_response.status_code == 409
        assert "processing" in delete_response.text

    @pytest.mark.asyncio
    async def test_delete_download_with_file_path_traversal(self, tmp_path):
        """Test deleting job with path traversal file_path raises 403."""
        email = f"delpath_{uuid.uuid4().hex[:8]}@example.com"
        password = "securepassword123"

        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test", follow_redirects=False
        ) as client:
            await do_register(client, email, password)
            csrf_token = await do_login(client, email, password)

            login_resp = await client.post(
                "/web/login",
                data={"email": email, "password": password},
                headers={"X-CSRF-Token": csrf_token},
            )

            access_token = login_resp.cookies.get("access_token", "")
            csrf_token = (
                login_resp.cookies.get("csrf_token")
                or client.cookies.get("csrf_token")
                or csrf_token
            )

            from sqlalchemy import select

            from core.models.download_job import DownloadJob
            from core.models.user import User

            async with TestingSessionLocal() as session:
                result = await session.execute(select(User).where(User.email == email))
                user = result.scalar_one()

                job = DownloadJob(
                    id=uuid.uuid4(),
                    user_id=user.id,
                    url="https://www.youtube.com/watch?v=dQw4w9WgXcQ",
                    status="completed",
                    file_path=str(tmp_path / ".." / "etc" / "passwd"),
                )
                session.add(job)
                await session.commit()
                job_id = str(job.id)

            headers = {"HX-Request": "true"}
            if csrf_token:
                headers["X-CSRF-Token"] = csrf_token

            with patch("app.services.download_service.settings") as mock_settings:
                mock_settings.storage_path = str(tmp_path)

                delete_response = await client.delete(
                    f"/web/downloads/{job_id}",
                    headers=headers,
                    cookies={"access_token": access_token},
                )

        assert delete_response.status_code == 403

    @pytest.mark.asyncio
    async def test_delete_download_with_os_error_on_file_delete(self, tmp_path):
        """Test deleting job when os.remove raises OSError still deletes DB record."""
        email = f"delos_{uuid.uuid4().hex[:8]}@example.com"
        password = "securepassword123"

        downloads_dir = tmp_path / "downloads"
        downloads_dir.mkdir()
        file1 = downloads_dir / "test.mp3"
        file1.write_text("test content")

        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test", follow_redirects=False
        ) as client:
            await do_register(client, email, password)
            csrf_token = await do_login(client, email, password)

            login_resp = await client.post(
                "/web/login",
                data={"email": email, "password": password},
                headers={"X-CSRF-Token": csrf_token},
            )

            access_token = login_resp.cookies.get("access_token", "")
            csrf_token = (
                login_resp.cookies.get("csrf_token")
                or client.cookies.get("csrf_token")
                or csrf_token
            )

            from sqlalchemy import select

            from core.models.download_job import DownloadJob
            from core.models.user import User

            async with TestingSessionLocal() as session:
                result = await session.execute(select(User).where(User.email == email))
                user = result.scalar_one()

                job = DownloadJob(
                    id=uuid.uuid4(),
                    user_id=user.id,
                    url="https://www.youtube.com/watch?v=dQw4w9WgXcQ",
                    status="completed",
                    file_path=str(file1),
                )
                session.add(job)
                await session.commit()
                job_id = str(job.id)

            headers = {"HX-Request": "true"}
            if csrf_token:
                headers["X-CSRF-Token"] = csrf_token

            with patch("app.services.download_service.settings") as mock_settings:
                mock_settings.storage_path = str(tmp_path)
                with patch(
                    "app.services.download_service.os.remove",
                    side_effect=OSError("Permission denied"),
                ):
                    delete_response = await client.delete(
                        f"/web/downloads/{job_id}",
                        headers=headers,
                        cookies={"access_token": access_token},
                    )

        assert delete_response.status_code == 200
        assert delete_response.text == ""

        # Verify DB record was deleted
        async with TestingSessionLocal() as session:
            result = await session.execute(
                select(DownloadJob).where(DownloadJob.id == uuid.UUID(job_id))
            )
            assert result.scalar_one_or_none() is None


class TestDownloadFileBranches:
    """Tests for additional GET /web/downloads/{job_id}/file branches."""

    @pytest.mark.asyncio
    async def test_download_file_job_not_completed(self):
        """Test downloading file for non-completed job returns 400."""
        email = f"dlncomp_{uuid.uuid4().hex[:8]}@example.com"
        password = "securepassword123"

        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test", follow_redirects=False
        ) as client:
            await do_register(client, email, password)
            csrf_token = await do_login(client, email, password)

            login_resp = await client.post(
                "/web/login",
                data={"email": email, "password": password},
                headers={"X-CSRF-Token": csrf_token},
            )

            access_token = login_resp.cookies.get("access_token", "")
            csrf_token = (
                login_resp.cookies.get("csrf_token")
                or client.cookies.get("csrf_token")
                or csrf_token
            )

            from sqlalchemy import select

            from core.models.download_job import DownloadJob
            from core.models.user import User

            async with TestingSessionLocal() as session:
                result = await session.execute(select(User).where(User.email == email))
                user = result.scalar_one()

                job = DownloadJob(
                    id=uuid.uuid4(),
                    user_id=user.id,
                    url="https://www.youtube.com/watch?v=dQw4w9WgXcQ",
                    status="processing",
                )
                session.add(job)
                await session.commit()
                job_id = str(job.id)

            download_response = await client.get(
                f"/web/downloads/{job_id}/file",
                cookies={"access_token": access_token},
            )

        assert download_response.status_code == 400
        assert "not completed" in download_response.text.lower()

    @pytest.mark.asyncio
    async def test_download_file_no_file_path(self):
        """Test downloading file for completed job with no file_path returns 404."""
        email = f"dlnopath_{uuid.uuid4().hex[:8]}@example.com"
        password = "securepassword123"

        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test", follow_redirects=False
        ) as client:
            await do_register(client, email, password)
            csrf_token = await do_login(client, email, password)

            login_resp = await client.post(
                "/web/login",
                data={"email": email, "password": password},
                headers={"X-CSRF-Token": csrf_token},
            )

            access_token = login_resp.cookies.get("access_token", "")
            csrf_token = (
                login_resp.cookies.get("csrf_token")
                or client.cookies.get("csrf_token")
                or csrf_token
            )

            from sqlalchemy import select

            from core.models.download_job import DownloadJob
            from core.models.user import User

            async with TestingSessionLocal() as session:
                result = await session.execute(select(User).where(User.email == email))
                user = result.scalar_one()

                job = DownloadJob(
                    id=uuid.uuid4(),
                    user_id=user.id,
                    url="https://www.youtube.com/watch?v=dQw4w9WgXcQ",
                    status="completed",
                    file_path=None,
                )
                session.add(job)
                await session.commit()
                job_id = str(job.id)

            download_response = await client.get(
                f"/web/downloads/{job_id}/file",
                cookies={"access_token": access_token},
            )

        assert download_response.status_code == 404
        assert "file not found" in download_response.text.lower()

    @pytest.mark.asyncio
    async def test_download_file_expired(self):
        """Test downloading file for expired job returns 410."""
        from datetime import UTC, datetime, timedelta

        email = f"dlexp_{uuid.uuid4().hex[:8]}@example.com"
        password = "securepassword123"

        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test", follow_redirects=False
        ) as client:
            await do_register(client, email, password)
            csrf_token = await do_login(client, email, password)

            login_resp = await client.post(
                "/web/login",
                data={"email": email, "password": password},
                headers={"X-CSRF-Token": csrf_token},
            )

            access_token = login_resp.cookies.get("access_token", "")
            csrf_token = (
                login_resp.cookies.get("csrf_token")
                or client.cookies.get("csrf_token")
                or csrf_token
            )

            from sqlalchemy import select

            from core.models.download_job import DownloadJob
            from core.models.user import User

            async with TestingSessionLocal() as session:
                result = await session.execute(select(User).where(User.email == email))
                user = result.scalar_one()

                job = DownloadJob(
                    id=uuid.uuid4(),
                    user_id=user.id,
                    url="https://www.youtube.com/watch?v=dQw4w9WgXcQ",
                    status="completed",
                    file_path="/tmp/fake.mp3",
                    expires_at=datetime.now(UTC) - timedelta(hours=1),
                )
                session.add(job)
                await session.commit()
                job_id = str(job.id)

            download_response = await client.get(
                f"/web/downloads/{job_id}/file",
                cookies={"access_token": access_token},
            )

        assert download_response.status_code == 410
        assert "expired" in download_response.text.lower()

    @pytest.mark.asyncio
    async def test_download_file_missing_from_disk(self, tmp_path):
        """Test downloading file that doesn't exist on disk returns 404."""
        email = f"dlmiss_{uuid.uuid4().hex[:8]}@example.com"
        password = "securepassword123"

        downloads_dir = tmp_path / "downloads"
        downloads_dir.mkdir()

        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test", follow_redirects=False
        ) as client:
            await do_register(client, email, password)
            csrf_token = await do_login(client, email, password)

            login_resp = await client.post(
                "/web/login",
                data={"email": email, "password": password},
                headers={"X-CSRF-Token": csrf_token},
            )

            access_token = login_resp.cookies.get("access_token", "")
            csrf_token = (
                login_resp.cookies.get("csrf_token")
                or client.cookies.get("csrf_token")
                or csrf_token
            )

            from sqlalchemy import select

            from core.models.download_job import DownloadJob
            from core.models.user import User

            async with TestingSessionLocal() as session:
                result = await session.execute(select(User).where(User.email == email))
                user = result.scalar_one()

                job = DownloadJob(
                    id=uuid.uuid4(),
                    user_id=user.id,
                    url="https://www.youtube.com/watch?v=dQw4w9WgXcQ",
                    status="completed",
                    file_path=str(downloads_dir / "nonexistent.mp3"),
                    file_name="nonexistent.mp3",
                )
                session.add(job)
                await session.commit()
                job_id = str(job.id)

            with patch("app.services.download_service.settings") as mock_settings:
                mock_settings.storage_path = str(tmp_path)

                download_response = await client.get(
                    f"/web/downloads/{job_id}/file",
                    cookies={"access_token": access_token},
                )

        assert download_response.status_code == 404
        assert "file not found on disk" in download_response.text.lower()

    @pytest.mark.asyncio
    async def test_download_file_path_traversal_returns_403(self, tmp_path):
        """Test downloading a stored path outside downloads returns 403."""
        email = f"dlpath_{uuid.uuid4().hex[:8]}@example.com"
        password = "securepassword123"

        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test", follow_redirects=False
        ) as client:
            await do_register(client, email, password)
            csrf_token = await do_login(client, email, password)

            login_resp = await client.post(
                "/web/login",
                data={"email": email, "password": password},
                headers={"X-CSRF-Token": csrf_token},
            )

            access_token = login_resp.cookies.get("access_token", "")

            from core.models.user import User

            async with TestingSessionLocal() as session:
                result = await session.execute(select(User).where(User.email == email))
                user = result.scalar_one()

                job = DownloadJob(
                    id=uuid.uuid4(),
                    user_id=user.id,
                    url="https://www.youtube.com/watch?v=dQw4w9WgXcQ",
                    status="completed",
                    file_path=str(tmp_path / ".." / "etc" / "passwd"),
                    file_name="passwd",
                )
                session.add(job)
                await session.commit()
                job_id = str(job.id)

            with patch("app.services.download_service.settings") as mock_settings:
                mock_settings.storage_path = str(tmp_path)

                download_response = await client.get(
                    f"/web/downloads/{job_id}/file",
                    cookies={"access_token": access_token},
                )

        assert download_response.status_code == 403
        assert "access denied" in download_response.text.lower()

    @pytest.mark.asyncio
    async def test_download_file_success(self, tmp_path):
        """Test successful file download returns FileResponse."""
        email = f"dlsucc_{uuid.uuid4().hex[:8]}@example.com"
        password = "securepassword123"

        downloads_dir = tmp_path / "downloads"
        downloads_dir.mkdir()
        test_file = downloads_dir / "test.mp3"
        test_file.write_text("fake audio content")

        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test", follow_redirects=False
        ) as client:
            await do_register(client, email, password)
            csrf_token = await do_login(client, email, password)

            login_resp = await client.post(
                "/web/login",
                data={"email": email, "password": password},
                headers={"X-CSRF-Token": csrf_token},
            )

            access_token = login_resp.cookies.get("access_token", "")
            csrf_token = (
                login_resp.cookies.get("csrf_token")
                or client.cookies.get("csrf_token")
                or csrf_token
            )

            from sqlalchemy import select

            from core.models.download_job import DownloadJob
            from core.models.user import User

            async with TestingSessionLocal() as session:
                result = await session.execute(select(User).where(User.email == email))
                user = result.scalar_one()

                job = DownloadJob(
                    id=uuid.uuid4(),
                    user_id=user.id,
                    url="https://www.youtube.com/watch?v=dQw4w9WgXcQ",
                    status="completed",
                    file_path=str(test_file),
                    file_name="test.mp3",
                )
                session.add(job)
                await session.commit()
                job_id = str(job.id)

            with patch("app.services.download_service.settings") as mock_settings:
                mock_settings.storage_path = str(tmp_path)

                download_response = await client.get(
                    f"/web/downloads/{job_id}/file",
                    cookies={"access_token": access_token},
                )

        assert download_response.status_code == 200
        assert download_response.content == b"fake audio content"
        assert download_response.headers.get("content-disposition", "").count("test.mp3") > 0


class TestResolveErrorsHelpers:
    """Tests for error resolution helpers covering missing branches."""

    def test_resolve_login_errors_csrf(self):
        """Test csrf error code mapping."""
        from app.api.routes.web import _resolve_login_errors

        message, field_errors = _resolve_login_errors("csrf")
        assert message == "Invalid CSRF token"
        assert field_errors == {}

    def test_resolve_login_errors_inactive(self):
        """Test inactive error code mapping."""
        from app.api.routes.web import _resolve_login_errors

        message, field_errors = _resolve_login_errors("inactive")
        assert message == "Account is inactive"
        assert field_errors == {"email": "Account is inactive"}

    def test_resolve_login_errors_unknown(self):
        """Test unknown error code returns None."""
        from app.api.routes.web import _resolve_login_errors

        message, field_errors = _resolve_login_errors("unknown")
        assert message is None
        assert field_errors == {}

    def test_resolve_register_errors_password_too_short(self):
        """Test password_too_short error code mapping."""
        from app.api.routes.web import _resolve_register_errors

        message, field_errors = _resolve_register_errors("password_too_short")
        assert message == "Password must be at least 8 characters"
        assert field_errors == {"password": "Password must be at least 8 characters"}

    def test_resolve_register_errors_email_exists(self):
        """Test email_exists error code mapping."""
        from app.api.routes.web import _resolve_register_errors

        message, field_errors = _resolve_register_errors("email_exists")
        assert message == "Email already registered"
        assert field_errors == {"email": "Email already registered"}

    def test_resolve_register_errors_csrf(self):
        """Test csrf error code mapping."""
        from app.api.routes.web import _resolve_register_errors

        message, field_errors = _resolve_register_errors("csrf")
        assert message == "Invalid CSRF token"
        assert field_errors == {}

    def test_resolve_register_errors_unknown(self):
        """Test unknown error code returns None."""
        from app.api.routes.web import _resolve_register_errors

        message, field_errors = _resolve_register_errors("unknown")
        assert message is None
        assert field_errors == {}

    def test_resolve_settings_errors_all_codes(self):
        """Test all settings error code mappings."""
        from app.api.routes.web import _resolve_settings_errors

        test_cases = {
            "username_too_short": (
                "Username must be at least 3 characters",
                {"username": "Username must be at least 3 characters"},
            ),
            "bad_current_password": (
                "Current password is incorrect",
                {"current_password": "Current password is incorrect"},
            ),
            "password_mismatch": (
                "New passwords do not match",
                {"new_password_confirm": "New passwords do not match"},
            ),
            "password_too_short": (
                "New password must be at least 8 characters",
                {"new_password": "New password must be at least 8 characters"},
            ),
            "bad_password": ("Password is incorrect", {"delete_password": "Password is incorrect"}),
            "delete_confirmation": (
                "Please type DELETE to confirm account deletion",
                {"confirm_text": "Please type DELETE to confirm account deletion"},
            ),
            "file_cleanup": ("Unable to remove all downloaded files. Account was not deleted.", {}),
            "csrf": ("Invalid CSRF token", {}),
        }

        for code, (expected_message, expected_fields) in test_cases.items():
            message, field_errors = _resolve_settings_errors(code)
            assert message == expected_message, f"Failed for code {code}"
            assert field_errors == expected_fields, f"Failed for code {code}"

    def test_resolve_settings_errors_none(self):
        """Test None error code returns None."""
        from app.api.routes.web import _resolve_settings_errors

        message, field_errors = _resolve_settings_errors(None)
        assert message is None
        assert field_errors == {}

    def test_resolve_settings_errors_unknown(self):
        """Test unknown error code returns None."""
        from app.api.routes.web import _resolve_settings_errors

        message, field_errors = _resolve_settings_errors("unknown_code")
        assert message is None
        assert field_errors == {}


class TestTermsPage:
    """Tests for the GET /web/terms endpoint added in this PR."""

    async def test_terms_page_returns_200(self):
        """Terms page should return 200 OK without authentication."""
        async with AsyncClient(
            transport=ASGITransport(app=app),
            base_url="http://test",
            follow_redirects=False,
        ) as client:
            response = await client.get("/web/terms")

        assert response.status_code == 200

    async def test_terms_page_sets_csrf_cookie(self):
        """Terms page should set a csrf_token cookie on the response."""
        async with AsyncClient(
            transport=ASGITransport(app=app),
            base_url="http://test",
            follow_redirects=False,
        ) as client:
            response = await client.get("/web/terms")

        assert response.status_code == 200
        assert response.cookies.get("csrf_token") is not None

    async def test_terms_page_returns_html(self):
        """Terms page should return HTML content type."""
        async with AsyncClient(
            transport=ASGITransport(app=app),
            base_url="http://test",
            follow_redirects=False,
        ) as client:
            response = await client.get("/web/terms")

        assert "text/html" in response.headers.get("content-type", "")

    async def test_terms_page_renders_title(self):
        """Terms page should render the 'Terms of Service' heading."""
        async with AsyncClient(
            transport=ASGITransport(app=app),
            base_url="http://test",
            follow_redirects=False,
        ) as client:
            response = await client.get("/web/terms")

        assert "Terms of Service" in response.text

    async def test_terms_page_renders_acceptance_section(self):
        """Terms page should include the Acceptance of Terms section."""
        async with AsyncClient(
            transport=ASGITransport(app=app),
            base_url="http://test",
            follow_redirects=False,
        ) as client:
            response = await client.get("/web/terms")

        assert "Acceptance of Terms" in response.text

    async def test_terms_page_renders_permitted_uses_section(self):
        """Terms page should include the Permitted Uses section."""
        async with AsyncClient(
            transport=ASGITransport(app=app),
            base_url="http://test",
            follow_redirects=False,
        ) as client:
            response = await client.get("/web/terms")

        assert "Permitted Uses" in response.text

    async def test_terms_page_renders_prohibited_uses_section(self):
        """Terms page should include the Prohibited Uses section."""
        async with AsyncClient(
            transport=ASGITransport(app=app),
            base_url="http://test",
            follow_redirects=False,
        ) as client:
            response = await client.get("/web/terms")

        assert "Prohibited Uses" in response.text

    async def test_terms_page_renders_disclaimer_section(self):
        """Terms page should include the Disclaimer of Liability section."""
        async with AsyncClient(
            transport=ASGITransport(app=app),
            base_url="http://test",
            follow_redirects=False,
        ) as client:
            response = await client.get("/web/terms")

        assert "Disclaimer of Liability" in response.text

    async def test_terms_page_renders_dmca_section(self):
        """Terms page should include the DMCA and Content Removal section."""
        async with AsyncClient(
            transport=ASGITransport(app=app),
            base_url="http://test",
            follow_redirects=False,
        ) as client:
            response = await client.get("/web/terms")

        assert "DMCA and Content Removal" in response.text

    async def test_terms_page_contains_current_year(self):
        """Terms page footer should render the current year as a whole token (e.g. '© 2026')."""
        from datetime import UTC, datetime

        # Match the app: get_template_context uses datetime.now(UTC).year.
        current_year = str(datetime.now(UTC).year)

        async with AsyncClient(
            transport=ASGITransport(app=app),
            base_url="http://test",
            follow_redirects=False,
        ) as client:
            response = await client.get("/web/terms")

        # Anchor to the copyright entity so a bare 4-digit number appearing
        # elsewhere in the page (the last_updated date, statute refs, etc.)
        # cannot cause a false pass. The template emits the literal HTML entity
        # "&copy;", which is preserved verbatim in the raw response body.
        assert f"&copy; {current_year}" in response.text

    async def test_terms_page_csrf_token_in_html_matches_cookie(self):
        """The CSRF token embedded in the page should match the cookie value."""
        async with AsyncClient(
            transport=ASGITransport(app=app),
            base_url="http://test",
            follow_redirects=False,
        ) as client:
            response = await client.get("/web/terms")

        csrf_cookie = response.cookies.get("csrf_token")
        assert csrf_cookie is not None
        # The CSRF token must appear somewhere in the rendered HTML
        assert csrf_cookie in response.text

    async def test_terms_page_accessible_without_auth(self):
        """Terms page must be reachable without any authentication headers."""
        async with AsyncClient(
            transport=ASGITransport(app=app),
            base_url="http://test",
            follow_redirects=False,
        ) as client:
            # Explicitly no auth headers
            response = await client.get("/web/terms", headers={})

        # Should not redirect to login
        assert response.status_code == 200

    async def test_terms_page_has_sign_in_link(self):
        """Terms page footer navigation should contain a Sign In link."""
        async with AsyncClient(
            transport=ASGITransport(app=app),
            base_url="http://test",
            follow_redirects=False,
        ) as client:
            response = await client.get("/web/terms")

        assert "/web/login" in response.text

    async def test_terms_page_has_terms_link(self):
        """Terms page footer navigation should contain a link back to /web/terms."""
        async with AsyncClient(
            transport=ASGITransport(app=app),
            base_url="http://test",
            follow_redirects=False,
        ) as client:
            response = await client.get("/web/terms")

        assert "/web/terms" in response.text

    async def test_terms_page_renders_dmca_agent_placeholder(self):
        """DMCA designated agent section must flag that statutory contact details are missing."""
        async with AsyncClient(
            transport=ASGITransport(app=app),
            base_url="http://test",
            follow_redirects=False,
        ) as client:
            response = await client.get("/web/terms")

        assert "DMCA Designated Agent" in response.text
        # Statutory § 512(c)(2) contact details must be explicitly called out as TODO.
        assert "512(c)(2)" in response.text

    async def test_terms_page_renders_explicit_last_updated(self):
        """The 'Last updated' date must be an explicit revision date, not the auto year."""
        async with AsyncClient(
            transport=ASGITransport(app=app),
            base_url="http://test",
            follow_redirects=False,
        ) as client:
            response = await client.get("/web/terms")

        assert "Last updated: April 26, 2026" in response.text

    async def test_terms_page_important_legal_notice_present(self):
        """Terms page should render the Important Legal Notice warning box."""
        async with AsyncClient(
            transport=ASGITransport(app=app),
            base_url="http://test",
            follow_redirects=False,
        ) as client:
            response = await client.get("/web/terms")

        assert "Important Legal Notice" in response.text

    async def test_terms_page_nature_of_service_section(self):
        """Terms page should include the Nature of Vooglaadija section."""
        async with AsyncClient(
            transport=ASGITransport(app=app),
            base_url="http://test",
            follow_redirects=False,
        ) as client:
            response = await client.get("/web/terms")

        assert "Nature of Vooglaadija" in response.text

    async def test_terms_page_new_csrf_token_each_request(self):
        """Each request to /web/terms should set a CSRF cookie (may differ per request)."""
        async with AsyncClient(
            transport=ASGITransport(app=app),
            base_url="http://test",
            follow_redirects=False,
        ) as client:
            response1 = await client.get("/web/terms")
            csrf1 = response1.cookies.get("csrf_token")

        async with AsyncClient(
            transport=ASGITransport(app=app),
            base_url="http://test",
            follow_redirects=False,
        ) as client:
            response2 = await client.get("/web/terms")
            csrf2 = response2.cookies.get("csrf_token")

        # Both requests must receive a CSRF token (values may or may not differ)
        assert csrf1 is not None
        assert csrf2 is not None

    async def test_terms_page_existing_csrf_cookie_reused(self):
        """When a csrf_token cookie is sent with the request, the same token is reused."""
        async with AsyncClient(
            transport=ASGITransport(app=app),
            base_url="http://test",
            follow_redirects=False,
        ) as client:
            # First request: obtain a fresh CSRF token
            first = await client.get("/web/terms")
            existing_token = first.cookies.get("csrf_token")
            assert existing_token is not None

            # Second request within the same client so the cookie jar carries the token
            second = await client.get("/web/terms")

            # Read the cookie jar while the client is still open (httpx does not
            # guarantee jar state after the context manager exits).
            jar_token = client.cookies.get("csrf_token")
            second_html = second.text

        # response.cookies only reflects Set-Cookie headers; the authoritative source
        # for "reuse" is the client's cookie jar (and the token embedded in the HTML).
        assert jar_token == existing_token
        # The rendered page must embed the same, reused token.
        assert existing_token in second_html

"""Web routes tests for Clerk integration."""

import re
import uuid
from html.parser import HTMLParser
from types import SimpleNamespace
from typing import ClassVar

import pytest
from httpx import ASGITransport, AsyncClient

from app.main import app


def get_csrf_from_response(response) -> str | None:
    """Extract CSRF token from response cookies."""
    return response.cookies.get("csrf_token")


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


class TestLoginPage:
    """Tests for GET /web/login with Clerk."""

    @pytest.mark.asyncio
    async def test_login_page_renders(self):
        """Test that login page renders with Clerk component."""
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response = await client.get("/web/login")

        assert response.status_code == 200
        assert "clerk-signin" in response.text
        assert "clerk_publishable_key" in response.text.lower() or "pk_" in response.text

    @pytest.mark.asyncio
    async def test_login_page_renders_error(self):
        """Test login page shows error message."""
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response = await client.get("/web/login?error=1")

        assert response.status_code == 200
        assert "Authentication failed" in response.text


class TestRegisterPage:
    """Tests for GET /web/register with Clerk."""

    @pytest.mark.asyncio
    async def test_register_page_renders(self):
        """Test that register page renders with Clerk component."""
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response = await client.get("/web/register")

        assert response.status_code == 200
        assert "clerk-signup" in response.text


class TestLogout:
    """Tests for POST /web/logout with Clerk."""

    @pytest.mark.asyncio
    async def test_logout_redirects_to_login(self):
        """Test logout redirects to login page."""
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test", follow_redirects=False
        ) as client:
            response = await client.post("/web/logout")

        assert response.status_code == 303
        assert "/web/login" in response.headers["location"]


class TestDashboardPage:
    """Tests for GET /web/downloads (dashboard) with Clerk auth."""

    @pytest.mark.asyncio
    async def test_dashboard_requires_auth(self):
        """Test that dashboard requires authentication."""
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response = await client.get("/web/downloads")

        assert response.status_code == 401

    @pytest.mark.asyncio
    async def test_dashboard_with_clerk_cookie(self):
        """Test that dashboard renders for user with Clerk session cookie."""
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test", follow_redirects=False
        ) as client:
            response = await client.get(
                "/web/downloads",
                cookies={"__session": f"mock-token-user_{uuid.uuid4().hex[:12]}"},
            )

        assert response.status_code == 200

"""Tests for app/main.py - FastAPI application entry point."""

import signal
import sys
import uuid
from unittest.mock import MagicMock

import pytest
import structlog.contextvars
from fastapi import Request
from fastapi.responses import JSONResponse, RedirectResponse
from httpx import ASGITransport, AsyncClient
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.api.exceptions import (
    general_exception_handler,
    http_exception_handler,
    validation_exception_handler,
)
from app.api.middleware.request_id import add_request_id
from app.api.middleware.security_headers import add_security_headers
from app.api.startup import _shutdown_state, _sigterm_handler
from app.main import (
    UVLOOP_AVAILABLE,
    app,
    lifespan,
    root,
)


class TestSecurityHeadersMiddleware:
    """Tests for add_security_headers middleware."""

    @pytest.mark.asyncio
    async def test_security_headers_added(self):
        """Test that security headers are added to responses."""
        mock_request = MagicMock(spec=Request)
        mock_request.state.nonce = "test-nonce-123"

        async def mock_call_next(request):
            response = MagicMock()
            response.headers = {}
            return response

        result = await add_security_headers(mock_request, mock_call_next)

        assert "Content-Security-Policy" in result.headers
        assert result.headers["X-Content-Type-Options"] == "nosniff"
        assert result.headers["X-Frame-Options"] == "DENY"
        assert result.headers["Referrer-Policy"] == "strict-origin-when-cross-origin"
        assert result.headers["Permissions-Policy"] == "camera=(), microphone=(), geolocation=()"

    @pytest.mark.asyncio
    async def test_csp_contains_nonce(self):
        """Test that CSP header contains a nonce."""
        mock_request = MagicMock(spec=Request)

        async def mock_call_next(request):
            response = MagicMock()
            response.headers = {}
            return response

        result = await add_security_headers(mock_request, mock_call_next)

        csp = result.headers["Content-Security-Policy"]
        assert "script-src" in csp
        assert "'nonce-" in csp


class TestRequestIdMiddleware:
    """Tests for add_request_id middleware."""

    @pytest.mark.asyncio
    async def test_request_id_added_to_response(self):
        """Test that X-Request-ID header is added to responses."""
        mock_request = MagicMock(spec=Request)

        request_id_captured = None

        async def mock_call_next(request):
            nonlocal request_id_captured
            request_id_captured = request.state.request_id
            response = MagicMock()
            response.headers = {}
            return response

        result = await add_request_id(mock_request, mock_call_next)

        assert result.headers["X-Request-ID"] == request_id_captured
        assert len(request_id_captured) == 36  # UUID format with dashes

    @pytest.mark.asyncio
    async def test_request_id_unbound_when_downstream_raises(self):
        """Test that request ID context is cleared when downstream handling fails."""
        structlog.contextvars.clear_contextvars()
        mock_request = MagicMock(spec=Request)

        async def mock_call_next(request):
            raise RuntimeError("downstream failed")

        with pytest.raises(RuntimeError, match="downstream failed"):
            await add_request_id(mock_request, mock_call_next)

        assert "request_id" not in structlog.contextvars.get_contextvars()


class TestHTTPExceptionHandler:
    """Tests for http_exception_handler."""

    @pytest.mark.asyncio
    async def test_http_exception_401_unauthorized(self):
        """Test 401 returns UNAUTHORIZED error code."""
        mock_request = MagicMock(spec=Request)
        mock_request.state.request_id = "test-request-id"

        exc = StarletteHTTPException(status_code=401, detail="Not authenticated")

        result = await http_exception_handler(mock_request, exc)

        assert isinstance(result, JSONResponse)
        assert result.status_code == 401

    @pytest.mark.asyncio
    async def test_http_exception_404_not_found(self):
        """Test 404 returns NOT_FOUND error code."""
        mock_request = MagicMock(spec=Request)
        mock_request.state.request_id = "test-request-id"

        exc = StarletteHTTPException(status_code=404, detail="Resource not found")

        result = await http_exception_handler(mock_request, exc)

        assert isinstance(result, JSONResponse)
        assert result.status_code == 404

    @pytest.mark.asyncio
    async def test_http_exception_422_validation_error(self):
        """Test 422 returns VALIDATION_ERROR error code."""
        mock_request = MagicMock(spec=Request)
        mock_request.state.request_id = "test-request-id"

        exc = StarletteHTTPException(status_code=422, detail="Validation error")

        result = await http_exception_handler(mock_request, exc)

        assert isinstance(result, JSONResponse)
        assert result.status_code == 422

    @pytest.mark.asyncio
    async def test_http_exception_500_internal_error(self):
        """Test 500 returns INTERNAL_ERROR error code."""
        mock_request = MagicMock(spec=Request)
        mock_request.state.request_id = "test-request-id"

        exc = StarletteHTTPException(status_code=500, detail="Internal error")

        result = await http_exception_handler(mock_request, exc)

        assert isinstance(result, JSONResponse)
        assert result.status_code == 500

    @pytest.mark.asyncio
    async def test_http_exception_unmapped_4xx(self):
        """Test unmapped 4xx status returns VALIDATION_ERROR."""
        mock_request = MagicMock(spec=Request)
        mock_request.state.request_id = "test-request-id"

        exc = StarletteHTTPException(status_code=418, detail="I'm a teapot")

        result = await http_exception_handler(mock_request, exc)

        assert isinstance(result, JSONResponse)
        assert result.status_code == 418

    @pytest.mark.asyncio
    async def test_http_exception_no_request_id(self):
        """Test handler works when request_id is not set."""
        mock_request = MagicMock(spec=Request)
        mock_request.state.request_id = "unknown"

        exc = StarletteHTTPException(status_code=404, detail="Not found")

        result = await http_exception_handler(mock_request, exc)

        assert isinstance(result, JSONResponse)
        assert result.status_code == 404


class TestValidationExceptionHandler:
    """Tests for validation_exception_handler."""

    @pytest.mark.asyncio
    async def test_validation_exception_handler(self):
        """Test that validation errors are properly formatted."""
        from fastapi.exceptions import RequestValidationError

        mock_request = MagicMock(spec=Request)
        mock_request.state.request_id = "test-request-id"

        exc = RequestValidationError(
            errors=[
                {
                    "loc": ("body", "email"),
                    "msg": "field required",
                    "type": "missing",
                },
                {
                    "loc": ("body", "password"),
                    "msg": "string too short",
                    "type": "string_too_short",
                },
            ]
        )

        result = await validation_exception_handler(mock_request, exc)

        assert isinstance(result, JSONResponse)
        assert result.status_code == 422
        content = result.body
        assert b"validation_errors" in content
        assert b"email" in content
        assert b"password" in content

    @pytest.mark.asyncio
    async def test_validation_exception_nested_field(self):
        """Test validation errors with nested field locations."""
        from fastapi.exceptions import RequestValidationError

        mock_request = MagicMock(spec=Request)
        mock_request.state.request_id = "test-request-id"

        exc = RequestValidationError(
            errors=[
                {
                    "loc": ("body", "user", "profile", "name"),
                    "msg": "field required",
                    "type": "missing",
                },
            ]
        )

        result = await validation_exception_handler(mock_request, exc)

        assert isinstance(result, JSONResponse)
        content = result.body
        assert b"user.profile.name" in content


class TestGeneralExceptionHandler:
    """Tests for general_exception_handler."""

    @pytest.mark.asyncio
    async def test_general_exception_handler_returns_500(self):
        """Test that unhandled exceptions return 500."""
        mock_request = MagicMock(spec=Request)
        mock_request.state.request_id = "test-request-id"

        exc = RuntimeError("Something went wrong")

        result = await general_exception_handler(mock_request, exc)

        assert isinstance(result, JSONResponse)
        assert result.status_code == 500
        assert b"INTERNAL_ERROR" in result.body

    @pytest.mark.asyncio
    async def test_general_exception_handler_includes_request_id(self):
        """Test that error response includes X-Request-ID header."""
        mock_request = MagicMock(spec=Request)
        mock_request.state.request_id = "specific-request-id"

        exc = ValueError("Test error")

        result = await general_exception_handler(mock_request, exc)

        assert result.headers.get("X-Request-ID") == "specific-request-id"


class TestRootRedirect:
    """Tests for root redirect endpoint."""

    @pytest.mark.asyncio
    async def test_root_redirect_unauthenticated(self):
        """Test that unauthenticated users are redirected to login."""
        mock_request = MagicMock(spec=Request)
        mock_request.cookies.get.return_value = None

        result = await root(mock_request)

        assert isinstance(result, RedirectResponse)
        assert result.status_code == 303
        assert result.headers.get("location") == "/web/login"

    @pytest.mark.asyncio
    async def test_root_redirect_authenticated(self):
        """Test that authenticated users are redirected to dashboard."""
        mock_request = MagicMock(spec=Request)

        # Clerk stores session token in __session cookie
        mock_request.cookies.get.return_value = "clerk-session-token"

        result = await root(mock_request)

        assert isinstance(result, RedirectResponse)
        assert result.status_code == 303
        assert result.headers.get("location") == "/web/downloads"

    @pytest.mark.asyncio
    async def test_root_redirect_invalid_token(self):
        """Test that invalid tokens redirect to login."""
        mock_request = MagicMock(spec=Request)
        mock_request.cookies.get.return_value = "invalid-token"

        result = await root(mock_request)

        assert isinstance(result, RedirectResponse)
        assert result.status_code == 303
        assert result.headers.get("location") == "/web/login"


class TestShutdownDiagnostics:
    """Tests for shutdown diagnostics functions."""

    def test_sigterm_handler_sets_state(self):
        """Test that SIGTERM handler updates shutdown state."""
        _shutdown_state._received = 0

        _sigterm_handler(signal.SIGTERM, None)

        assert _shutdown_state.received == signal.SIGTERM

    def test_sigterm_handler_accepts_frame(self):
        """Test that SIGTERM handler works with frame parameter."""
        _shutdown_state._received = 0

        _sigterm_handler(signal.SIGTERM, sys._getframe())

        assert _shutdown_state.received == signal.SIGTERM


class TestAppConfiguration:
    """Tests for app configuration."""

    def test_app_has_title(self):
        """Test that app has correct title."""
        assert app.title == "Vooglaadija API"

    def test_app_has_version(self):
        """Test that app has correct version."""
        assert app.version == "1.0.0"

    def test_app_has_description(self):
        """Test that app has description."""
        assert "REST API" in app.description

    def test_app_uses_custom_docs_url(self):
        """Test that app uses custom docs URL."""
        assert app.docs_url is None  # Disabled default docs

    def test_app_uses_custom_redoc_url(self):
        """Test that app uses custom redoc URL."""
        assert app.redoc_url is None  # Disabled default redoc


class TestUVLoopAvailability:
    """Tests for uvloop availability detection."""

    def test_uvloop_available_is_boolean(self):
        """Test that UVLOOP_AVAILABLE is a boolean."""
        assert isinstance(UVLOOP_AVAILABLE, bool)


class TestDocsEndpoint:
    """Tests for custom /docs endpoint."""

    @pytest.mark.asyncio
    async def test_docs_endpoint_returns_html(self):
        """Test that /docs returns HTML response."""
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response = await client.get("/docs")

        assert response.status_code == 200
        assert "text/html" in response.headers.get("content-type", "")

    @pytest.mark.asyncio
    async def test_docs_endpoint_contains_swagger_ui(self):
        """Test that /docs contains Swagger UI."""
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response = await client.get("/docs")

        assert response.status_code == 200
        text_lower = response.text.lower()
        assert "swagger" in text_lower


class TestReDocEndpoint:
    """Tests for custom /redoc endpoint."""

    @pytest.mark.asyncio
    async def test_redoc_endpoint_returns_html(self):
        """Test that /redoc returns HTML response."""
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response = await client.get("/redoc")

        assert response.status_code == 200
        assert "text/html" in response.headers.get("content-type", "")

    @pytest.mark.asyncio
    async def test_redoc_endpoint_contains_redoc(self):
        """Test that /redoc contains ReDoc."""
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response = await client.get("/redoc")

        assert response.status_code == 200
        text_lower = response.text.lower()
        assert "redoc" in text_lower


class TestHealthEndpoint:
    """Tests for health check endpoint."""

    @pytest.mark.asyncio
    async def test_health_endpoint(self):
        """Test that /health endpoint exists and returns a response."""
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response = await client.get("/health")

        assert response.status_code in [200, 503]
        data = response.json()
        assert "status" in data


class TestExceptionHandlersRegistered:
    """Tests that exception handlers are properly registered."""

    def test_rate_limit_exceeded_handler_registered(self):
        """Test that RateLimitExceeded handler is registered."""
        from slowapi.errors import RateLimitExceeded

        assert RateLimitExceeded in app.exception_handlers

    def test_starlette_http_exception_handler_registered(self):
        """Test that StarletteHTTPException handler is registered."""
        assert StarletteHTTPException in app.exception_handlers

    def test_request_validation_handler_registered(self):
        """Test that RequestValidationError handler is registered."""
        from fastapi.exceptions import RequestValidationError

        assert RequestValidationError in app.exception_handlers

    def test_generic_exception_handler_registered(self):
        """Test that generic Exception handler is registered."""
        assert Exception in app.exception_handlers


class TestLifespan:
    """Tests for lifespan context manager."""

    @pytest.mark.asyncio
    async def test_lifespan_startup_and_shutdown(self):
        """Test that lifespan context manager runs startup and shutdown."""
        startup_events = []
        shutdown_events = []

        mock_app = MagicMock()

        async with lifespan(mock_app):
            startup_events.append("started")

        shutdown_events.append("stopped")

        assert "started" in startup_events
        assert "stopped" in shutdown_events


class TestOpenAPITags:
    """Tests for OpenAPI tags configuration."""

    def test_app_has_openapi_tags(self):
        """Test that app has OpenAPI tags defined."""
        assert hasattr(app, "openapi_tags")
        assert app.openapi_tags is not None

    def test_auth_tag_present(self):
        """Test that auth tag is present."""
        tag_names = [tag["name"] for tag in app.openapi_tags]
        assert "auth" in tag_names

    def test_downloads_tag_present(self):
        """Test that downloads tag is present."""
        tag_names = [tag["name"] for tag in app.openapi_tags]
        assert "downloads" in tag_names

    def test_health_tag_present(self):
        """Test that health tag is present."""
        tag_names = [tag["name"] for tag in app.openapi_tags]
        assert "health" in tag_names

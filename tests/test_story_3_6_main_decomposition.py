"""Story 3.6 guardrails for app.main decomposition."""

import importlib
import re
from pathlib import Path
from uuid import UUID, uuid4

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from slowapi.errors import RateLimitExceeded
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.api.middleware.security_headers import add_security_headers
from app.auth import create_access_token
from app.main import app
from core.config import settings


@pytest.mark.unit
def test_main_py_is_thin_application_assembly() -> None:
    """The FastAPI entry point stays below the story line-count limit."""
    line_count = len(Path("app/main.py").read_text().splitlines())
    assert line_count < 150


@pytest.mark.unit
def test_main_py_no_longer_owns_extracted_implementation_symbols() -> None:
    """The FastAPI entry point does not define moved middleware, docs, or exceptions."""
    source = Path("app/main.py").read_text()
    forbidden_patterns = [
        r"class\s+RequestBodySizeMiddleware\b",
        r"class\s+PrometheusMiddleware\b",
        r"def\s+add_security_headers\b",
        r"def\s+add_request_id\b",
        r"def\s+custom_docs\b",
        r"def\s+custom_redoc\b",
        r"def\s+http_exception_handler\b",
        r"def\s+validation_exception_handler\b",
        r"def\s+general_exception_handler\b",
        r"def\s+_install_shutdown_diagnostics\b",
    ]
    for pattern in forbidden_patterns:
        assert re.search(pattern, source) is None


@pytest.mark.unit
def test_extracted_owner_modules_import_directly_and_keep_middleware_compatibility() -> None:
    """Focused owner modules import directly and the old middleware public import still works."""
    from app.api.middleware import PrometheusMiddleware
    from app.api.middleware.prometheus import PrometheusMiddleware as OwnedPrometheusMiddleware
    from app.api.middleware.request_body_size import RequestBodySizeMiddleware
    from app.api.middleware.request_id import add_request_id
    from app.api.middleware.security_headers import add_security_headers

    assert PrometheusMiddleware is OwnedPrometheusMiddleware
    assert RequestBodySizeMiddleware.MAX_BODY_SIZE == 1024 * 1024
    assert callable(add_request_id)
    assert callable(add_security_headers)


@pytest.mark.unit
def test_extracted_api_modules_do_not_import_main_or_create_global_app() -> None:
    """Extracted API modules do not import app.main or construct a FastAPI app."""
    module_paths = [
        Path("app/api/docs.py"),
        Path("app/api/exceptions.py"),
        Path("app/api/startup.py"),
    ]
    for path in module_paths:
        assert path.exists()
        source = path.read_text()
        assert "from app.main import" not in source
        assert "import app.main" not in source
        assert "FastAPI(" not in source
        importlib.import_module(".".join(path.with_suffix("").parts))


@pytest.mark.unit
@pytest.mark.asyncio
async def test_docs_and_redoc_return_expected_html_markers() -> None:
    """The custom docs routes still return Swagger UI and ReDoc HTML."""
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        docs_response = await client.get("/docs")
        redoc_response = await client.get("/redoc")

    assert docs_response.status_code == 200
    assert "text/html" in docs_response.headers["content-type"]
    assert "swagger" in docs_response.text.lower()
    assert redoc_response.status_code == 200
    assert "text/html" in redoc_response.headers["content-type"]
    assert "redoc" in redoc_response.text.lower()


@pytest.mark.unit
@pytest.mark.asyncio
async def test_self_hosted_docs_keep_local_assets_sri_and_matching_nonce() -> None:
    """Self-hosted docs keep local assets, Swagger SRI hashes, and a matching inline nonce."""
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get("/docs")

    csp = response.headers["Content-Security-Policy"]
    nonce_match = re.search(r"script-src 'self' 'nonce-([a-f0-9]{32})'", csp)
    assert nonce_match is not None
    nonce = nonce_match.group(1)
    assert "/static/swagger/swagger-ui-bundle.js" in response.text
    assert "/static/swagger/swagger-ui.css" in response.text
    assert "https://cdn.jsdelivr.net" not in response.text
    assert (
        "sha384-0028baa75a6060bac3a81329f501985abbdc1d527a5c16ac87977fb8722684d27a0092ae437ab3be434867ae18f9156d"
        in response.text
    )
    assert (
        "sha384-f50d9fa52fb1792e1f7c9ba09a827c28525fb895d01884eb3da6066e10ac72a5532876199917378c96f56c0237fbb93"
        in response.text
    )
    assert f'<script nonce="{nonce}">' in response.text


@pytest.mark.unit
@pytest.mark.asyncio
async def test_docs_cdn_fallback_keeps_versioned_urls_and_cdn_csp(monkeypatch, tmp_path) -> None:
    """Docs CDN fallback keeps pinned asset versions and its jsDelivr CSP allowance."""
    from app.api import docs as docs_module

    fallback_app = FastAPI(title="Fallback Docs", docs_url=None, redoc_url=None)
    fallback_app.middleware("http")(add_security_headers)
    monkeypatch.setattr(docs_module, "APP_DIR", tmp_path)
    docs_module.register_docs_routes(fallback_app)

    async with AsyncClient(
        transport=ASGITransport(app=fallback_app), base_url="http://test"
    ) as client:
        docs_response = await client.get("/docs")
        redoc_response = await client.get("/redoc")

    assert docs_response.status_code == 200
    assert "swagger-ui-dist@5.32.5/swagger-ui-bundle.js" in docs_response.text
    assert "swagger-ui-dist@5.32.5/swagger-ui.css" in docs_response.text
    assert "https://cdn.jsdelivr.net" in docs_response.headers["Content-Security-Policy"]
    assert redoc_response.status_code == 200
    assert "redoc@2.0.0-rc.70/bundles/redoc.standalone.js" in redoc_response.text
    assert "https://cdn.jsdelivr.net" in redoc_response.headers["Content-Security-Policy"]


@pytest.mark.unit
@pytest.mark.asyncio
async def test_security_headers_include_nonce_and_expected_headers() -> None:
    """Security middleware still adds CSP nonce and baseline hardening headers."""
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get("/docs")

    csp = response.headers["Content-Security-Policy"]
    assert re.search(r"script-src 'self' 'nonce-[a-f0-9]{32}'", csp)
    assert response.headers["X-Content-Type-Options"] == "nosniff"
    assert response.headers["X-Frame-Options"] == "DENY"
    assert response.headers["Referrer-Policy"] == "strict-origin-when-cross-origin"
    assert response.headers["Permissions-Policy"] == "camera=(), microphone=(), geolocation=()"


@pytest.mark.unit
@pytest.mark.asyncio
async def test_request_id_header_is_valid_uuid() -> None:
    """Request ID middleware still adds a UUID-shaped X-Request-ID response header."""
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get("/docs")

    assert str(UUID(response.headers["X-Request-ID"])) == response.headers["X-Request-ID"]


@pytest.mark.unit
@pytest.mark.asyncio
async def test_request_body_size_middleware_rejects_large_non_get_and_bypasses_safe_methods() -> (
    None
):
    """Request body limiting still rejects oversized writes and bypasses safe methods."""
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        large_body = b"x" * ((1024 * 1024) + 1)
        post_response = await client.post("/api/v1/auth/login", content=large_body)
        get_response = await client.request("GET", "/docs", content=large_body)
        head_response = await client.request("HEAD", "/docs", content=large_body)
        options_response = await client.request("OPTIONS", "/docs", content=large_body)

    assert post_response.status_code == 413
    assert post_response.json()["error"]["code"] == "VALIDATION_ERROR"
    assert "Request body too large" in post_response.json()["error"]["message"]
    assert get_response.status_code == 200
    assert head_response.status_code in {200, 405}
    assert options_response.status_code in {200, 405}


@pytest.mark.unit
@pytest.mark.asyncio
async def test_cors_preflight_preserves_story_configuration() -> None:
    """CORS preflight preserves configured origins, credentials, methods, and allowed headers."""
    allowed_origin = settings.cors_origins.split(",")[0]
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.options(
            "/api/v1/auth/login",
            headers={
                "Origin": allowed_origin,
                "Access-Control-Request-Method": "POST",
                "Access-Control-Request-Headers": "Content-Type, Authorization, X-CSRF-Token, X-Request-ID, HX-Request, HX-Target, HX-Current-URL",
            },
        )

    assert response.status_code == 200
    assert response.headers["access-control-allow-origin"] == allowed_origin
    assert response.headers["access-control-allow-credentials"] == "true"
    assert response.headers["access-control-allow-methods"] == "GET, POST, PUT, DELETE"
    allowed_headers = response.headers["access-control-allow-headers"]
    for header in [
        "Content-Type",
        "Authorization",
        "X-CSRF-Token",
        "X-Request-ID",
        "HX-Request",
        "HX-Target",
        "HX-Current-URL",
    ]:
        assert header in allowed_headers


@pytest.mark.unit
def test_expected_exception_handlers_are_registered() -> None:
    """The application registers all global exception handlers through the app instance."""
    from fastapi.exceptions import RequestValidationError

    assert RateLimitExceeded in app.exception_handlers
    assert StarletteHTTPException in app.exception_handlers
    assert RequestValidationError in app.exception_handlers
    assert Exception in app.exception_handlers


@pytest.mark.unit
@pytest.mark.asyncio
async def test_lifespan_cleans_up_worker_poller_when_context_raises(monkeypatch) -> None:
    """Lifespan shutdown cleanup still runs when serving exits through an exception."""
    from app.api import startup

    events = []
    poller = object()

    monkeypatch.setattr(startup, "init_metrics", lambda: events.append("metrics"))
    monkeypatch.setattr(
        startup,
        "verify_templates_and_static_assets",
        lambda: events.append("assets"),
    )
    monkeypatch.setattr(
        startup,
        "_install_shutdown_diagnostics",
        lambda: events.append("signals"),
    )
    monkeypatch.setattr(
        startup,
        "start_worker_health_poller",
        lambda: events.append("start_poller") or poller,
    )

    async def stop_worker_health_poller(received_poller) -> None:
        events.append(("stop_poller", received_poller))

    async def close_api_resources() -> None:
        events.append("close_resources")

    monkeypatch.setattr(startup, "stop_worker_health_poller", stop_worker_health_poller)
    monkeypatch.setattr(startup, "close_api_resources", close_api_resources)

    lifespan = startup.create_lifespan("test-version", uvloop_available=False)
    with pytest.raises(RuntimeError, match="serve failed"):
        async with lifespan(FastAPI()):
            raise RuntimeError("serve failed")

    assert events == [
        "metrics",
        "assets",
        "signals",
        "start_poller",
        ("stop_poller", poller),
        "close_resources",
    ]


@pytest.mark.unit
@pytest.mark.asyncio
async def test_root_redirects_missing_invalid_and_valid_tokens_unchanged() -> None:
    """The root route still redirects unauthenticated, invalid, and authenticated users correctly."""
    valid_token = create_access_token(uuid4())

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        missing_response = await client.get("/")
        invalid_response = await client.get("/", cookies={"access_token": "invalid-token"})
        valid_response = await client.get("/", cookies={"access_token": valid_token})

    assert missing_response.status_code == 303
    assert missing_response.headers["location"] == "/web/login"
    assert invalid_response.status_code == 303
    assert invalid_response.headers["location"] == "/web/login"
    assert valid_response.status_code == 303
    assert valid_response.headers["location"] == "/web/downloads"

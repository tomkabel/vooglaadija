"""FastAPI application entry point with structured logging and performance optimizations.

Features:
- structlog for structured JSON logging in production
- orjson for fast JSON serialization
- uvloop for improved async performance
- Sentry for error tracking (production only)
"""

import asyncio
import os
import signal
import threading
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

import structlog.contextvars
from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.openapi.docs import get_redoc_html, get_swagger_ui_html
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from slowapi.errors import RateLimitExceeded
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.middleware.base import BaseHTTPMiddleware

# Optional: uvloop for better async performance (installed separately)
try:
    import uvloop

    uvloop.install()
    UVLOOP_AVAILABLE = True
except ImportError:
    UVLOOP_AVAILABLE = False

from app.api.middleware import PrometheusMiddleware
from app.api.rate_limit_config import limiter, rate_limit_exceeded_handler
from app.api.routes import auth, downloads, health
from app.api.routes.chaos import router as chaos_router
from app.api.routes.metrics import router as metrics_router
from app.api.routes.sse import router as sse_router
from app.api.routes.web import router as web_router
from app.auth import verify_token
from app.logging_config import configure_logging, get_logger
from app.metrics import WORKER_STATUS, init_metrics
from app.schemas.error import ErrorCode, error_response_dict
from app.services.redis_client import check_worker_health
from core.config import settings

# Initialize structlog - must happen before any logging
configure_logging(log_level=os.environ.get("LOG_LEVEL", "INFO"))
logger = get_logger(__name__)

APP_VERSION = "1.0.0"


class _ShutdownState:
    """Thread-safe shutdown state tracker."""

    def __init__(self) -> None:
        self._received: int = 0
        self._lock = threading.Lock()

    @property
    def received(self) -> int:
        with self._lock:
            return self._received

    def set(self, signum: int) -> None:
        with self._lock:
            self._received = signum


_shutdown_state = _ShutdownState()


def _sigterm_handler(signum: int, frame: Any) -> None:
    """Handle SIGTERM/SIGINT for shutdown diagnostics."""
    _shutdown_state.set(signum)
    signal_name = signal.Signals(signum).name if hasattr(signal, "Signals") else str(signum)
    logger.warning(
        "shutdown_signal_received",
        signal=signal_name,
        signal_number=signum,
    )


def _install_shutdown_diagnostics() -> None:
    """Install shutdown signal handlers safely from main thread.

    This function should be called from inside the lifespan() startup routine
    after Uvicorn has installed its handlers. It handles:
    - Only registering from the main thread to avoid ValueError
    - Chaining to any existing handlers instead of replacing them
    """

    def _chained_sigterm_handler(signum: int, frame: Any) -> None:
        """Handler that calls previous handler and our diagnostics."""
        _shutdown_state.set(signum)
        signal_name = signal.Signals(signum).name if hasattr(signal, "Signals") else str(signum)
        logger.warning(
            "shutdown_signal_received",
            signal=signal_name,
            signal_number=signum,
        )
        prev_handler = prev_term_handler if signum == signal.SIGTERM else prev_int_handler
        if callable(prev_handler) and prev_handler is not _chained_sigterm_handler:
            try:
                prev_handler(signum, frame)
            except Exception:
                pass

    try:
        # Only install from main thread to avoid ValueError
        if threading.current_thread() is threading.main_thread():
            # Get current handler to potentially chain to it
            current_handler = signal.getsignal(signal.SIGTERM)
            # Only install if not already our chained handler
            if current_handler is not _chained_sigterm_handler:
                prev_term_handler = signal.signal(signal.SIGTERM, _chained_sigterm_handler)
            else:
                prev_term_handler = current_handler
            # Also register SIGINT
            current_int_handler = signal.getsignal(signal.SIGINT)
            if current_int_handler is not _chained_sigterm_handler:
                prev_int_handler = signal.signal(signal.SIGINT, _chained_sigterm_handler)
            else:
                prev_int_handler = current_int_handler
            logger.info("shutdown_diagnostics_installed")
    except ValueError:
        # Not running in main thread, skip signal handler installation
        logger.warning("shutdown_diagnostics_skipped_not_main_thread")


# Sentry initialization (production only)
if settings.environment == "production" and os.environ.get("SENTRY_DSN"):
    import sentry_sdk
    from sentry_sdk.integrations.fastapi import FastApiIntegration
    from sentry_sdk.integrations.redis import RedisIntegration
    from sentry_sdk.integrations.sqlalchemy import SqlalchemyIntegration

    sentry_sdk.init(
        dsn=os.environ["SENTRY_DSN"],
        integrations=[
            FastApiIntegration(transaction_style="endpoint"),
            SqlalchemyIntegration(),
            RedisIntegration(),
        ],
        traces_sample_rate=0.1,  # 10% of transactions for performance monitoring
        profiles_sample_rate=0.1,
        environment=settings.environment,
        release=f"vooglaadija@{APP_VERSION}",
    )
    logger.info("sentry_initialized", dsn_masked="***")


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Lifespan context manager for startup/shutdown events."""
    logger.info(
        "application_starting",
        version=APP_VERSION,
        environment=settings.environment,
        uvloop_available=UVLOOP_AVAILABLE,
    )
    init_metrics()

    # Validate critical assets exist at startup to fail fast with clear errors
    _template_dir = Path(__file__).resolve().parent / "templates"
    _static_dir = Path(__file__).resolve().parent / "static"
    if not _template_dir.exists():
        logger.error("templates_directory_missing", path=str(_template_dir))
    else:
        required_templates = ["base.html", "login.html", "register.html", "dashboard.html"]
        missing = [t for t in required_templates if not (_template_dir / t).exists()]
        if missing:
            logger.error("missing_templates", templates=missing, path=str(_template_dir))
        else:
            logger.info(
                "templates_verified", count=len(required_templates), path=str(_template_dir)
            )
    if not _static_dir.exists():
        logger.error("static_directory_missing", path=str(_static_dir))
    else:
        logger.info("static_directory_verified", path=str(_static_dir))

    # Install shutdown diagnostics after Uvicorn handlers are in place
    # This must happen after uvicorn imports the module but before handling requests
    _install_shutdown_diagnostics()

    # Start background worker health poller.
    # The WORKER_STATUS gauge (read by Grafana panel 10) is never written by
    # the worker process — it lives in the API process. This task periodically
    # checks Redis for worker:health:* keys and updates the gauge accordingly.
    # Interval (15s) is shorter than the Prometheus scrape interval (15s) so
    # the gauge value is always fresh when scraped.
    async def _poll_worker_health() -> None:
        """Poll Redis for worker heartbeats and update the WORKER_STATUS gauge.

        Uses exponential backoff on failure (1s→2s→4s→8s→15s max) to avoid
        log spam during sustained Redis outages, while returning to 15s
        normal interval on success.
        """
        backoff = 1
        max_interval = 15
        while True:
            try:
                is_healthy = await check_worker_health()
                WORKER_STATUS.set(1 if is_healthy else 0)
                backoff = 1
            except Exception:
                WORKER_STATUS.set(0)
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, max_interval)
                continue
            await asyncio.sleep(15)

    health_poller = asyncio.create_task(_poll_worker_health())

    yield

    health_poller.cancel()
    try:
        await health_poller
    except asyncio.CancelledError:
        pass

    WORKER_STATUS.set(0)  # Mark worker down on API shutdown
    WORKER_STATUS.set(0)

    # Clean up global pubsub service connection pool
    try:
        from app.services.pubsub_service import close_pubsub_service

        await close_pubsub_service()
    except Exception:
        pass

    logger.info(
        "application_shutting_down",
        shutdown_signal=_shutdown_state.received,
    )


app = FastAPI(
    title="Vooglaadija API",
    summary="Asynchronous API for authenticated video download jobs.",
    description=(
        "REST API for user authentication, creating download jobs, tracking job status, "
        "and retrieving processed files. Authentication uses bearer JWT access tokens."
    ),
    version=APP_VERSION,
    docs_url=None,  # Disable default docs to use custom
    redoc_url=None,  # Disable default redoc to use custom
    contact={
        "name": "Team 21",
        "url": "https://github.com/tomkabel/team21-vooglaadija",
    },
    license_info={
        "name": "GPLv3",
        "url": "https://www.gnu.org/licenses/gpl-3.0.html",
    },
    openapi_tags=[
        {
            "name": "auth",
            "description": "User registration, user authentication, token refresh, and current user profile.",
        },
        {
            "name": "downloads",
            "description": "Create, query, download, and delete media extraction jobs.",
        },
        {
            "name": "health",
            "description": "Service health and readiness checks.",
        },
    ],
    lifespan=lifespan,
)

redoc_dir = Path(__file__).resolve().parent / "static" / "redoc"
if redoc_dir.exists():
    app.mount("/static/redoc", StaticFiles(directory=str(redoc_dir)), name="redoc")

swagger_dir = Path(__file__).resolve().parent / "static" / "swagger"
if swagger_dir.exists():
    app.mount("/static/swagger", StaticFiles(directory=str(swagger_dir)), name="swagger")
else:
    logger.warning(f"Swagger static directory {swagger_dir} not found. Skipping mount.")


# Custom /docs route with self-hosted assets, SRI, and nonce
@app.get("/docs", include_in_schema=False)
async def custom_docs(request: Request):
    nonce = request.state.nonce
    swagger_dir = Path(__file__).resolve().parent / "static" / "swagger"
    if swagger_dir.exists():
        swagger_js_url = "/static/swagger/swagger-ui-bundle.js"
        swagger_css_url = "/static/swagger/swagger-ui.css"
    else:
        swagger_js_url = "https://cdn.jsdelivr.net/npm/swagger-ui-dist@5.32.5/swagger-ui-bundle.js"
        swagger_css_url = "https://cdn.jsdelivr.net/npm/swagger-ui-dist@5.32.5/swagger-ui.css"
    response = get_swagger_ui_html(
        openapi_url=app.openapi_url or "/openapi.json",
        title=app.title + " - API Docs",
        swagger_js_url=swagger_js_url,
        swagger_css_url=swagger_css_url,
    )
    html = bytes(response.body).decode()
    if swagger_dir.exists():
        html = html.replace(
            '<script src="/static/swagger/swagger-ui-bundle.js"></script>',
            '<script src="/static/swagger/swagger-ui-bundle.js" integrity="sha384-0028baa75a6060bac3a81329f501985abbdc1d527a5c16ac87977fb8722684d27a0092ae437ab3be434867ae18f9156d" crossorigin="anonymous"></script>',
        )
        html = html.replace(
            '<link rel="stylesheet" type="text/css" href="/static/swagger/swagger-ui.css">',
            '<link rel="stylesheet" type="text/css" href="/static/swagger/swagger-ui.css" integrity="sha384-f50d9fa52fb1792e1f7c9ba09a827c28525fb895d01884eb3da6066e10ac72a5532876199917378c96f56c0237fbb93" crossorigin="anonymous">',
        )
    html = html.replace("<script>\nconst ui =", f'<script nonce="{nonce}">\nconst ui =')
    # Add jsDelivr to CSP for Swagger UI CDN fallback
    response = HTMLResponse(html)
    response.headers["Content-Security-Policy"] = (
        f"default-src 'self'; "
        f"script-src 'self' 'nonce-{nonce}' https://cdn.jsdelivr.net; "
        f"style-src 'self' https://fonts.googleapis.com 'unsafe-inline' https://cdn.jsdelivr.net; "
        f"font-src 'self' https://fonts.gstatic.com; "
        f"img-src 'self' data: blob:; "
        f"connect-src 'self'; "
        f"frame-ancestors 'none'; "
        f"base-uri 'self'; "
        f"form-action 'self'"
    )
    return response


# Custom /redoc route with self-hosted assets and nonce
@app.get("/redoc", include_in_schema=False)
async def custom_redoc(request: Request):
    nonce = request.state.nonce
    redoc_dir = Path(__file__).resolve().parent / "static" / "redoc"
    if redoc_dir.exists():
        response = get_redoc_html(
            openapi_url=app.openapi_url or "/openapi.json",
            title=app.title + " - ReDoc",
            redoc_js_url="/static/redoc/redoc.standalone.js",
        )
        # Self-hosted: return with nonce only
        html = bytes(response.body).decode()
        html = html.replace("<script>\nconst ui =", f'<script nonce="{nonce}">\nconst ui =')
        return HTMLResponse(html)
    else:
        response = get_redoc_html(
            openapi_url=app.openapi_url or "/openapi.json",
            title=app.title + " - ReDoc",
            redoc_js_url="https://cdn.jsdelivr.net/npm/redoc@2.0.0-rc.70/bundles/redoc.standalone.js",
        )
        # CDN fallback: add jsdelivr to CSP
        html = bytes(response.body).decode()
        html = html.replace("<script>\nconst ui =", f'<script nonce="{nonce}">\nconst ui =')
        response = HTMLResponse(html)
        response.headers["Content-Security-Policy"] = (
            f"default-src 'self'; "
            f"script-src 'self' 'nonce-{nonce}' https://cdn.jsdelivr.net; "
            f"style-src 'self' https://fonts.googleapis.com 'unsafe-inline' https://cdn.jsdelivr.net; "
            f"font-src 'self' https://fonts.gstatic.com; "
            f"img-src 'self' data: blob:; "
            f"connect-src 'self'; "
            f"frame-ancestors 'none'; "
            f"base-uri 'self'; "
            f"form-action 'self'"
        )
        return response


class RequestBodySizeMiddleware(BaseHTTPMiddleware):
    """Limit incoming request body size to prevent resource exhaustion.

    JSON/POST bodies are limited to 1MB. File download requests (GET/HEAD)
    are not subject to this limit since they don't carry request bodies.
    """

    MAX_BODY_SIZE = 1024 * 1024  # 1MB

    async def dispatch(self, request: Request, call_next):
        if request.method in ("GET", "HEAD", "OPTIONS"):
            return await call_next(request)

        content_length_str = request.headers.get("content-length")
        if content_length_str:
            try:
                content_length = int(content_length_str)
                if content_length > self.MAX_BODY_SIZE:
                    return JSONResponse(
                        status_code=413,
                        content=error_response_dict(
                            ErrorCode.VALIDATION_ERROR,
                            f"Request body too large. Maximum size is {self.MAX_BODY_SIZE // 1024}KB.",
                        ),
                    )
            except (ValueError, TypeError):
                pass

        return await call_next(request)


app.add_middleware(RequestBodySizeMiddleware)
app.add_middleware(PrometheusMiddleware)
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, rate_limit_exceeded_handler)


# Security headers middleware (CSP and other best practices)
@app.middleware("http")
async def add_security_headers(request: Request, call_next: Any) -> Any:
    """Add Content-Security-Policy and other security headers to all responses."""
    # Generate a secure nonce for inline script tags
    nonce = uuid.uuid4().hex
    request.state.nonce = nonce

    response = await call_next(request)

    # CSP: Allow same-origin scripts with nonce for inline scripts, allow Google Fonts CDN
    response.headers["Content-Security-Policy"] = (
        f"default-src 'self'; "
        f"script-src 'self' 'nonce-{nonce}'; "
        f"style-src 'self' https://fonts.googleapis.com 'unsafe-inline'; "
        f"font-src 'self' https://fonts.gstatic.com; "
        f"img-src 'self' data: blob:; "
        f"connect-src 'self'; "
        f"frame-ancestors 'none'; "
        f"base-uri 'self'; "
        f"form-action 'self'"
    )
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
    response.headers["Permissions-Policy"] = "camera=(), microphone=(), geolocation=()"

    return response


# Request ID / correlation ID middleware for observability
@app.middleware("http")
async def add_request_id(request: Request, call_next: Any) -> Any:
    """Add a unique request ID to each request and bind it to structured logs."""
    request_id = str(uuid.uuid4())
    request.state.request_id = request_id
    # Bind correlation ID to structlog so every log in this request includes it
    structlog.contextvars.bind_contextvars(request_id=request_id)

    response = await call_next(request)
    response.headers["X-Request-ID"] = request_id
    # Unbind after request to avoid leaking across async boundaries
    structlog.contextvars.unbind_contextvars("request_id")
    return response


# Configure CORS
origins = settings.cors_origins.split(",")
app.add_middleware(
    CORSMiddleware,
    allow_origins=origins,
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "DELETE"],
    allow_headers=[
        "Content-Type",
        "Authorization",
        "X-CSRF-Token",
        "X-Request-ID",
        "HX-Request",
        "HX-Target",
        "HX-Current-URL",
    ],
)

app.mount(
    "/static", StaticFiles(directory=str(Path(__file__).resolve().parent / "static")), name="static"
)


# Global exception handlers
@app.exception_handler(StarletteHTTPException)
async def http_exception_handler(request: Request, exc: StarletteHTTPException) -> JSONResponse:
    """Handle HTTP exceptions with standardized error response."""
    # Get request_id if available
    request_id = getattr(request.state, "request_id", "unknown")

    # Map status codes to error codes
    error_code_map = {
        400: ErrorCode.VALIDATION_ERROR,
        401: ErrorCode.UNAUTHORIZED,
        403: ErrorCode.FORBIDDEN,
        404: ErrorCode.NOT_FOUND,
        405: ErrorCode.VALIDATION_ERROR,
        406: ErrorCode.VALIDATION_ERROR,
        409: ErrorCode.RESOURCE_CONFLICT,
        415: ErrorCode.VALIDATION_ERROR,
        422: ErrorCode.VALIDATION_ERROR,
        429: ErrorCode.RATE_LIMIT_EXCEEDED,
        500: ErrorCode.INTERNAL_ERROR,
        503: ErrorCode.SERVICE_UNAVAILABLE,
    }

    # Default to VALIDATION_ERROR for unmapped 4xx, INTERNAL_ERROR for 5xx
    code = error_code_map.get(
        exc.status_code,
        ErrorCode.VALIDATION_ERROR if 400 <= exc.status_code < 500 else ErrorCode.INTERNAL_ERROR,
    )

    logger.warning(
        "http_exception",
        status_code=exc.status_code,
        error_code=code.value,
        detail=str(exc.detail),
        request_id=request_id,
    )

    return JSONResponse(
        status_code=exc.status_code,
        content=error_response_dict(code, str(exc.detail)),
        headers=exc.headers or None,
    )


@app.exception_handler(RequestValidationError)
async def validation_exception_handler(
    request: Request, exc: RequestValidationError
) -> JSONResponse:
    """Handle validation errors with standardized error response."""
    request_id = getattr(request.state, "request_id", "unknown")

    # Extract validation errors details
    errors = []
    for error in exc.errors():
        errors.append(
            {
                "field": ".".join(str(loc) for loc in error["loc"] if loc != "body"),
                "message": error["msg"],
                "type": error["type"],
            },
        )

    logger.warning(
        "validation_error",
        error_count=len(errors),
        request_id=request_id,
    )

    return JSONResponse(
        status_code=422,
        content=error_response_dict(
            ErrorCode.VALIDATION_ERROR,
            "Request validation failed",
            details={"validation_errors": errors},
        ),
    )


@app.exception_handler(Exception)
async def general_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    """Handle unexpected exceptions with standardized error response."""
    request_id = getattr(request.state, "request_id", "unknown")
    logger.error(
        "unhandled_exception",
        exception_type=type(exc).__name__,
        exception_message=str(exc),
        request_id=request_id,
        exc_info=True,
    )

    # Sentry will automatically capture the exception if configured
    return JSONResponse(
        status_code=500,
        content=error_response_dict(ErrorCode.INTERNAL_ERROR, "An internal error occurred"),
        headers={"X-Request-ID": request_id},
    )


app.include_router(auth.router, prefix="/api/v1")
app.include_router(downloads.router, prefix="/api/v1")
app.include_router(health.router)
app.include_router(metrics_router)

# Chaos injection API — always registered, feature-flag gated inside handlers

app.include_router(chaos_router)
if settings.feature_chaos_api_enabled:
    logger.info("chaos_api_enabled", feature_chaos_api_enabled=True)
else:
    logger.info("chaos_api_registered_but_disabled", feature_chaos_api_enabled=False)

# Web/HTMX routes - SSE mounted FIRST so /web/downloads/stream is matched before /web/downloads
# Both routers have their own prefix="/web" defined, so include without additional prefix
app.include_router(sse_router)  # prefix="/web", routes: /web/downloads/stream
app.include_router(web_router)  # prefix="/web", routes: /web/login, /web/downloads, etc.


@app.get("/")
async def root(request: Request) -> RedirectResponse:
    """Redirect root to login or dashboard based on auth status."""
    # Check if user has a valid token in cookies
    token = request.cookies.get("access_token")
    if token:
        payload = verify_token(token)
        if payload is not None:
            # User is authenticated, redirect to dashboard
            return RedirectResponse(url="/web/downloads", status_code=303)

    # Not authenticated, redirect to login
    return RedirectResponse(url="/web/login", status_code=303)

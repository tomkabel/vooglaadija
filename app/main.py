"""FastAPI application assembly for Vooglaadija."""

import os
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles

try:
    import uvloop

    uvloop.install()
    UVLOOP_AVAILABLE = True
except ImportError:
    UVLOOP_AVAILABLE = False

from app.api.docs import mount_docs_static, register_docs_routes
from app.api.exceptions import register_exception_handlers
from app.api.middleware import (
    PrometheusMiddleware,
    RequestBodySizeMiddleware,
    add_request_id,
    add_security_headers,
)
from app.api.rate_limit_config import limiter
from app.api.routes import auth, downloads, health
from app.api.routes.chaos import router as chaos_router
from app.api.routes.metrics import router as metrics_router
from app.api.routes.sse import router as sse_router
from app.api.routes.web import router as web_router
from app.api.startup import create_lifespan, initialize_sentry
from app.auth import verify_token
from core.config import settings
from core.logging_config import configure_logging, get_logger

configure_logging(log_level=os.environ.get("LOG_LEVEL", "INFO"))
logger = get_logger(__name__)

APP_VERSION = "1.0.0"

initialize_sentry(APP_VERSION)
lifespan = create_lifespan(APP_VERSION, UVLOOP_AVAILABLE)

app = FastAPI(
    title="Vooglaadija API",
    summary="Asynchronous API for authenticated video download jobs.",
    description=(
        "REST API for user authentication, creating download jobs, tracking job status, "
        "and retrieving processed files. Authentication uses bearer JWT access tokens."
    ),
    version=APP_VERSION,
    docs_url=None,
    redoc_url=None,
    contact={"name": "Team 21", "url": "https://github.com/tomkabel/team21-vooglaadija"},
    license_info={"name": "GPLv3", "url": "https://www.gnu.org/licenses/gpl-3.0.html"},
    openapi_tags=[
        {
            "name": "auth",
            "description": "User registration, user authentication, token refresh, and current user profile.",
        },
        {
            "name": "downloads",
            "description": "Create, query, download, and delete media extraction jobs.",
        },
        {"name": "health", "description": "Service health and readiness checks."},
    ],
    lifespan=lifespan,
)

# Startup order: configure logging, initialize metrics, verify templates/static
# assets, install shutdown diagnostics, start worker health polling, serve requests.
# Middleware runtime order: CORS, request ID, security headers, Prometheus metrics,
# then request body limiting. Security headers create the docs nonce before handlers run.
app.add_middleware(RequestBodySizeMiddleware)
app.add_middleware(PrometheusMiddleware)
app.middleware("http")(add_security_headers)
app.middleware("http")(add_request_id)
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins.split(","),
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

app.state.limiter = limiter
mount_docs_static(app)
app.mount(
    "/static", StaticFiles(directory=str(Path(__file__).resolve().parent / "static")), name="static"
)
register_docs_routes(app)
register_exception_handlers(app)

app.include_router(auth.router, prefix="/api/v1")
app.include_router(downloads.router, prefix="/api/v1")
app.include_router(health.router)
app.include_router(metrics_router)
app.include_router(chaos_router)
if settings.feature_chaos_api_enabled:
    logger.info("chaos_api_enabled", feature_chaos_api_enabled=True)
else:
    logger.info("chaos_api_registered_but_disabled", feature_chaos_api_enabled=False)

app.include_router(sse_router)
app.include_router(web_router)


@app.get("/")
async def root(request: Request) -> RedirectResponse:
    """Redirect root to login or dashboard based on auth status."""
    token = request.cookies.get("access_token")
    if token and verify_token(token) is not None:
        return RedirectResponse(url="/web/downloads", status_code=303)
    return RedirectResponse(url="/web/login", status_code=303)

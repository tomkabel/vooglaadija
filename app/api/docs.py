"""Custom API documentation routes and static asset mounts."""

from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.openapi.docs import get_redoc_html, get_swagger_ui_html
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles

from core.logging_config import get_logger

logger = get_logger(__name__)
APP_DIR = Path(__file__).resolve().parents[1]


def mount_docs_static(app: FastAPI) -> None:
    """Mount self-hosted Swagger and ReDoc assets when available."""
    redoc_dir = APP_DIR / "static" / "redoc"
    if redoc_dir.exists():
        app.mount("/static/redoc", StaticFiles(directory=str(redoc_dir)), name="redoc")

    swagger_dir = APP_DIR / "static" / "swagger"
    if swagger_dir.exists():
        app.mount("/static/swagger", StaticFiles(directory=str(swagger_dir)), name="swagger")
    else:
        logger.warning(f"Swagger static directory {swagger_dir} not found. Skipping mount.")


def register_docs_routes(app: FastAPI) -> None:
    """
    Register custom Swagger UI and ReDoc routes.

    The routes use local documentation assets when available and fall back to jsDelivr assets otherwise. Generated pages include request-specific script nonces and apply a Content Security Policy when CDN assets are used.
    """

    @app.get("/docs", include_in_schema=False)
    async def custom_docs(request: Request) -> HTMLResponse:
        """
        Generate the Swagger UI API documentation page.

        Parameters:
                request (Request): The incoming request providing the application OpenAPI configuration and security nonce.

        Returns:
                HTMLResponse: The rendered Swagger UI page, using local assets when available and CDN assets with an appropriate content security policy otherwise.
        """
        nonce = request.state.nonce
        swagger_dir = APP_DIR / "static" / "swagger"
        if swagger_dir.exists():
            swagger_js_url = "/static/swagger/swagger-ui-bundle.js"
            swagger_css_url = "/static/swagger/swagger-ui.css"
        else:
            swagger_js_url = (
                "https://cdn.jsdelivr.net/npm/swagger-ui-dist@5.32.5/swagger-ui-bundle.js"
            )
            swagger_css_url = "https://cdn.jsdelivr.net/npm/swagger-ui-dist@5.32.5/swagger-ui.css"

        response = get_swagger_ui_html(
            openapi_url=request.app.openapi_url or "/openapi.json",
            title=request.app.title + " - API Docs",
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
            html = html.replace(
                '<link type="text/css" rel="stylesheet" href="/static/swagger/swagger-ui.css">',
                '<link type="text/css" rel="stylesheet" href="/static/swagger/swagger-ui.css" integrity="sha384-f50d9fa52fb1792e1f7c9ba09a827c28525fb895d01884eb3da6066e10ac72a5532876199917378c96f56c0237fbb93" crossorigin="anonymous">',
            )
        html = _inject_inline_script_nonce(html, nonce)
        docs_response = HTMLResponse(html)
        if not swagger_dir.exists():
            docs_response.headers["Content-Security-Policy"] = _cdn_docs_csp(nonce)
        return docs_response

    @app.get("/redoc", include_in_schema=False)
    async def custom_redoc(request: Request) -> HTMLResponse:
        """Generate the ReDoc API documentation page using local or CDN assets."""
        nonce = request.state.nonce
        redoc_dir = APP_DIR / "static" / "redoc"
        if redoc_dir.exists():
            response = get_redoc_html(
                openapi_url=request.app.openapi_url or "/openapi.json",
                title=request.app.title + " - ReDoc",
                redoc_js_url="/static/redoc/redoc.standalone.js",
            )
            html = bytes(response.body).decode()
            html = _inject_inline_script_nonce(html, nonce)
            return HTMLResponse(html)

        response = get_redoc_html(
            openapi_url=request.app.openapi_url or "/openapi.json",
            title=request.app.title + " - ReDoc",
            redoc_js_url="https://cdn.jsdelivr.net/npm/redoc@2.0.0-rc.70/bundles/redoc.standalone.js",
        )
        html = bytes(response.body).decode()
        html = _inject_inline_script_nonce(html, nonce)
        redoc_response = HTMLResponse(html)
        redoc_response.headers["Content-Security-Policy"] = _cdn_docs_csp(nonce)
        return redoc_response


def _inject_inline_script_nonce(html: str, nonce: str) -> str:
    """
    Add a nonce attribute to FastAPI-generated inline documentation scripts.

    Parameters:
        html (str): Generated documentation HTML.
        nonce (str): Nonce value to add to matching inline script tags.

    Returns:
        str: Documentation HTML with the nonce added to matching inline scripts.
    """
    return html.replace(
        "<script>\n    const ui =",
        f'<script nonce="{nonce}">\n    const ui =',
    ).replace("<script>\nconst ui =", f'<script nonce="{nonce}">\nconst ui =')


def _cdn_docs_csp(nonce: str) -> str:
    """Return the docs CSP that permits the existing jsDelivr fallback."""
    return (
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

"""Aggregate web router and compatibility exports."""

from fastapi import APIRouter

from app.api.routes.web import (
    web_auth,
    web_dashboard,
    web_downloads,
    web_settings,
    web_terms,
)
from app.api.routes.web.web_helpers import (  # noqa: F401
    _change_password_response,
    _demo_user_or_raise,
    _downloads_base_path,
    _htmx_or_redirect,
    _login_success_response,
    _prime_demo_jobs,
    _register_success_response,
    _register_user_or_error_response,
    _resolve_login_errors,
    _resolve_register_errors,
    _resolve_settings_errors,
    _validate_redirect_url,
    get_csrf_token,
    get_template_context,
    is_htmx_request,
    logger,
    render_csrf_page,
    rotate_csrf_token,
    set_csrf_token_cookie,
    settings,
    templates,
    validate_csrf_token,
)
from app.api.routes.web_helpers import _success_html  # noqa: F401

router = APIRouter(prefix="/web", tags=["web"])
router.include_router(web_auth.router)
router.include_router(web_downloads.router)
router.include_router(web_dashboard.router)
router.include_router(web_settings.router)
router.include_router(web_terms.router)

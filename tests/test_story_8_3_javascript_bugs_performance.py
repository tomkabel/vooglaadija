"""Regression tests for Story 8.3 frontend bug and performance fixes."""

import re
from pathlib import Path

import pytest
from httpx import ASGITransport, AsyncClient

from app.main import app
from tests.conftest import create_test_user_and_login

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SPRITE_URL = "/static/icons/sprite.svg"


def _source(path: str) -> str:
    return (PROJECT_ROOT / path).read_text(encoding="utf-8")


def _dashboard_js() -> str:
    return _source("app/static/js/dashboard.js")


def _all_icon_reference_sources() -> str:
    checked_paths = [
        "app/templates/base.html",
        "app/templates/chaos-lab.html",
        "app/templates/dashboard.html",
        "app/templates/login.html",
        "app/templates/register.html",
        "app/templates/settings.html",
        "app/templates/partials/_download_item.html",
        "app/templates/partials/_download_list.html",
        "app/static/js/dashboard.js",
    ]
    return "\n".join(_source(path) for path in checked_paths)


@pytest.mark.unit
def test_download_form_submit_state_is_scoped_to_download_form():
    """Dashboard submit re-enable handling only targets the download form request."""
    dashboard = _source("app/templates/dashboard.html")
    source = _dashboard_js()

    assert 'id="download-form"' in dashboard
    assert 'form[hx-post="/web/downloads"]' in source
    assert "function getDownloadForm()" in source
    assert "function isDownloadFormRequest(evt)" in source
    assert "evt.detail.target" in source
    assert "#download-form" in source
    assert "const form =\n      evt.detail.elt && evt.detail.elt.tagName === 'FORM'" not in source


@pytest.mark.unit
def test_skeleton_removal_is_driven_by_download_row_mutations():
    """The skeleton clears when the download rows child count changes."""
    source = _dashboard_js()

    assert "MutationObserver" in source
    assert "let skeletonObserver = null;" in source
    assert "const initialChildCount = rows.childElementCount;" in source
    assert "rows.childElementCount !== initialChildCount" in source
    assert "skeletonObserver.disconnect()" in source
    assert not re.search(r"setTimeout\([^)]*5000", source, flags=re.DOTALL)
    assert "const skelTimer = setTimeout" not in source


@pytest.mark.unit
def test_sse_extension_is_loaded_only_by_dashboard_template():
    """Base keeps global scripts, while dashboard owns the SSE extension script."""
    base = _source("app/templates/base.html")
    dashboard = _source("app/templates/dashboard.html")

    assert '<script src="/static/js/sse.js"></script>' not in base
    assert '<script src="/static/js/toast.js" defer></script>' in base
    assert '<script src="/static/js/auth.js" defer></script>' in base
    assert '<script src="/static/js/htmx-error-handler.js" defer></script>' in base
    assert '<script src="/static/js/sse.js"></script>' in dashboard
    assert '<script src="/static/js/dashboard.js" defer></script>' in dashboard


@pytest.mark.unit
def test_fonts_load_non_blocking_with_noscript_fallback():
    """Google Fonts CSS uses the non-blocking print media pattern."""
    base = _source("app/templates/base.html")

    assert 'href="https://fonts.googleapis.com"' in base
    assert 'href="https://fonts.gstatic.com"' in base
    assert 'media="print"' in base
    assert "onload=\"this.media='all'\"" in base
    assert "<noscript>" in base
    assert 'rel="preload"\n      href="https://fonts.googleapis.com/css2?' not in base


@pytest.mark.unit
def test_head_scripts_do_not_depend_on_document_body_before_body_exists():
    """Head-time HTMX CSRF wiring does not require document.body to exist yet."""
    base = _source("app/templates/base.html")
    head = base.split("<body", maxsplit=1)[0]

    assert 'document.addEventListener("htmx:configRequest"' in head
    assert 'document.body.addEventListener("htmx:configRequest"' not in head


@pytest.mark.unit
def test_svg_sprite_is_externalized_and_all_references_use_static_sprite():
    """The inline sprite is removed and active icon references use the external sprite."""
    base = _source("app/templates/base.html")
    sprite = _source("app/static/icons/sprite.svg")
    references = _all_icon_reference_sources()

    assert "<symbol id=" not in base
    assert '<use href="#icon' not in references
    assert f'<use href="{SPRITE_URL}#icon-' in references
    for icon_id in [
        "icon-play",
        "icon-video",
        "icon-download",
        "icon-trash",
        "icon-mail",
        "icon-lock",
        "icon-user",
        "icon-check",
        "icon-clock",
        "icon-alert",
        "icon-arrow-right",
        "icon-settings",
        "icon-link",
        "icon-logout",
        "icon-warning",
        "icon-shield",
        "icon-spinner",
        "icon-bolt",
        "icon-x",
        "icon-refresh",
    ]:
        assert f'id="{icon_id}"' in sprite


@pytest.mark.unit
def test_duplicate_cleanup_uses_set_based_tracking():
    """Duplicate row cleanup tracks seen job IDs with a Set."""
    source = _dashboard_js()

    assert "const seenJobIds = new Set();" in source
    assert "seenJobIds.has(id)" in source
    assert "seenJobIds.add(id)" in source
    assert "const ids = {}" not in source
    assert "ids[id]" not in source


@pytest.mark.unit
def test_reconnect_banner_retry_uses_csp_compliant_event_listener():
    """The reconnect retry button avoids CSP-blocked inline JavaScript handlers."""
    source = _dashboard_js()

    assert 'onclick="location.reload()"' not in source
    assert "data-sse-retry" in source
    assert "window.location.reload();" in source


@pytest.mark.unit
@pytest.mark.asyncio
async def test_dashboard_route_still_loads_dashboard_assets():
    """The rendered dashboard keeps dashboard JS and owns the SSE extension."""
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        access_token = await create_test_user_and_login(client)
        response = await client.get("/web/downloads", cookies={"__Host-access_token": access_token})

    assert response.status_code == 200
    assert '<script src="/static/js/sse.js"></script>' in response.text
    assert '<script src="/static/js/dashboard.js" defer></script>' in response.text


@pytest.mark.unit
@pytest.mark.asyncio
async def test_dashboard_route_renders_scoped_download_form_contract():
    """The rendered dashboard exposes the scoped HTMX download form contract."""
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        access_token = await create_test_user_and_login(client)
        response = await client.get("/web/downloads", cookies={"__Host-access_token": access_token})

    assert response.status_code == 200
    assert 'id="download-form"' in response.text
    assert 'hx-post="/web/downloads"' in response.text
    assert 'hx-target="#download-rows"' in response.text
    assert 'hx-indicator="#submit-spinner"' in response.text
    assert 'hx-ext="sse"' in response.text
    assert 'sse-connect="/web/downloads/stream"' in response.text


@pytest.mark.unit
@pytest.mark.asyncio
async def test_non_dashboard_pages_do_not_render_sse_extension():
    """Non-dashboard rendered pages do not load the dashboard-only SSE extension."""
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        login_response = await client.get("/web/login")
        register_response = await client.get("/web/register")
        access_token = await create_test_user_and_login(client)
        settings_response = await client.get(
            "/web/settings", cookies={"__Host-access_token": access_token}
        )

    for response in (login_response, register_response, settings_response):
        assert response.status_code == 200
        assert '<script src="/static/js/sse.js"></script>' not in response.text
        assert 'hx-ext="sse"' not in response.text


@pytest.mark.unit
@pytest.mark.asyncio
async def test_csp_allows_required_google_fonts_onload_handler():
    """The CSP permits only the hashed Google Fonts media-switch onload handler."""
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get("/web/login")

    assert response.status_code == 200
    csp = response.headers["Content-Security-Policy"]
    assert "'unsafe-hashes'" in csp
    assert "'sha256-MhtPZXr7+LpJUY5qtMutB+qWfQtMaPccfe7QXtCcEYc='" in csp
    assert "script-src 'self' 'nonce-" in csp


@pytest.mark.unit
@pytest.mark.asyncio
async def test_static_sprite_response_is_immutable():
    """The external sprite is served with immutable long-lived caching."""
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get(SPRITE_URL)

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("image/svg+xml")
    cache_control = response.headers["cache-control"]
    assert "immutable" in cache_control
    assert "max-age=31536000" in cache_control


@pytest.mark.unit
@pytest.mark.asyncio
async def test_missing_static_sprite_variant_returns_404():
    """Missing icon sprite files return the normal static-file 404."""
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get("/static/icons/missing.svg")

    assert response.status_code == 404

"""Accessibility regression tests for Story 8.6."""

import re
from pathlib import Path

import pytest
from httpx import ASGITransport, AsyncClient

from app.main import app
from tests.conftest import create_test_user_and_login

PROJECT_ROOT = Path(__file__).resolve().parents[1]

AUDITED_SOURCE_PATHS = [
    "frontend/css/src/styles.css",
    "app/templates/base.html",
    "app/templates/login.html",
    "app/templates/register.html",
    "app/templates/dashboard.html",
    "app/templates/settings.html",
    "app/templates/partials/_download_list.html",
    "app/templates/partials/_download_item.html",
    "app/templates/partials/_chaos_status.html",
]

CONTRAST_SOURCE_PATHS = [
    *AUDITED_SOURCE_PATHS,
    "app/static/js/dashboard.js",
]


def _source(path: str) -> str:
    return (PROJECT_ROOT / path).read_text(encoding="utf-8")


def _hex_to_rgb(hex_color: str) -> tuple[float, float, float]:
    value = hex_color.lstrip("#")
    return tuple(int(value[index : index + 2], 16) / 255 for index in (0, 2, 4))


def _linearize(channel: float) -> float:
    if channel <= 0.03928:
        return channel / 12.92
    return ((channel + 0.055) / 1.055) ** 2.4


def _relative_luminance(hex_color: str) -> float:
    red, green, blue = (_linearize(channel) for channel in _hex_to_rgb(hex_color))
    return 0.2126 * red + 0.7152 * green + 0.0722 * blue


def _contrast_ratio(foreground: str, background: str) -> float:
    fg_luminance = _relative_luminance(foreground)
    bg_luminance = _relative_luminance(background)
    lighter = max(fg_luminance, bg_luminance)
    darker = min(fg_luminance, bg_luminance)
    return (lighter + 0.05) / (darker + 0.05)


def _custom_property(source: str, name: str) -> str:
    match = re.search(rf"--color-{re.escape(name)}:\s*(#[0-9a-fA-F]{{6}});", source)
    assert match, f"Missing --color-{name}"
    return match.group(1)


def _index_after(source: str, needle: str, start_marker: str) -> int:
    start = source.index(start_marker)
    return source.index(needle, start)


def _assert_no_low_contrast_tokens(response_text: str) -> None:
    assert "text-gray-500" not in response_text
    assert "placeholder-gray-500" not in response_text


def _assert_field_error_contract(response_text: str, error_id: str, message: str) -> None:
    assert f'id="{error_id}"' in response_text
    assert f'aria-describedby="{error_id}"' in response_text
    assert f'aria-errormessage="{error_id}"' in response_text
    assert 'role="alert"' in response_text
    assert message in response_text


@pytest.mark.unit
def test_audited_sources_do_not_use_low_contrast_gray_500_text():
    """Audited pages and shared helpers avoid low-contrast gray-500 foregrounds."""
    offenders = [
        f"{path}: {line.strip()}"
        for path in CONTRAST_SOURCE_PATHS
        for line in _source(path).splitlines()
        if "text-gray-500" in line or "placeholder-gray-500" in line
    ]

    assert offenders == []


@pytest.mark.unit
def test_deployed_css_does_not_retain_low_contrast_gray_500_utility():
    """Tailwind deploy output must not keep the removed gray-500 text utility."""
    assert ".text-gray-500{" not in _source("app/static/css/styles.css")
    assert "'text-gray-500'" not in _source("frontend/tailwind.config.js")


@pytest.mark.unit
def test_selected_gray_tokens_meet_wcag_aa_on_dark_surfaces():
    """Muted text tokens keep at least 4.5:1 contrast on the app surfaces."""
    css = _source("frontend/css/src/styles.css")
    surfaces = [
        _custom_property(css, "surface-900"),
        _custom_property(css, "surface-800"),
        _custom_property(css, "surface-700"),
    ]
    tailwind_gray_400 = "#9ca3af"
    tailwind_gray_300 = "#d1d5db"

    for surface in surfaces:
        assert _contrast_ratio(tailwind_gray_400, surface) >= 4.5
        assert _contrast_ratio(tailwind_gray_300, surface) >= 4.5


@pytest.mark.unit
def test_shared_interactive_classes_have_visible_focus_visible_styles():
    """Shared buttons and form controls expose visible keyboard focus rings."""
    css = _source("frontend/css/src/styles.css")

    for selector in [
        ".btn-primary",
        ".btn-secondary",
        ".btn-danger",
        ".btn-danger-solid",
        ".btn-ghost",
        ".download-btn",
        ".form-input",
    ]:
        block = re.search(rf"{re.escape(selector)}\s*\{{(?P<body>.*?)\n  \}}", css, re.DOTALL)
        assert block, f"Missing {selector}"
        assert "focus-visible:outline-none" in block.group("body")
        assert "focus-visible:ring-2" in block.group("body")
        assert "focus-visible:ring-offset-2" in block.group("body")


@pytest.mark.unit
def test_template_specific_interactive_elements_have_focus_visible_classes():
    """Template links that bypass shared classes still get visible focus treatment."""
    expected_focus_classes = {
        "app/templates/base.html": [
            "group focus-visible:outline-none focus-visible:ring-2",
            "hover:text-amber-400 focus-visible:text-amber-300",
            "data-modal-cancel",
            "data-modal-confirm",
        ],
        "app/templates/login.html": [
            "hover:text-amber-300 focus-visible:text-amber-300",
            "Guest Demo",
            "Create one",
        ],
        "app/templates/register.html": [
            "hover:text-amber-300 focus-visible:text-amber-300",
            "Sign in",
        ],
    }

    for path, snippets in expected_focus_classes.items():
        source = _source(path)
        for snippet in snippets:
            assert snippet in source


@pytest.mark.unit
def test_keyboard_flow_sources_avoid_positive_tabindex_and_focus_traps():
    """Audited templates keep native focus order and avoid manual tabindex traps."""
    sources = "\n".join(_source(path) for path in AUDITED_SOURCE_PATHS)

    assert not re.search(r"tabindex=['\"](?!0|-1)(?:\+)?\d+", sources)
    assert "autofocus" not in sources
    assert "trapFocus" not in sources
    assert "focus-trap" not in sources


@pytest.mark.unit
def test_download_row_controls_are_named_focusable_and_in_logical_order():
    """Download row actions keep accessible names, focus styles, and source order."""
    source = _source("app/templates/partials/_download_item.html")

    assert 'class="download-btn text-xs"' in source
    assert 'aria-label="Delete download"' in source
    assert 'type="button"' in source
    assert 'class="btn-danger"' in source
    assert source.index('class="download-btn text-xs"') < source.index(
        'aria-label="Delete download"'
    )


@pytest.mark.unit
@pytest.mark.asyncio
async def test_login_and_register_render_without_low_contrast_tokens():
    """Public auth pages render the expected keyboard controls without gray-500 text."""
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        login_response = await client.get("/web/login")
        register_response = await client.get("/web/register")

    assert login_response.status_code == 200
    assert register_response.status_code == 200

    for response_text in [login_response.text, register_response.text]:
        _assert_no_low_contrast_tokens(response_text)
        assert "Skip to main content" in response_text
        assert 'class="form-input"' in response_text
        assert 'type="submit"' in response_text

    assert login_response.text.index('id="email"') < login_response.text.index('id="password"')
    assert login_response.text.index('id="login-submit"') < login_response.text.index("Guest Demo")
    assert login_response.text.index("Guest Demo") < login_response.text.index("Create one")

    assert register_response.text.index('id="email"') < register_response.text.index(
        'id="password"'
    )
    assert register_response.text.index('id="password"') < register_response.text.index(
        'id="password_confirm"'
    )
    assert register_response.text.index('id="register-submit"') < register_response.text.index(
        "Already have an account?"
    )


@pytest.mark.unit
@pytest.mark.asyncio
async def test_auth_error_states_render_accessible_error_contracts():
    """Login and register errors keep contrast-safe, field-linked alert markup."""
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        login_response = await client.get("/web/login?error=1")
        register_response = await client.get("/web/register?error=password_mismatch")

    assert login_response.status_code == 200
    assert register_response.status_code == 200

    _assert_no_low_contrast_tokens(login_response.text)
    _assert_no_low_contrast_tokens(register_response.text)
    _assert_field_error_contract(
        login_response.text,
        "email-error",
        "Invalid email or password",
    )
    _assert_field_error_contract(
        register_response.text,
        "password-confirm-error",
        "Passwords do not match",
    )


@pytest.mark.unit
@pytest.mark.asyncio
async def test_authenticated_dashboard_and_settings_render_accessible_controls():
    """Authenticated pages render keyboard-reachable controls without gray-500 text."""
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        access_token = await create_test_user_and_login(client)
        cookies = {"access_token": access_token}
        dashboard_response = await client.get("/web/downloads", cookies=cookies)
        settings_response = await client.get("/web/settings", cookies=cookies)

    assert dashboard_response.status_code == 200
    assert settings_response.status_code == 200

    for response_text in [dashboard_response.text, settings_response.text]:
        _assert_no_low_contrast_tokens(response_text)
        assert "Skip to main content" in response_text
        assert 'type="submit"' in response_text

    assert dashboard_response.text.index("Settings") < dashboard_response.text.index("Sign out")
    assert dashboard_response.text.index('id="new-download-url"') < _index_after(
        dashboard_response.text,
        "Download",
        'id="new-download-url"',
    )

    assert settings_response.text.index("Back to dashboard") < settings_response.text.index(
        "Sign out"
    )
    assert settings_response.text.index('id="username"') < settings_response.text.index(
        "Save username"
    )
    assert settings_response.text.index('id="current_password"') < _index_after(
        settings_response.text,
        "Change password",
        'id="current_password"',
    )
    assert settings_response.text.index('id="delete_password"') < settings_response.text.index(
        "Delete my account"
    )


@pytest.mark.unit
@pytest.mark.asyncio
async def test_settings_error_state_renders_accessible_error_contract():
    """Authenticated settings errors keep contrast-safe, field-linked alert markup."""
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        access_token = await create_test_user_and_login(client)
        response = await client.get(
            "/web/settings?error=bad_current_password",
            cookies={"access_token": access_token},
        )

    assert response.status_code == 200
    _assert_no_low_contrast_tokens(response.text)
    _assert_field_error_contract(
        response.text,
        "current-password-error",
        "Current password is incorrect",
    )

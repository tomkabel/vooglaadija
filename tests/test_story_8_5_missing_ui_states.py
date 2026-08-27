"""Regression tests for Story 8.5 missing UI states."""

import subprocess
import textwrap
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from httpx import ASGITransport, AsyncClient

from app.main import app
from core.config import settings
from tests.conftest import create_test_user_and_login

pytestmark = pytest.mark.slow


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def _source(path: str) -> str:
    return (PROJECT_ROOT / path).read_text(encoding="utf-8")


def _run_node_script(script: str) -> None:
    result = subprocess.run(
        ["node", "-e", textwrap.dedent(script)],
        cwd=PROJECT_ROOT,
        capture_output=True,
        check=False,
        text=True,
    )
    assert result.returncode == 0, result.stderr


def _csrf_token(client: AsyncClient, response) -> str:
    token = response.cookies.get("csrf_token") or client.cookies.get("csrf_token")
    assert token
    return token


@pytest.mark.unit
def test_dashboard_sse_disconnect_banner_uses_ten_second_reconnect_contract():
    """The dashboard shows one CSP-compliant reconnect banner after 10 seconds without SSE."""
    source = _source("app/static/js/dashboard.js")

    assert "const SSE_DISCONNECT_BANNER_DELAY = 10000;" in source
    assert "elapsed > SSE_DISCONNECT_BANNER_DELAY" in source
    assert "Connection lost." in source
    assert ">Reconnect<" in source
    assert "data-sse-retry" in source
    assert "addEventListener('click'" in source
    assert "window.location.reload();" in source
    assert "onclick=" not in source
    assert "Retry Connection" not in source
    assert "Refresh" not in source
    assert "elapsed > 60000" not in source
    assert "elapsed > 120000" not in source


@pytest.mark.unit
@pytest.mark.asyncio
async def test_settings_username_save_button_has_scoped_loading_contract():
    """The rendered settings page and JS expose username-only save loading state."""
    settings_js = _source("app/static/js/settings.js")

    async with AsyncClient(transport=ASGITransport(app=app), base_url="https://test") as client:
        access_token = await create_test_user_and_login(client)
        response = await client.get("/web/settings", cookies={"access_token": access_token})

    assert response.status_code == 200
    assert 'id="username-settings-form"' in response.text
    assert "data-username-submit" in response.text
    assert "data-username-spinner" in response.text
    assert "data-username-label" in response.text
    assert "min-w-[160px]" in response.text
    assert "/static/icons/sprite.svg#icon-spinner" in response.text
    assert "Save username" in response.text

    assert "const usernameForm = document.getElementById('username-settings-form');" in settings_js
    assert "function setUsernameSaveLoading(isLoading)" in settings_js
    assert "data-username-submit" in settings_js
    assert "data-username-spinner" in settings_js
    assert "aria-busy" in settings_js
    assert "htmx:beforeRequest" in settings_js
    assert "htmx:afterRequest" in settings_js
    assert "evt.detail?.elt?.closest('#username-settings-form')" in settings_js
    assert "password-change-form" in settings_js
    assert "delete-account-form" in settings_js


@pytest.mark.unit
@pytest.mark.asyncio
async def test_settings_username_htmx_fragments_cover_success_and_error_states():
    """The username save endpoint returns HTMX fragments for success and critical errors."""
    async with AsyncClient(transport=ASGITransport(app=app), base_url="https://test") as client:
        access_token = await create_test_user_and_login(client)
        client.cookies.set("access_token", access_token)

        settings_response = await client.get("/web/settings")
        csrf_token = _csrf_token(client, settings_response)

        success_response = await client.post(
            "/web/settings/username",
            data={"username": "story85-user"},
            headers={"HX-Request": "true", "X-CSRF-Token": csrf_token},
        )
        validation_response = await client.post(
            "/web/settings/username",
            data={"username": "ab"},
            headers={"HX-Request": "true", "X-CSRF-Token": csrf_token},
        )
        csrf_response = await client.post(
            "/web/settings/username",
            data={"username": "story85-user-2"},
            headers={"HX-Request": "true", "X-CSRF-Token": "invalid-token"},
        )

    assert success_response.status_code == 200
    assert "Username updated successfully" in success_response.text
    assert "class='success-box'" in success_response.text
    assert "role='status'" in success_response.text
    assert "aria-live='polite'" in success_response.text

    assert validation_response.status_code == 400
    assert "Username must be at least 3 characters" in validation_response.text
    assert "class='error-box'" in validation_response.text
    assert "role='alert'" in validation_response.text

    assert csrf_response.status_code == 403
    assert "Invalid CSRF token" in csrf_response.text
    assert "class='error-box'" in csrf_response.text


@pytest.mark.unit
@pytest.mark.asyncio
async def test_chaos_lab_polling_failure_state_is_page_scoped():
    """The chaos lab status panel has a scoped failure UI without changing feature gating."""
    chaos_js = _source("app/static/js/chaos-lab.js")
    previous = settings.feature_chaos_api_enabled
    settings.feature_chaos_api_enabled = True
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="https://test") as client:
            response = await client.get("/web/chaos-lab")
    finally:
        settings.feature_chaos_api_enabled = previous

    assert response.status_code == 200
    assert 'id="chaos-status-panel"' in response.text
    assert 'aria-live="polite"' in response.text
    assert 'hx-get="/web/chaos-lab/status"' in response.text
    assert 'hx-trigger="load, every 5s"' in response.text
    assert '<script src="/static/js/chaos-lab.js" defer></script>' in response.text

    assert "Could not reach chaos API" in chaos_js
    assert "chaos-status-panel" in chaos_js
    assert "htmx:responseError" in chaos_js
    assert "htmx:sendError" in chaos_js
    assert "htmx:afterRequest" in chaos_js
    assert "evt.detail?.elt?.closest('#chaos-status-panel')" in chaos_js
    assert "clearChaosStatusFailure" in chaos_js


@pytest.mark.unit
@pytest.mark.asyncio
async def test_chaos_lab_status_route_covers_success_and_feature_flag_error(monkeypatch):
    """The chaos status endpoint keeps the successful partial and disabled 404 behavior."""
    monkeypatch.setattr(settings, "feature_chaos_api_enabled", True)
    status = SimpleNamespace(
        circuit_breaker_open=True,
        worker_crash=False,
        db_failover=False,
        throttle_spike=False,
        slow_processing=True,
    )

    with patch(
        "app.api.routes.web.web_dashboard.get_all_chaos_status",
        new=AsyncMock(return_value=status),
    ):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="https://test") as client:
            success_response = await client.get("/web/chaos-lab/status")

    monkeypatch.setattr(settings, "feature_chaos_api_enabled", False)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="https://test") as client:
        page_404_response = await client.get("/web/chaos-lab")
        status_404_response = await client.get("/web/chaos-lab/status")

    assert success_response.status_code == 200
    assert "Active scenarios" in success_response.text
    assert "Circuit Breaker" in success_response.text
    assert "ACTIVE" in success_response.text
    assert "Slow Processing" in success_response.text
    assert "SLOWED" in success_response.text

    assert page_404_response.status_code == 404
    assert status_404_response.status_code == 404


@pytest.mark.unit
def test_success_fragments_auto_dismiss_after_five_seconds_with_fade_class():
    """HTMX success fragments keep live-region semantics and receive timed fade-out."""
    helpers = _source("app/api/routes/web_helpers.py")
    toast_js = _source("app/static/js/toast.js")
    css = _source("frontend/css/src/styles.css")

    assert "class='success-box'" in helpers
    assert "role='status'" in helpers
    assert "aria-live='polite'" in helpers
    assert "function scheduleSuccessBoxDismiss(successBox)" in toast_js
    assert "document.querySelectorAll('.success-box')" in toast_js
    assert "htmx:afterSwap" in toast_js
    assert "success-box-exit" in toast_js
    assert "setTimeout(() => {" in toast_js
    assert "}, 5000)" in toast_js
    assert ".success-box-exit" in css
    assert "@keyframes success-box-fade-out" in css


@pytest.mark.unit
def test_success_box_auto_dismiss_can_run_without_browser_dependencies():
    """The toast module schedules current and swapped success boxes for fade-out."""
    _run_node_script(
        """
        const assert = require('node:assert/strict');
        const fs = require('node:fs');
        const vm = require('node:vm');

        const timers = [];
        global.setTimeout = (callback, delay) => {
          timers.push({ callback, delay });
          return timers.length;
        };
        global.clearTimeout = () => {};

        class FakeClassList {
          constructor() {
            this.values = new Set();
          }

          add(value) {
            this.values.add(value);
          }

          contains(value) {
            return this.values.has(value);
          }
        }

        class FakeElement extends EventTarget {
          constructor() {
            super();
            this.classList = new FakeClassList();
            this.dataset = {};
          }

          matches(selector) {
            return selector === '.success-box';
          }

          querySelectorAll(selector) {
            return selector === '.success-box' ? [this] : [];
          }

          remove() {
            this.removed = true;
          }
        }

        const firstSuccess = new FakeElement();
        const swappedSuccess = new FakeElement();
        const body = new EventTarget();

        global.window = { Vooglaadija: {} };
        global.document = {
          body,
          getElementById: () => null,
          querySelectorAll: (selector) => (selector === '.success-box' ? [firstSuccess] : []),
          createElement: () => new FakeElement(),
        };

        vm.runInThisContext(fs.readFileSync('app/static/js/toast.js', 'utf8'));

        assert.equal(timers[0].delay, 5000);
        timers[0].callback();
        assert.ok(firstSuccess.classList.contains('success-box-exit'));
        assert.equal(timers[1].delay, 300);
        timers[1].callback();
        assert.equal(firstSuccess.removed, true);

        body.dispatchEvent(
          new CustomEvent('htmx:afterSwap', { detail: { target: swappedSuccess } }),
        );
        assert.equal(timers[2].delay, 5000);
        timers[2].callback();
        assert.ok(swappedSuccess.classList.contains('success-box-exit'));
        """
    )


@pytest.mark.unit
def test_settings_username_loading_state_can_run_without_browser_dependencies():
    """The settings module disables and restores the username submit button from HTMX events."""
    _run_node_script(
        """
        const assert = require('node:assert/strict');
        const fs = require('node:fs');
        const vm = require('node:vm');

        class FakeClassList {
          constructor(values = []) {
            this.values = new Set(values);
          }

          toggle(value, force) {
            if (force) {
              this.values.add(value);
            } else {
              this.values.delete(value);
            }
          }

          contains(value) {
            return this.values.has(value);
          }
        }

        class FakeElement extends EventTarget {
          constructor({ id = '', textContent = '', parent = null, classes = [] } = {}) {
            super();
            this.id = id;
            this.textContent = textContent;
            this.parent = parent;
            this.disabled = false;
            this.attrs = {};
            this.classList = new FakeClassList(classes);
          }

          setAttribute(name, value) {
            this.attrs[name] = String(value);
          }

          getAttribute(name) {
            return this.attrs[name];
          }

          closest(selector) {
            let element = this;
            while (element) {
              if (selector === '#username-settings-form' && element.id === 'username-settings-form') {
                return element;
              }
              element = element.parent;
            }
            return null;
          }

          querySelector(selector) {
            return this.children?.[selector] || null;
          }
        }

        class CustomEvent extends Event {
          constructor(type, options = {}) {
            super(type);
            this.detail = options.detail || {};
          }
        }

        const usernameForm = new FakeElement({ id: 'username-settings-form' });
        const usernameSubmit = new FakeElement({ parent: usernameForm });
        const usernameSpinner = new FakeElement({ parent: usernameForm, classes: ['hidden'] });
        const usernameLabel = new FakeElement({ parent: usernameForm, textContent: 'Save username' });
        usernameForm.children = {
          '[data-username-submit]': usernameSubmit,
          '[data-username-spinner]': usernameSpinner,
          '[data-username-label]': usernameLabel,
        };

        global.window = { Vooglaadija: { toast: { show: () => {} } } };
        global.document = {
          body: new EventTarget(),
          getElementById: (id) => (id === 'username-settings-form' ? usernameForm : null),
        };
        global.CustomEvent = CustomEvent;

        vm.runInThisContext(fs.readFileSync('app/static/js/settings.js', 'utf8'));

        usernameForm.dispatchEvent(new Event('htmx:beforeRequest'));
        assert.equal(usernameSubmit.disabled, true);
        assert.equal(usernameForm.getAttribute('aria-busy'), 'true');
        assert.equal(usernameSubmit.getAttribute('aria-busy'), 'true');
        assert.equal(usernameSpinner.classList.contains('hidden'), false);
        assert.equal(usernameLabel.textContent, 'Saving…');

        document.body.dispatchEvent(
          new CustomEvent('htmx:afterRequest', { detail: { elt: usernameSubmit } }),
        );
        assert.equal(usernameSubmit.disabled, false);
        assert.equal(usernameForm.getAttribute('aria-busy'), 'false');
        assert.equal(usernameSubmit.getAttribute('aria-busy'), 'false');
        assert.equal(usernameSpinner.classList.contains('hidden'), true);
        assert.equal(usernameLabel.textContent, 'Save username');

        usernameForm.dispatchEvent(new Event('htmx:beforeRequest'));
        document.body.dispatchEvent(
          new CustomEvent('htmx:sendError', { detail: { elt: usernameSubmit } }),
        );
        assert.equal(usernameSubmit.disabled, false);
        assert.equal(usernameSpinner.classList.contains('hidden'), true);
        """
    )


@pytest.mark.unit
def test_chaos_lab_polling_failure_state_can_run_without_browser_dependencies():
    """The chaos module replaces a failed poll with a scoped accessible failure state."""
    _run_node_script(
        """
        const assert = require('node:assert/strict');
        const fs = require('node:fs');
        const vm = require('node:vm');

        class FakeElement extends EventTarget {
          constructor(id) {
            super();
            this.id = id;
            this.dataset = {};
            this.attrs = {};
            this.innerHTML = '<span>Loading status…</span>';
          }

          setAttribute(name, value) {
            this.attrs[name] = String(value);
          }

          getAttribute(name) {
            return this.attrs[name];
          }

          closest(selector) {
            return selector === '#chaos-status-panel' ? this : null;
          }
        }

        class CustomEvent extends Event {
          constructor(type, options = {}) {
            super(type);
            this.detail = options.detail || {};
          }
        }

        const panel = new FakeElement('chaos-status-panel');
        global.document = {
          body: new EventTarget(),
          getElementById: (id) => (id === 'chaos-status-panel' ? panel : null),
        };
        global.CustomEvent = CustomEvent;

        vm.runInThisContext(fs.readFileSync('app/static/js/chaos-lab.js', 'utf8'));

        document.body.dispatchEvent(
          new CustomEvent('htmx:beforeRequest', { detail: { elt: panel } }),
        );
        assert.equal(panel.getAttribute('aria-busy'), 'true');

        document.body.dispatchEvent(
          new CustomEvent('htmx:responseError', { detail: { elt: panel } }),
        );
        assert.equal(panel.dataset.chaosStatusFailed, 'true');
        assert.equal(panel.getAttribute('aria-busy'), 'false');
        assert.match(panel.innerHTML, /Could not reach chaos API/);

        document.body.dispatchEvent(
          new CustomEvent('htmx:afterRequest', {
            detail: { elt: panel, successful: true },
          }),
        );
        assert.equal(panel.dataset.chaosStatusFailed, undefined);
        assert.equal(panel.getAttribute('aria-busy'), 'false');

        document.body.dispatchEvent(
          new CustomEvent('htmx:sendError', { detail: { target: panel } }),
        );
        assert.equal(panel.dataset.chaosStatusFailed, 'true');
        assert.match(panel.innerHTML, /Could not reach chaos API/);
        """
    )

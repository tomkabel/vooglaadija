"""Regression tests for Story 8.4 JavaScript namespace consolidation."""

import re
import subprocess
import textwrap
from pathlib import Path

import pytest

pytestmark = pytest.mark.slow


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def _source(path: str) -> str:
    return (PROJECT_ROOT / path).read_text(encoding="utf-8")


def _global_script_order(source: str, script: str) -> int:
    marker = f'<script src="/static/js/{script}"'
    position = source.find(marker)
    assert position != -1, f"{script} is not loaded by base.html"
    return position


def _run_node_script(script: str) -> None:
    result = subprocess.run(
        ["node", "-e", textwrap.dedent(script)],
        cwd=PROJECT_ROOT,
        capture_output=True,
        check=False,
        text=True,
    )
    assert result.returncode == 0, result.stderr


@pytest.mark.unit
def test_base_loads_namespace_bootstrap_before_consumers():
    """The base template loads the Vooglaadija namespace before app modules consume it."""
    base = _source("app/templates/base.html")

    namespace_pos = _global_script_order(base, "vooglaadija.js")

    assert namespace_pos < _global_script_order(base, "toast.js")
    assert namespace_pos < _global_script_order(base, "auth.js")
    assert namespace_pos < _global_script_order(base, "htmx-error-handler.js")
    assert namespace_pos < base.index("{% block extra_head %}")


@pytest.mark.unit
def test_namespace_bootstrap_exposes_idempotent_shape():
    """The namespace bootstrap owns state, events, and the initializer surface."""
    source = _source("app/static/js/vooglaadija.js")

    assert "window.Vooglaadija" in source
    assert ".state" in source
    assert ".events" in source
    assert ".init" in source
    assert "new EventTarget()" in source
    assert "window.Vooglaadija = window.Vooglaadija || {}" in source
    assert "window.Vooglaadija.state = window.Vooglaadija.state || {}" in source
    assert "window.Vooglaadija.events = window.Vooglaadija.events || new EventTarget()" in source


@pytest.mark.unit
def test_namespace_bootstrap_preserves_existing_runtime_state():
    """The namespace bootstrap is idempotent at runtime and preserves existing submodules."""
    _run_node_script(
        """
        const assert = require('node:assert/strict');
        const fs = require('node:fs');
        const vm = require('node:vm');

        function runBootstrap() {
          vm.runInThisContext(fs.readFileSync('app/static/js/vooglaadija.js', 'utf8'));
        }

        const state = { existing: true };
        const events = new EventTarget();
        global.window = { Vooglaadija: { state, events } };

        runBootstrap();
        runBootstrap();

        assert.equal(window.Vooglaadija.state, state);
        assert.equal(window.Vooglaadija.events, events);
        assert.deepEqual(window.Vooglaadija.state, { existing: true });

        let callbackArgument = null;
        const initResult = window.Vooglaadija.init((app) => {
          callbackArgument = app;
        });

        assert.equal(callbackArgument, window.Vooglaadija);
        assert.equal(initResult, window.Vooglaadija);
        """
    )


@pytest.mark.unit
def test_no_application_owned_flat_globals_remain():
    """Application-owned APIs are exposed only through window.Vooglaadija."""
    sources = "\n".join(
        _source(path)
        for path in [
            "app/static/js/toast.js",
            "app/static/js/auth.js",
            "app/static/js/settings.js",
            "app/static/js/htmx-error-handler.js",
            "app/static/js/dashboard.js",
        ]
    )

    assert "window.showToast =" not in sources
    assert "window.auth =" not in sources
    assert "window.VooglaadijaDashboard =" not in sources
    assert re.search(r"window\.(?:showToast|auth|VooglaadijaDashboard)\s*=", sources) is None
    assert "window.Vooglaadija.toast" in _source("app/static/js/toast.js")
    assert "window.Vooglaadija.auth" in _source("app/static/js/auth.js")
    assert "window.Vooglaadija.dashboard" in _source("app/static/js/dashboard.js")


@pytest.mark.unit
def test_toast_consumers_use_namespaced_api():
    """Auth, settings, and HTMX error handling call the namespaced toast API."""
    for path in [
        "app/static/js/auth.js",
        "app/static/js/settings.js",
        "app/static/js/htmx-error-handler.js",
    ]:
        source = _source(path)

        assert "window.showToast" not in source
        assert "Vooglaadija.toast.show" in source


@pytest.mark.unit
def test_dashboard_registers_handlers_and_dispatches_sse_through_event_bus():
    """Dashboard SSE handlers use the shared EventTarget event bus."""
    source = _source("app/static/js/dashboard.js")

    assert "Vooglaadija.events.addEventListener('job-update', handleJobUpdate" in source
    assert "Vooglaadija.events.addEventListener('progress-update', handleProgressUpdate" in source
    assert "new CustomEvent('job-update'" in source
    assert "new CustomEvent('progress-update'" in source
    assert "Vooglaadija.events.dispatchEvent(" in source
    assert re.search(
        r"function handleJobUpdate\(event\) {\s+clearSkel\(\);\s+markSseActivity\(\);", source
    )
    assert re.search(
        r"function handleProgressUpdate\(event\) {\s+clearSkel\(\);\s+markSseActivity\(\);",
        source,
    )
    assert "source.addEventListener('job_update', handleJobUpdate)" not in source
    assert "source.addEventListener('progress_update', handleProgressUpdate)" not in source
    assert "source.addEventListener('job_update', replace)" not in source


@pytest.mark.unit
def test_dashboard_event_bus_runtime_bridges_sse_and_cleans_old_source():
    """Dashboard runtime wiring bridges SSE messages through the bus without duplicate setup."""
    _run_node_script(
        """
        const assert = require('node:assert/strict');
        const fs = require('node:fs');
        const vm = require('node:vm');

        const NativeEventTarget = EventTarget;

        class RecordingEventTarget extends NativeEventTarget {
          constructor() {
            super();
            this.added = [];
            this.removed = [];
            this.dispatched = [];
          }

          addEventListener(type, listener, options) {
            this.added.push({ type, listener });
            return super.addEventListener(type, listener, options);
          }

          removeEventListener(type, listener, options) {
            this.removed.push({ type, listener });
            return super.removeEventListener(type, listener, options);
          }

          dispatchEvent(event) {
            this.dispatched.push({ type: event.type, detail: event.detail });
            return super.dispatchEvent(event);
          }
        }

        class FakeClassList {
          add() {}
          remove() {}
          contains() {
            return false;
          }
        }

        class FakeElement extends NativeEventTarget {
          constructor(tagName = 'div') {
            super();
            this.tagName = tagName.toUpperCase();
            this.attributes = {};
            this.children = [];
            this.classList = new FakeClassList();
            this.dataset = {};
            this.parentElement = null;
            this.parentNode = null;
            this.style = {};
            this.textContent = '';
            this.innerHTML = '';
          }

          appendChild(child) {
            child.parentElement = this;
            child.parentNode = this;
            this.children.push(child);
            return child;
          }

          insertBefore(child) {
            return this.appendChild(child);
          }

          querySelector() {
            return null;
          }

          querySelectorAll() {
            return [];
          }

          setAttribute(name, value) {
            this.attributes[name] = String(value);
          }

          getAttribute(name) {
            return this.attributes[name] || null;
          }

          removeAttribute(name) {
            delete this.attributes[name];
          }

          insertAdjacentElement() {}
          remove() {}
          focus() {}
          matches() {
            return false;
          }
          closest() {
            return null;
          }
        }

        class FakeSource {
          constructor() {
            this.listeners = new Map();
            this.added = [];
            this.removed = [];
          }

          addEventListener(type, listener) {
            this.added.push({ type, listener });
            this.listeners.set(type, listener);
          }

          removeEventListener(type, listener) {
            this.removed.push({ type, listener });
            if (this.listeners.get(type) === listener) {
              this.listeners.delete(type);
            }
          }

          emit(type, data) {
            const listener = this.listeners.get(type);
            if (listener) listener({ data });
          }
        }

        const elements = new Map([
          ['confirm-modal', new FakeElement()],
          ['modal-title', new FakeElement()],
          ['modal-desc', new FakeElement()],
        ]);
        const modalCancel = new FakeElement('button');
        const modalConfirm = new FakeElement('button');
        const existingRow = new FakeElement();
        existingRow.dataset.jobId = 'job-1';

        global.EventTarget = RecordingEventTarget;
        global.window = new NativeEventTarget();
        window.location = { reload() {} };
        global.CSS = { escape: (value) => String(value) };
        global.Element = FakeElement;
        global.htmx = { process() {} };
        global.setInterval = () => 1;
        global.clearInterval = () => {};

        global.document = {
          body: new FakeElement('body'),
          readyState: 'complete',
          activeElement: null,
          addEventListener() {},
          createElement: (tagName) => new FakeElement(tagName),
          getElementById: (id) => elements.get(id) || null,
          querySelector: (selector) => {
            if (selector === '[data-modal-cancel]') return modalCancel;
            if (selector === '[data-modal-confirm]') return modalConfirm;
            if (selector.startsWith('[data-job-id=')) return existingRow;
            return null;
          },
          querySelectorAll: () => [],
        };

        function runScript(path) {
          vm.runInThisContext(fs.readFileSync(path, 'utf8'), { filename: path });
        }

        runScript('app/static/js/vooglaadija.js');
        const bus = window.Vooglaadija.events;

        runScript('app/static/js/dashboard.js');
        assert.ok(
          bus.added.some(
            ({ type, listener }) => type === 'job-update' && listener.name === 'handleJobUpdate',
          ),
        );
        assert.ok(
          bus.added.some(
            ({ type, listener }) =>
              type === 'progress-update' && listener.name === 'handleProgressUpdate',
          ),
        );

        const listenerCount = bus.added.length;
        runScript('app/static/js/dashboard.js');
        assert.equal(bus.added.length, listenerCount);

        const source1 = new FakeSource();
        document.body.dispatchEvent(new CustomEvent('htmx:sseOpen', { detail: { source: source1 } }));
        assert.ok(source1.listeners.has('job_update'));
        assert.ok(source1.listeners.has('progress_update'));

        const source1AddCount = source1.added.length;
        document.body.dispatchEvent(new CustomEvent('htmx:sseOpen', { detail: { source: source1 } }));
        assert.equal(source1.added.length, source1AddCount);

        const dispatchCount = bus.dispatched.length;
        source1.emit('job_update', '{bad json');
        source1.emit('job_update', JSON.stringify({ url: 'https://example.test/no-id' }));
        assert.equal(bus.dispatched.length, dispatchCount);

        source1.emit(
          'job_update',
          JSON.stringify({ id: 'job-1', url: 'https://example.test/video', status: 'processing' }),
        );
        assert.equal(bus.dispatched.at(-1).type, 'job-update');
        assert.equal(bus.dispatched.at(-1).detail.id, 'job-1');

        source1.emit(
          'progress_update',
          JSON.stringify({ id: 'job-1', url: 'https://example.test/video' }),
        );
        assert.equal(bus.dispatched.at(-1).type, 'progress-update');

        const source2 = new FakeSource();
        document.body.dispatchEvent(new CustomEvent('htmx:sseOpen', { detail: { source: source2 } }));
        assert.ok(source1.removed.some(({ type }) => type === 'job_update'));
        assert.ok(source1.removed.some(({ type }) => type === 'progress_update'));
        assert.ok(source2.listeners.has('job_update'));
        assert.ok(source2.listeners.has('progress_update'));
        """
    )


@pytest.mark.unit
def test_dashboard_preserves_story_8_1_to_8_3_contract_markers():
    """The namespace conversion preserves prior Epic 8 frontend contracts."""
    source = _source("app/static/js/dashboard.js")

    assert "document.getElementById('download-announcer')" in source
    assert "New download: ${" in source
    assert "let sseHealthIntervalId = null;" in source
    assert "sseHealthIntervalId = setInterval(runSseHealthCheck, 5000);" in source
    assert "MutationObserver" in source
    assert "const seenJobIds = new Set();" in source
    assert "function isDownloadFormRequest(evt)" in source
    assert "data-sse-retry" in source
    assert "window.location.reload();" in source


@pytest.mark.unit
def test_dashboard_tracks_shared_state_in_namespace():
    """Shared dashboard state moves into the Vooglaadija state container."""
    source = _source("app/static/js/dashboard.js")

    assert "Vooglaadija.state.dashboard" in source
    assert "sseSource" in source
    assert "htmxPending" in source

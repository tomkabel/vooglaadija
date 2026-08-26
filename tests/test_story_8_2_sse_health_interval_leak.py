"""Regression tests for Story 8.2 SSE health monitor timer cleanup."""

import re
from pathlib import Path

import pytest

pytestmark = pytest.mark.slow


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DASHBOARD_JS = PROJECT_ROOT / "app/static/js/dashboard.js"


def _dashboard_source() -> str:
    return DASHBOARD_JS.read_text(encoding="utf-8")


def _sse_health_monitor_source(source: str) -> str:
    match = re.search(
        r"// .+ SSE Health Monitor .+?\n(?P<block>.*?)\n  // .+ Row Factory",
        source,
        flags=re.DOTALL,
    )
    assert match is not None
    return match.group("block")


@pytest.mark.unit
def test_sse_health_monitor_uses_one_managed_interval():
    """The SSE health monitor has one startup path and no nested intervals."""
    source = _dashboard_source()
    health_source = _sse_health_monitor_source(source)

    assert "let sseHealthIntervalId = null;" in source
    assert health_source.count("setInterval(") == 1
    assert "sseHealthIntervalId = setInterval(runSseHealthCheck, 5000);" in health_source
    assert "const check = setInterval" not in source
    assert "clearInterval(sseHealthIntervalId)" in source


@pytest.mark.unit
def test_sse_health_monitor_registers_idempotent_unload_and_htmx_cleanup():
    """Dashboard lifecycle events clear the managed health interval."""
    source = _dashboard_source()

    assert "function stopSseHealthMonitor()" in source
    assert "function teardownSseHealthMonitor()" in source
    assert "sseHealthIntervalId = null;" in source
    assert "pagehide" in source
    assert "pageshow" in source
    assert "beforeunload" in source
    assert "htmx:beforeCleanupElement" in source
    assert "htmx:load" in source
    assert "sse-container" in source
    assert "download-list" in source
    assert "teardownSseHealthMonitor()" in source
    assert "startSseHealthMonitor()" in source


@pytest.mark.unit
def test_sse_health_monitor_preserves_status_and_banner_behavior():
    """Health state strings and timing thresholds remain stable."""
    source = _dashboard_source()
    health_source = _sse_health_monitor_source(source)

    assert "SSE_TIMEOUT = 35000" in source
    assert "SSE_DISCONNECT_BANNER_DELAY = 10000" in source
    assert "elapsed > SSE_DISCONNECT_BANNER_DELAY" in health_source
    assert "lastMsg < SSE_DISCONNECT_BANNER_DELAY" in health_source
    assert "Reconnecting\\u2026" in health_source
    assert "live-indicator--active" in health_source
    assert "live-indicator--error" in health_source
    assert "sse-reconnect-banner" in health_source
    assert "Connection lost." in health_source
    assert "Reconnect" in health_source
    assert "Retry Connection" not in health_source
    assert "Refresh" not in health_source


@pytest.mark.unit
def test_sse_health_monitor_only_runs_while_dashboard_component_exists():
    """The health monitor restarts after dashboard remounts but not on unrelated HTMX loads."""
    source = _dashboard_source()
    health_source = _sse_health_monitor_source(source)

    assert "function shouldRunSseHealthMonitor()" in health_source
    assert "document.getElementById('sse-container')" in health_source
    assert "sseHealthIntervalId !== null || !shouldRunSseHealthMonitor()" in health_source
    assert "window.addEventListener('pageshow', startSseHealthMonitor)" in health_source
    assert "if (isDashboardSseElement(evt.detail?.elt)) startSseHealthMonitor()" in health_source

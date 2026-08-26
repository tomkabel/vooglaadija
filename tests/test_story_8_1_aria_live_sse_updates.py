import pytest

"""Regression tests for Story 8.1 SSE update announcements."""

from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace

from app.api.routes.web.web_helpers import templates

pytestmark = pytest.mark.slow



PROJECT_ROOT = Path(__file__).resolve().parents[1]


def _source(path: str) -> str:
    return (PROJECT_ROOT / path).read_text(encoding="utf-8")


def _render_download_list(jobs: list[SimpleNamespace]) -> str:
    return templates.env.get_template("partials/_download_list.html").render(jobs=jobs)


def _assert_download_rows_live_attributes(html: str) -> None:
    assert html.count('id="download-rows"') == 1
    assert 'id="download-rows"' in html
    assert 'role="feed"' in html
    assert 'aria-label="Downloads"' in html
    assert 'aria-live="polite"' in html
    assert 'aria-atomic="false"' in html
    assert 'aria-busy="false"' in html


@pytest.mark.unit
def test_download_list_empty_state_exposes_live_feed_attributes():
    """The empty download list exposes stable live feed semantics."""
    html = _render_download_list(jobs=[])

    _assert_download_rows_live_attributes(html)
    assert 'id="download-announcer"' in html
    assert 'class="sr-only"' in html
    assert 'aria-atomic="true"' in html
    assert 'id="download-skeleton" aria-hidden="true"' in html


@pytest.mark.unit
def test_download_list_populated_state_exposes_live_feed_attributes():
    """The populated download list exposes the same live feed semantics."""
    job = SimpleNamespace(
        id="job-1",
        title="Accessible Video",
        url="https://example.test/video",
        status="pending",
        file_name=None,
        created_at=datetime(2026, 6, 22, 12, 0, tzinfo=UTC),
    )

    html = _render_download_list(jobs=[job])

    _assert_download_rows_live_attributes(html)
    assert 'id="download-announcer"' in html
    assert "Accessible Video" in html


@pytest.mark.unit
def test_dashboard_js_emits_required_download_announcements():
    """The dashboard script announces new rows and status changes explicitly."""
    source = _source("app/static/js/dashboard.js")

    assert "function announceDownloadUpdate(message)" in source
    assert "document.getElementById('download-announcer')" in source
    assert "New download: ${" in source
    assert "} - ${" in source
    assert "data.title || data.url || row.querySelector('.url-text')?.textContent" in source

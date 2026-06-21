"""Tests for metrics endpoint."""

import pytest
from httpx import ASGITransport, AsyncClient
from prometheus_client import CONTENT_TYPE_LATEST

from app.main import app


@pytest.mark.asyncio
async def test_metrics_requires_auth():
    """Test that /metrics endpoint requires authentication."""
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get("/metrics")
    assert response.status_code == 401


@pytest.mark.asyncio
async def test_metrics_with_auth():
    """Test that /metrics endpoint returns prometheus metrics when authenticated."""
    from unittest.mock import MagicMock

    from app.api.dependencies import get_current_user
    from core.models.user import User

    mock_user = MagicMock(spec=User)
    mock_user.id = "test-user-id"
    mock_user.email = "test@example.com"

    app.dependency_overrides[get_current_user] = lambda: mock_user

    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response = await client.get("/metrics")
        assert response.status_code == 200
        assert response.headers["content-type"] == CONTENT_TYPE_LATEST
    finally:
        app.dependency_overrides.pop(get_current_user, None)


@pytest.mark.asyncio
async def test_metrics_returns_prometheus_format():
    """Test that /metrics returns prometheus text format."""
    from unittest.mock import MagicMock

    from app.api.dependencies import get_current_user
    from core.models.user import User

    mock_user = MagicMock(spec=User)
    mock_user.id = "test-user-id"
    mock_user.email = "test@example.com"

    app.dependency_overrides[get_current_user] = lambda: mock_user

    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response = await client.get("/metrics")
        content = response.text
        assert "ytprocessor_info" in content
        assert "# HELP ytprocessor" in content
        assert "# TYPE ytprocessor" in content
    finally:
        app.dependency_overrides.pop(get_current_user, None)


@pytest.mark.asyncio
async def test_prometheus_metrics_endpoint_exposes_core_metric_singletons():
    """Test that the scrape endpoint exposes values from canonical core metrics."""
    from core.metrics import QUEUE_DEPTH

    QUEUE_DEPTH.set(7)
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response = await client.get("/prometheus-metrics")
    finally:
        QUEUE_DEPTH.set(0)

    assert response.status_code == 200
    assert response.headers["content-type"] == CONTENT_TYPE_LATEST
    assert "ytprocessor_queue_depth 7.0" in response.text

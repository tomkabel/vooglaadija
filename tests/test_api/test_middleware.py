"""Tests for API middleware."""

from unittest.mock import AsyncMock, MagicMock

import pytest
from starlette.requests import Request
from starlette.responses import Response

from app.api.middleware import PrometheusMiddleware


class TestPrometheusMiddleware:
    """Tests for PrometheusMiddleware."""

    @pytest.mark.asyncio
    async def test_metrics_skip_for_metrics_endpoint(self):
        """Test that /metrics endpoint skips middleware processing."""
        middleware = PrometheusMiddleware(app=MagicMock())

        mock_request = MagicMock(spec=Request)
        mock_request.url.path = "/metrics"
        mock_request.scope = {"route": None}

        mock_call_next = AsyncMock()
        mock_response = MagicMock(spec=Response)
        mock_response.status_code = 200
        mock_call_next.return_value = mock_response

        result = await middleware.dispatch(mock_request, mock_call_next)

        # Should return immediately without recording metrics
        mock_call_next.assert_called_once()
        assert result == mock_response

    @pytest.mark.asyncio
    async def test_metrics_recorded_on_success(self):
        """Test that metrics are recorded on successful request."""
        from unittest.mock import patch

        from core.metrics import HTTP_REQUEST_DURATION, HTTP_REQUESTS

        middleware = PrometheusMiddleware(app=MagicMock())

        mock_route = MagicMock()
        mock_route.path_format = "/api/v1/test"

        mock_request = MagicMock(spec=Request)
        mock_request.url.path = "/api/v1/test"
        mock_request.method = "GET"
        mock_request.scope = {"route": mock_route}

        mock_call_next = AsyncMock()
        mock_response = MagicMock(spec=Response)
        mock_response.status_code = 200
        mock_call_next.return_value = mock_response

        with (
            patch.object(HTTP_REQUESTS, "labels") as mock_requests_labels,
            patch.object(HTTP_REQUEST_DURATION, "labels") as mock_duration_labels,
        ):
            mock_inc = MagicMock()
            mock_observe = MagicMock()
            mock_requests_labels.return_value.inc = mock_inc
            mock_duration_labels.return_value.observe = mock_observe

            result = await middleware.dispatch(mock_request, mock_call_next)

            assert result == mock_response
            mock_requests_labels.assert_called_once_with(
                method="GET",
                endpoint="/api/v1/test",
                status_code=200,
            )
            mock_inc.assert_called_once()
            mock_observe.assert_called_once()

    @pytest.mark.asyncio
    async def test_metrics_recorded_on_exception(self):
        """Test that metrics are recorded when exception occurs."""
        from unittest.mock import patch

        from core.metrics import HTTP_REQUEST_DURATION, HTTP_REQUESTS

        middleware = PrometheusMiddleware(app=MagicMock())

        mock_route = MagicMock()
        mock_route.path_format = "/api/v1/test"

        mock_request = MagicMock(spec=Request)
        mock_request.url.path = "/api/v1/test"
        mock_request.method = "POST"
        mock_request.scope = {"route": mock_route}

        async def raise_exception(request):
            raise ValueError("Test error")

        with (
            patch.object(HTTP_REQUESTS, "labels") as mock_requests_labels,
            patch.object(HTTP_REQUEST_DURATION, "labels") as mock_duration_labels,
        ):
            mock_inc = MagicMock()
            mock_observe = MagicMock()
            mock_requests_labels.return_value.inc = mock_inc
            mock_duration_labels.return_value.observe = mock_observe

            with pytest.raises(ValueError, match="Test error"):
                await middleware.dispatch(mock_request, raise_exception)

            # Verify metrics were recorded for exception case
            mock_requests_labels.assert_called_once_with(
                method="POST",
                endpoint="/api/v1/test",
                status_code=500,
            )
            mock_inc.assert_called_once()
            mock_observe.assert_called_once()

    def test_get_endpoint_from_route_with_path_format(self):
        """Test endpoint extraction from route with path_format."""
        middleware = PrometheusMiddleware(app=MagicMock())

        mock_route = MagicMock()
        mock_route.path_format = "/api/v1/{id}"

        result = middleware._get_endpoint_from_route(mock_route)
        assert result == "/api/v1/{id}"

    def test_get_endpoint_from_route_with_path(self):
        """Test endpoint extraction from route with path attribute."""
        middleware = PrometheusMiddleware(app=MagicMock())

        mock_route = MagicMock(spec=[])
        mock_route.path = "/health"

        result = middleware._get_endpoint_from_route(mock_route)
        assert result == "/health"

    def test_get_endpoint_from_route_none(self):
        """Test endpoint extraction when route is None."""
        middleware = PrometheusMiddleware(app=MagicMock())

        result = middleware._get_endpoint_from_route(None)
        assert result == "**unmatched**"

    def test_get_endpoint_from_route_no_path_attributes(self):
        """Test endpoint extraction when route has no path attributes."""
        middleware = PrometheusMiddleware(app=MagicMock())

        mock_route = MagicMock(spec=[])
        # Route with no path or path_format

        result = middleware._get_endpoint_from_route(mock_route)
        assert result == "**unmatched**"

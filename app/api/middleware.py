"""API middleware for metrics collection."""

import time

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.routing import BaseRoute

from core.metrics import HTTP_REQUEST_DURATION, HTTP_REQUESTS

CONTENT_TYPE_LATEST = "text/plain; charset=utf-8"


class PrometheusMiddleware(BaseHTTPMiddleware):
    """Middleware to collect HTTP metrics."""

    async def dispatch(self, request: Request, call_next):
        if request.url.path == "/metrics":
            return await call_next(request)

        method = request.method
        start_time = time.time()

        try:
            response = await call_next(request)
        except Exception:
            # Record metrics even on exceptions
            duration = time.time() - start_time
            route = request.scope.get("route")
            endpoint = self._get_endpoint_from_route(route)
            HTTP_REQUESTS.labels(
                method=method,
                endpoint=endpoint,
                status_code=500,
            ).inc()
            HTTP_REQUEST_DURATION.labels(
                method=method,
                endpoint=endpoint,
            ).observe(duration)
            raise

        route = request.scope.get("route")
        endpoint = self._get_endpoint_from_route(route)

        duration = time.time() - start_time
        status_code = response.status_code

        HTTP_REQUESTS.labels(
            method=method,
            endpoint=endpoint,
            status_code=status_code,
        ).inc()

        HTTP_REQUEST_DURATION.labels(
            method=method,
            endpoint=endpoint,
        ).observe(duration)

        return response

    def _get_endpoint_from_route(self, route: BaseRoute | None) -> str:
        """Get the endpoint path from the matched route."""
        if route is None:
            return "**unmatched**"
        if hasattr(route, "path_format"):
            return str(route.path_format)
        if hasattr(route, "path"):
            return str(route.path)
        return "**unmatched**"

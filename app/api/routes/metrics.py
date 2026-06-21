"""Prometheus metrics endpoint."""

from fastapi import APIRouter, Depends, Response
from prometheus_client import CONTENT_TYPE_LATEST, generate_latest

from app.api.dependencies import get_current_user

router = APIRouter(tags=["metrics"])


def _generate_metrics_response() -> Response:
    """Generate the Prometheus metrics response (reused by both endpoints)."""
    return Response(
        content=generate_latest(),
        media_type=CONTENT_TYPE_LATEST,
    )


@router.get("/metrics", dependencies=[Depends(get_current_user)])
async def metrics() -> Response:
    """Prometheus metrics endpoint (authenticated)."""
    return _generate_metrics_response()


@router.get("/prometheus-metrics")
async def prometheus_metrics() -> Response:
    """Prometheus scrape endpoint (unauthenticated — for container-to-container scraping).

    This is the endpoint Prometheus hits. It exposes operational metrics
    (job counts, circuit breaker state, queue depth) — no user data.
    """
    return _generate_metrics_response()

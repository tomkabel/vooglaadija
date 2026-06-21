import logging
import os
import time
from typing import TypedDict

from fastapi import APIRouter, Response, status
from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from app.schemas.error import ErrorCode, error_response_doc, success_response_doc
from core.redis_client import get_redis_client

logger = logging.getLogger(__name__)


class HealthDependencies(TypedDict):
    database: str
    redis: str


class HealthStatus(TypedDict):
    status: str
    timestamp: float
    dependencies: HealthDependencies


router = APIRouter(prefix="/health", tags=["health"])


class ReadinessResponse(BaseModel):
    """Response model for readiness check."""

    status: str
    database: str
    redis: str


@router.get(
    "",
    summary="Service Health Check",
    description="Independent endpoint to monitor database and redis connectivity.",
    responses={
        200: success_response_doc("Service is healthy", {"status": "healthy"}),
        503: error_response_doc(
            "Service unavailable", ErrorCode.INTERNAL_ERROR, "Dependency check failed"
        ),
    },
)
async def health_check() -> HealthStatus:
    """
    Returns the health status using independent, direct connections.
    """
    health_status: HealthStatus = {
        "status": "healthy",
        "timestamp": time.time(),
        "dependencies": {"database": "unknown", "redis": "unknown"},
    }

    # 1. Independent Database Check
    db_url = os.getenv("DATABASE_URL")
    if db_url:
        try:
            # We created a temporary engine just to check the pulse
            engine = create_async_engine(db_url)
            async with engine.connect() as conn:
                await conn.execute(text("SELECT 1"))
            health_status["dependencies"]["database"] = "ok"
            await engine.dispose()
        except Exception as e:
            health_status["dependencies"]["database"] = f"error: {e!s}"
            health_status["status"] = "unhealthy"
    else:
        health_status["dependencies"]["database"] = "missing DATABASE_URL"
        health_status["status"] = "unhealthy"

    # 2. Independent Redis Check
    redis_url = os.getenv("REDIS_URL")
    if redis_url:
        try:
            client = get_redis_client()
            if await client.ping():
                health_status["dependencies"]["redis"] = "ok"
        except Exception as e:
            health_status["dependencies"]["redis"] = f"error: {e!s}"
            health_status["status"] = "unhealthy"
    else:
        health_status["dependencies"]["redis"] = "missing REDIS_URL"
        health_status["status"] = "unhealthy"

    return health_status


@router.get(
    "/ready",
    summary="Readiness check",
    description="Readiness probe checking database and Redis connectivity. "
    "Used by Kubernetes to determine if the service can receive traffic.",
    response_model=ReadinessResponse,
    responses={
        200: success_response_doc(
            "Service is ready",
            {
                "status": "ready",
                "database": "connected",
                "redis": "connected",
            },
        ),
        503: error_response_doc(
            "Service not ready",
            ErrorCode.SERVICE_UNAVAILABLE,
            "One or more dependencies are unavailable",
        ),
    },
)
async def readiness_check() -> ReadinessResponse | Response:
    """
    Readiness probe that checks all dependencies.

    Returns 503 if any dependency is unhealthy, allowing
    Kubernetes to remove this pod from service endpoints.
    """
    db_status = "connected"
    redis_status = "connected"

    # Check database connectivity
    try:
        db_url = os.getenv("DATABASE_URL")
        if not db_url:
            db_status = "error: missing DATABASE_URL"
        else:
            engine = create_async_engine(db_url)
            try:
                async with engine.connect() as conn:
                    await conn.execute(text("SELECT 1"))
            finally:
                await engine.dispose()
    except Exception:
        logger.exception("Database readiness check failed")
        db_status = "error: unavailable"

    # Check Redis connectivity
    try:
        client = get_redis_client()
        await client.ping()
    except Exception:
        logger.exception("Redis readiness check failed")
        redis_status = "error: unavailable"

    # Determine overall status
    is_ready = db_status == "connected" and redis_status == "connected"

    response_data = {
        "status": "ready" if is_ready else "not_ready",
        "database": db_status,
        "redis": redis_status,
    }

    if is_ready:
        return ReadinessResponse(**response_data)

    # Return 503 with JSON body when not ready
    import json

    return Response(
        content=json.dumps(response_data),
        status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
        media_type="application/json",
    )

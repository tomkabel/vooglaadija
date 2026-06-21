from fastapi import APIRouter, Form, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from app.api.dependencies import CurrentUserFromCookie, DbSession
from app.api.routes.web import validate_csrf_token
from app.config import settings
from app.logging_config import get_logger
from app.services.redis_client import (
    SCENARIO_KEY_MAP,
    delete_chaos_keys,
    get_redis_client,
)

logger = get_logger(__name__)

router = APIRouter(prefix="/api/v1/chaos", tags=["chaos"])


def _require_feature_flag():
    """Raise 404 if chaos API is disabled."""
    if not settings.feature_chaos_api_enabled:
        raise HTTPException(status_code=404, detail="Not Found")


class ChaosInjectRequest(BaseModel):
    scenario: str
    duration_seconds: int = Field(default=30, ge=1)


class ChaosBulkSubmitRequest(BaseModel):
    count: int = Field(default=10, ge=1, le=50, description="Number of videos to submit")


class ChaosStatus(BaseModel):
    circuit_breaker_open: bool = False
    worker_crash: bool = False
    db_failover: bool = False
    throttle_spike: bool = False
    slow_processing: bool = False


def _scenario_key(scenario: str) -> str:
    return SCENARIO_KEY_MAP.get(scenario, f"chaos:{scenario}")


@router.post("/inject")
async def inject_chaos(
    request: Request,
    _user: CurrentUserFromCookie,
    scenario: str = Form(...),
    duration_seconds: int = Form(30),
):
    _require_feature_flag()
    if not await validate_csrf_token(request):
        return JSONResponse(
            status_code=403,
            content={"error": {"code": "CSRF_INVALID", "message": "Invalid CSRF token"}},
        )

    key = _scenario_key(scenario)

    r = get_redis_client()
    await r.set(key, "1", ex=duration_seconds)

    if scenario == "throttle_spike":
        import time as _time

        from app.metrics import THROTTLE_RISK_SCORE

        now = _time.time()
        spike_data: dict[str, float] = {}
        for i in range(15):
            spike_data[str(now - i * 2)] = now - i * 2
        await r.zadd("throttle:window:youtube", spike_data)
        await r.expire("throttle:window:youtube", settings.throttle_window_seconds * 2)
        THROTTLE_RISK_SCORE.labels(service="youtube", provider="yt-dlp").set(1.0)

    logger.info(
        "chaos_injected",
        scenario=scenario,
        duration_seconds=duration_seconds,
        redis_key=key,
    )

    return {
        "data": {
            "scenario": scenario,
            "duration_seconds": duration_seconds,
            "status": "active",
        },
        "message": f"Chaos scenario '{scenario}' injected for {duration_seconds}s",
    }


@router.post("/reset")
async def reset_chaos(
    request: Request,
    _user: CurrentUserFromCookie,
):
    _require_feature_flag()
    if not await validate_csrf_token(request):
        return JSONResponse(
            status_code=403,
            content={"error": {"code": "CSRF_INVALID", "message": "Invalid CSRF token"}},
        )

    deleted = await delete_chaos_keys()

    logger.info("chaos_reset", keys_deleted=deleted)

    return {
        "data": {"scenarios_reset": deleted},
        "message": "All chaos scenarios reset",
    }


@router.get("/status", response_model=dict)
async def chaos_status(
    request: Request,
    _user: CurrentUserFromCookie,
):
    _require_feature_flag()

    from app.services.redis_client import KEY_TO_SCENARIO_FIELD

    r = get_redis_client()
    status = ChaosStatus()
    for key, field_name in KEY_TO_SCENARIO_FIELD.items():
        exists = await r.exists(key)
        setattr(status, field_name, bool(exists))

    return {"data": status.model_dump()}


@router.post("/submit-videos")
async def chaos_submit_videos(
    request: Request,
    _user: CurrentUserFromCookie,
    db: DbSession,
    count: int = Form(default=10),
):
    """Bulk submit demo video URLs for chaos lab.

    Creates N random download jobs from the demo URL pool.
    Only available when FEATURE_CHAOS_API_ENABLED=true.
    """
    _require_feature_flag()
    if not await validate_csrf_token(request):
        return JSONResponse(
            status_code=403,
            content={"error": {"code": "CSRF_INVALID", "message": "Invalid CSRF token"}},
        )

    from app.services.job_factory import create_demo_jobs_bulk
    from app.utils.demo_urls import random_demo_urls

    count = max(1, min(count, 50))  # Sanity cap at 50
    urls = random_demo_urls(count)

    created = await create_demo_jobs_bulk(
        db,
        _user.id,
        urls,
        enqueue=True,
        stagger_delay=0.15,
    )

    return {
        "data": {
            "submitted": len(created),
            "requested": count,
            "urls": [j.url for j in created],
        },
        "message": f"Submitted {len(created)} demo videos for processing",
    }

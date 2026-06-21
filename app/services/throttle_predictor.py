import time

from app.logging_config import get_logger
from app.services.redis_client import get_redis_client
from core.config import settings
from core.metrics import THROTTLE_RISK_SCORE

logger = get_logger(__name__)

THROTTLE_WINDOW_KEY = "throttle:window:{service}"


async def record_response(service: str, status_code: int) -> None:
    if status_code != 429:
        return

    r = get_redis_client()
    key = THROTTLE_WINDOW_KEY.format(service=service)
    now = time.time()
    cutoff = now - settings.throttle_window_seconds

    try:
        await r.zremrangebyscore(key, "-inf", cutoff)
        await r.zadd(key, {str(now): now})
        await r.expire(key, settings.throttle_window_seconds * 2)
    except Exception:
        logger.warning("throttle_record_failed", service=service, exc_info=True)
        return

    risk = await get_risk_score(service)
    THROTTLE_RISK_SCORE.labels(service=service, provider="yt-dlp").set(risk)

    await risk_check_and_warn(service, risk)


async def get_risk_score(service: str) -> float:
    r = get_redis_client()
    key = THROTTLE_WINDOW_KEY.format(service=service)
    try:
        count: int = await r.zcard(key)
    except Exception:
        logger.warning("throttle_risk_check_failed", service=service, exc_info=True)
        return 0.0
    return min(count / settings.throttle_risk_threshold_scale, 1.0)


async def risk_check_and_warn(service: str, risk: float | None = None) -> None:
    if risk is None:
        risk = await get_risk_score(service)
    if risk >= settings.throttle_risk_threshold:
        logger.warning("throttle_risk_high", service=service, risk_score=risk)

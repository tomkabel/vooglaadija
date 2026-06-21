"""Dashboard and demo web page routes."""

from fastapi import APIRouter, HTTPException, Request

from app.api.routes.web.web_helpers import (
    get_csrf_token,
    get_template_context,
    set_csrf_token_cookie,
    templates,
)
from core.config import settings
from core.redis_client import get_all_chaos_status

router = APIRouter(tags=["web"])


@router.get("/chaos-lab")
async def chaos_lab_page(request: Request):
    """Render the chaos engineering lab page for live demo."""
    if not settings.feature_chaos_api_enabled:
        raise HTTPException(status_code=404, detail="Not Found")

    token = get_csrf_token(request)
    response = templates.TemplateResponse(
        request,
        "chaos-lab.html",
        get_template_context(request, csrf_token=token),
    )
    set_csrf_token_cookie(response, token)
    return response


@router.get("/chaos-lab/status")
async def chaos_lab_status(request: Request):
    """HTMX partial: return current chaos flag status for polling."""
    if not settings.feature_chaos_api_enabled:
        raise HTTPException(status_code=404, detail="Not Found")

    status_data = await get_all_chaos_status()

    return templates.TemplateResponse(
        request,
        "partials/_chaos_status.html",
        get_template_context(request, status=status_data),
    )


@router.get("/slides")
async def presentation_slides(request: Request):
    """Render the TOP1 demo presentation slides."""
    return templates.TemplateResponse(
        request,
        "slides/presentation.html",
        {},
    )

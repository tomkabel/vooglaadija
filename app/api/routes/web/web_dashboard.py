"""Dashboard and demo web page routes."""

from fastapi import APIRouter, HTTPException, Request
from starlette.templating import _TemplateResponse as TemplateResponse

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
async def chaos_lab_page(request: Request) -> TemplateResponse:
    """
    Render the chaos engineering lab page when the chaos API feature is enabled.
    
    Returns:
        TemplateResponse: The rendered chaos engineering lab page.
    
    Raises:
        HTTPException: If the chaos API feature is disabled.
    """
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
async def chaos_lab_status(request: Request) -> TemplateResponse:
    """
    Render the current chaos status for HTMX polling.
    
    Raises:
        HTTPException: If the chaos API feature is disabled.
    
    Returns:
        TemplateResponse: The rendered chaos status partial.
    """
    if not settings.feature_chaos_api_enabled:
        raise HTTPException(status_code=404, detail="Not Found")

    status_data = await get_all_chaos_status()

    return templates.TemplateResponse(
        request,
        "partials/_chaos_status.html",
        get_template_context(request, status=status_data),
    )


@router.get("/slides")
async def presentation_slides(request: Request) -> TemplateResponse:
    """Render the TOP1 demo presentation slides."""
    return templates.TemplateResponse(
        request,
        "slides/presentation.html",
        {"nonce": getattr(request.state, "nonce", "")},
    )

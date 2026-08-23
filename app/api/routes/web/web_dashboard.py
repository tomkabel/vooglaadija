"""Dashboard and demo web page routes."""

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse

from app.api.routes.web.web_helpers import (
    get_template_context,
    render_csrf_page,
    templates,
)
from core.config import settings
from core.redis_client import get_all_chaos_status

router = APIRouter(tags=["web"])


@router.get("/chaos-lab")
async def chaos_lab_page(request: Request) -> HTMLResponse:
    """
    Render the chaos engineering lab page when the chaos API feature is enabled.

    Returns:
        HTMLResponse: The rendered chaos engineering lab page.

    Raises:
        HTTPException: If the chaos API feature is disabled.
    """
    if not settings.feature_chaos_api_enabled:
        raise HTTPException(status_code=404, detail="Not Found")

    return render_csrf_page(request, "chaos-lab.html")


@router.get("/chaos-lab/status")
async def chaos_lab_status(request: Request) -> HTMLResponse:
    """
    Render the current chaos status for HTMX polling.

    Raises:
        HTTPException: If the chaos API feature is disabled.

    Returns:
        HTMLResponse: The rendered chaos status partial.
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
async def presentation_slides(request: Request) -> HTMLResponse:
    """Render the TOP1 demo presentation slides."""
    return templates.TemplateResponse(
        request,
        "slides/presentation.html",
        {"nonce": getattr(request.state, "nonce", "")},
    )

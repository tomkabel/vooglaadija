"""Terms of Service web page route."""

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse

from app.api.routes.web.web_helpers import render_csrf_page

router = APIRouter(tags=["web"])


@router.get("/terms")
async def terms_page(request: Request) -> HTMLResponse:
    """Render terms of service page with copyright disclaimers and lawful-use requirements."""
    return render_csrf_page(request, "terms.html", last_updated="April 26, 2026")

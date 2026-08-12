"""Terms of Service web page route."""

from fastapi import APIRouter, Request

from app.api.routes.web.web_helpers import (
    get_csrf_token,
    get_template_context,
    set_csrf_token_cookie,
    templates,
)

router = APIRouter(tags=["web"])


@router.get("/terms")
async def terms_page(request: Request):
    """Render terms of service page with copyright disclaimers and lawful-use requirements."""
    token = get_csrf_token(request)
    response = templates.TemplateResponse(
        request,
        "terms.html",
        get_template_context(request, csrf_token=token, last_updated="April 26, 2026"),
    )
    set_csrf_token_cookie(response, token)
    return response

"""Auth-related web routes.

With Clerk handling authentication, these routes render Clerk's
hosted/sign-in/sign-up components or redirect to Clerk's UI.
"""

from typing import Annotated

from fastapi import APIRouter, Query, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from app.api.dependencies import CurrentUserFromCookie
from app.api.routes.web.web_helpers import (
    _htmx_or_redirect,
    get_template_context,
    logger,
    templates,
)
from core.config import settings

router = APIRouter(tags=["web"])


@router.get("/login")
async def login_page(
    request: Request,
    return_url: str = "/web/downloads",
    error: Annotated[str | None, Query(max_length=100)] = None,
) -> HTMLResponse:
    """Render the login page with Clerk SignIn component."""
    context = get_template_context(
        request,
        return_url=return_url,
        error=error,
        clerk_publishable_key=settings.clerk_publishable_key,
    )
    return templates.TemplateResponse(request, "login.html", context)


@router.get("/register")
async def register_page(
    request: Request,
    error: Annotated[str | None, Query(max_length=100)] = None,
) -> HTMLResponse:
    """Render the register page with Clerk SignUp component."""
    context = get_template_context(
        request,
        error=error,
        clerk_publishable_key=settings.clerk_publishable_key,
    )
    return templates.TemplateResponse(request, "register.html", context)


@router.post("/logout", response_model=None)
async def logout(request: Request) -> HTMLResponse | RedirectResponse:
    """Log out by clearing the Clerk session cookie and redirecting."""
    redirect = RedirectResponse(url="/web/login?logged_out=1", status_code=303)
    redirect.delete_cookie(key="__session", path="/")
    return redirect


@router.post("/settings/password", response_model=None)
async def change_password(
    request: Request,
    *,
    current_user: CurrentUserFromCookie,
) -> HTMLResponse | RedirectResponse:
    """Redirect to Clerk's account management for password change."""
    return _htmx_or_redirect(
        request,
        200,
        '<a href="/web/account" class="text-amber-400">Manage account in Clerk</a>',
        "/web/settings?info=use_clerk",
    )


@router.get("/account")
async def account_redirect(request: Request) -> RedirectResponse:
    """Redirect to Clerk's hosted account management page."""
    return RedirectResponse(url="https://accounts.clerk.com/user", status_code=303)

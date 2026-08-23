"""Authentication endpoints (REST API).

With Clerk handling authentication, these endpoints are simplified:
- /me returns the current user profile
- /login and /register redirect to Clerk's hosted UI
"""

from fastapi import APIRouter, status
from fastapi.responses import RedirectResponse

from app.api.dependencies import CurrentUser
from app.schemas.user import UserResponse

router = APIRouter(prefix="/auth", tags=["auth"])


@router.get(
    "/me",
    response_model=UserResponse,
    summary="Get current user",
    description="Return the authenticated user's profile based on Clerk bearer token.",
    responses={
        200: {
            "description": "Current user profile",
            "content": {
                "application/json": {
                    "example": {
                        "id": "f47ac10b-58cc-4372-a567-0e02b2c3d479",
                        "email": "user@example.com",
                    }
                }
            },
        },
        401: {
            "description": "Unauthorized",
            "content": {
                "application/json": {
                    "example": {"detail": "Could not validate credentials"}
                }
            },
        },
    },
)
async def me(current_user: CurrentUser) -> UserResponse:
    return UserResponse(id=current_user.id, email=current_user.email)


@router.post("/login")
async def login() -> RedirectResponse:
    """Redirect to Clerk's hosted sign-in page."""
    return RedirectResponse(url="/web/login", status_code=303)


@router.post("/register")
async def register() -> RedirectResponse:
    """Redirect to Clerk's hosted sign-up page."""
    return RedirectResponse(url="/web/register", status_code=303)


@router.post("/logout")
async def logout() -> RedirectResponse:
    """Redirect to Clerk's sign-out handler (clears session)."""
    return RedirectResponse(url="/web/logout", status_code=303)

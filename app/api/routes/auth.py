"""Authentication endpoints (REST API)."""

from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from fastapi import APIRouter, HTTPException, Request, Response, status
from fastapi.responses import RedirectResponse
from sqlalchemy import select

from app.api.dependencies import CurrentUser, DbSession
from app.api.rate_limit_config import limiter
from app.auth import (
    REFRESH_TOKEN_TYPE,
    clear_token_cookies,
    create_access_token,
    create_refresh_token,
    get_auth_cookie_names,
    set_token_cookies,
    verify_token,
)
from app.schemas.error import ErrorCode, error_response_doc, success_response_doc
from app.schemas.token import Token, TokenRefresh
from app.schemas.user import UserCreate, UserResponse
from app.services.auth_service import verify_password
from app.services.user_service import DuplicateEmailError, UserService
from core.models.user import User, not_deleted

router = APIRouter(prefix="/auth", tags=["auth"])


@router.post(
    "/register",
    response_model=UserResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Register a new user",
    description="Create a user account with email and password.",
    responses={
        201: success_response_doc(
            "User created successfully",
            {"id": "f47ac10b-58cc-4372-a567-0e02b2c3d479", "email": "user@example.com"},
        ),
        409: error_response_doc(
            "Email already registered",
            ErrorCode.RESOURCE_CONFLICT,
            "Email already registered",
        ),
        422: error_response_doc(
            "Validation error",
            ErrorCode.VALIDATION_ERROR,
            "Request validation failed",
            details={
                "validation_errors": [
                    {
                        "field": "password",
                        "message": "Password must be at least 8 characters",
                        "type": "value_error",
                    },
                ],
            },
        ),
        429: error_response_doc(
            "Rate limit exceeded",
            ErrorCode.RATE_LIMIT_EXCEEDED,
            "Rate limit exceeded. Try again in 42 seconds.",
        ),
    },
)
@limiter.limit("5/minute")
async def register(
    request: Request,
    user_data: UserCreate,
    db: DbSession,
) -> UserResponse:
    try:
        user = await UserService(db=db).register(user_data.email, user_data.password)
    except DuplicateEmailError:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Email already registered",
        ) from None

    return UserResponse(id=user.id, email=user.email)


@router.post(
    "/login",
    response_model=Token,
    summary="Authenticate user",
    description="Authenticate with email and password and receive access/refresh JWT tokens.",
    responses={
        200: success_response_doc(
            "Authentication successful",
            {
                "access_token": "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.access",
                "refresh_token": "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.refresh",
                "token_type": "bearer",
            },
        ),
        401: error_response_doc(
            "Invalid credentials or inactive user",
            ErrorCode.UNAUTHORIZED,
            "Incorrect email or password",
        ),
        422: error_response_doc(
            "Validation error",
            ErrorCode.VALIDATION_ERROR,
            "Request validation failed",
            details={
                "validation_errors": [
                    {
                        "field": "email",
                        "message": "value is not a valid email address",
                        "type": "value_error",
                    },
                ],
            },
        ),
        429: error_response_doc(
            "Rate limit exceeded",
            ErrorCode.RATE_LIMIT_EXCEEDED,
            "Rate limit exceeded. Try again in 42 seconds.",
        ),
    },
)
@limiter.limit("5/minute")
async def login(
    request: Request,
    response: Response,
    user_data: UserCreate,
    db: DbSession,
) -> Token:
    """
    Authenticate a user and issue access and refresh tokens.

    Parameters:
        user_data (UserCreate): User email and password used for authentication.

    Returns:
        Token: The access token, refresh token, and bearer token type.

    Raises:
        HTTPException: If the credentials are invalid or the user account is inactive.
    """
    result = await db.execute(select(User).where(User.email == user_data.email, not_deleted()))
    user = result.scalar_one_or_none()

    if user is None or not await verify_password(user_data.password, user.password_hash):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Incorrect email or password",
            headers={"WWW-Authenticate": "Bearer"},
        )

    if not user.is_active:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="User account is inactive",
            headers={"WWW-Authenticate": "Bearer"},
        )

    access_token = create_access_token(user.id, token_version=user.token_version)
    refresh_token = create_refresh_token(user.id, token_version=user.token_version)

    set_token_cookies(response, access_token, refresh_token)

    return Token(
        access_token=access_token,
        refresh_token=refresh_token,
        token_type="bearer",  # noqa: S106
    )


@router.post(
    "/refresh",
    response_model=Token,
    summary="Refresh access token",
    description="Exchange a valid refresh token for a new access token and refresh token pair.",
    responses={
        200: success_response_doc(
            "Token refreshed successfully",
            {
                "access_token": "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.newaccess",
                "refresh_token": "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.newrefresh",
                "token_type": "bearer",
            },
        ),
        401: error_response_doc(
            "Invalid or expired refresh token",
            ErrorCode.UNAUTHORIZED,
            "Invalid or expired refresh token",
        ),
        422: error_response_doc(
            "Validation error",
            ErrorCode.VALIDATION_ERROR,
            "Request validation failed",
            details={
                "validation_errors": [
                    {"field": "refresh_token", "message": "Field required", "type": "missing"},
                ],
            },
        ),
        429: error_response_doc(
            "Rate limit exceeded",
            ErrorCode.RATE_LIMIT_EXCEEDED,
            "Rate limit exceeded. Try again in 42 seconds.",
        ),
    },
)
@limiter.limit("5/minute")
async def refresh(
    request: Request,
    response: Response,
    db: DbSession,
    token_refresh: TokenRefresh | None = None,
) -> Token:
    # Accept refresh token from body or from HttpOnly cookie
    # This allows JS-free refresh via credentials: 'include' sending the cookie
    """
    Issue replacement access and refresh tokens using a valid refresh token supplied in the request body or cookie.

    Parameters:
        token_refresh (TokenRefresh | None): Optional request-body refresh token; the refresh-token cookie is used when omitted.

    Returns:
        Token: Newly issued access and refresh tokens.

    Raises:
        HTTPException: If the refresh token is missing, invalid, expired, revoked, malformed, or belongs to an inactive or nonexistent user.
    """
    refresh_token_str = token_refresh.refresh_token if token_refresh else None
    if not refresh_token_str:
        refresh_token_str = request.cookies.get(get_auth_cookie_names()[1])

    if not refresh_token_str:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Refresh token required",
            headers={"WWW-Authenticate": "Bearer"},
        )

    payload = verify_token(refresh_token_str, expected_type=REFRESH_TOKEN_TYPE)

    if payload is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired refresh token",
            headers={"WWW-Authenticate": "Bearer"},
        )

    user_id = payload.get("sub")
    if user_id is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid token payload",
            headers={"WWW-Authenticate": "Bearer"},
        )

    try:
        user_uuid = UUID(user_id)
    except (ValueError, TypeError) as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid token payload",
            headers={"WWW-Authenticate": "Bearer"},
        ) from exc

    result = await db.execute(select(User).where(User.id == user_uuid, not_deleted()))
    user = result.scalar_one_or_none()

    if user is None or not user.is_active:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="User not found or inactive",
            headers={"WWW-Authenticate": "Bearer"},
        )

    token_ver = payload.get("ver", 1)
    if token_ver != user.token_version:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Token has been revoked",
            headers={"WWW-Authenticate": "Bearer"},
        )

    # Atomically reserve the consumed refresh token's jti BEFORE minting new
    # tokens: only one parallel request can reserve a jti, so a replayed or
    # concurrently-used refresh token is rejected here instead of racing the
    # blacklist write that used to happen after issuance.
    from app.services.token_blacklist import reserve_token_jti

    old_jti = payload.get("jti")
    if old_jti:
        remaining = max(int(payload.get("exp", 0)) - int(datetime.now(UTC).timestamp()), 60)
        if not await reserve_token_jti(old_jti, ttl_seconds=remaining):
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Refresh token has already been used",
                headers={"WWW-Authenticate": "Bearer"},
            )

    access_token = create_access_token(user.id, token_version=user.token_version)
    new_refresh_token = create_refresh_token(user.id, token_version=user.token_version)

    # Set JWT tokens as HttpOnly cookies for HTMX/browser auth
    set_token_cookies(response, access_token, new_refresh_token)

    return Token(
        access_token=access_token,
        refresh_token=new_refresh_token,
        token_type="bearer",  # noqa: S106
    )


@router.get(
    "/me",
    response_model=UserResponse,
    summary="Get current user",
    description="Return the authenticated user's profile based on bearer access token.",
    responses={
        200: success_response_doc(
            "Current user profile",
            {"id": "f47ac10b-58cc-4372-a567-0e02b2c3d479", "email": "user@example.com"},
        ),
        401: error_response_doc(
            "Unauthorized",
            ErrorCode.UNAUTHORIZED,
            "Could not validate credentials",
        ),
    },
)
async def me(current_user: CurrentUser) -> UserResponse:
    return UserResponse(id=current_user.id, email=current_user.email)


async def _blacklist_token_cookie(
    token_str: str | None,
    verify_fn: Any,
    blacklist_fn: Any,
) -> None:
    """
    Blacklist the token identified by a valid cookie value.

    The token's remaining lifetime determines the blacklist duration, with a minimum of 60 seconds.
    """
    if not token_str:
        return
    payload = verify_fn(token_str)
    if not payload:
        return
    jti = payload.get("jti")
    if not jti:
        return
    remaining = max(int(payload.get("exp", 0)) - int(datetime.now(UTC).timestamp()), 60)
    await blacklist_fn(jti, ttl_seconds=remaining)


@router.post("/logout")
async def logout(request: Request) -> RedirectResponse:
    """Clear auth cookies and redirect to login.

    Logout is a POST action to prevent CSRF from logout links.
    Blacklists the current access and refresh tokens' jti for their
    remaining lifetimes, preventing token reuse even if exfiltrated.
    """
    from app.services.token_blacklist import blacklist_token

    await _blacklist_token_cookie(
        request.cookies.get(get_auth_cookie_names()[0]),
        verify_token,
        blacklist_token,
    )
    await _blacklist_token_cookie(
        request.cookies.get(get_auth_cookie_names()[1]),
        verify_token,
        blacklist_token,
    )

    redirect = RedirectResponse(url="/web/login?logged_out=1", status_code=303)
    clear_token_cookies(redirect)
    return redirect

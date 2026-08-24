"""Shared API dependencies for authentication and authorization."""

from typing import Annotated
from uuid import UUID

from fastapi import Depends, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import ACCESS_TOKEN_TYPE, get_auth_cookie_names, verify_token
from app.services.api_key_service import ApiKeyService
from app.services.token_blacklist import is_token_blacklisted
from core.database import get_db
from core.models.api_key import API_KEY_TOKEN_PREFIX
from core.models.user import User, not_deleted

security = HTTPBearer(auto_error=False)


DbSession = Annotated[AsyncSession, Depends(get_db)]

credentials_exception = HTTPException(
    status_code=status.HTTP_401_UNAUTHORIZED,
    detail="Could not validate credentials",
    headers={"WWW-Authenticate": "Bearer"},
)


def _init_request_auth_state(request: Request) -> None:
    """Ensure request.state carries auth context for scope checks."""
    if not hasattr(request.state, "auth_method"):
        request.state.auth_method = None
    if not hasattr(request.state, "api_key_scopes"):
        request.state.api_key_scopes = ["*"]
    if not hasattr(request.state, "api_key_id"):
        request.state.api_key_id = None


async def _resolve_user_from_token(
    db: AsyncSession,
    token: str,
    expected_type: str,
    request: Request,
) -> User:
    payload = verify_token(token, expected_type=expected_type)
    if payload is None:
        raise credentials_exception

    user_id = payload.get("sub")
    if user_id is None or not isinstance(user_id, str):
        raise credentials_exception

    try:
        user_uuid = UUID(user_id)
    except (ValueError, TypeError):
        raise credentials_exception from None

    token_jti = payload.get("jti")
    if token_jti and await is_token_blacklisted(token_jti):
        raise credentials_exception

    result = await db.execute(select(User).where(User.id == user_uuid, not_deleted()))
    user: User | None = result.scalar_one_or_none()

    if user is None:
        raise credentials_exception

    if not user.is_active:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="User account is inactive",
            headers={"WWW-Authenticate": "Bearer"},
        )

    token_version = payload.get("ver", 1)
    if token_version != user.token_version:
        raise credentials_exception

    _init_request_auth_state(request)
    request.state.auth_method = "jwt"
    request.state.api_key_scopes = ["*"]
    request.state.api_key_id = None
    return user


async def _resolve_user_from_api_key(
    db: AsyncSession,
    token: str,
    request: Request,
) -> User:
    api_key = await ApiKeyService.authenticate(db, token)
    if api_key is None:
        raise credentials_exception

    # Load the owning user explicitly rather than relying on the lazy `user`
    # relationship, which is unsafe under the async session (MissingGreenlet).
    result = await db.execute(select(User).where(User.id == api_key.user_id, not_deleted()))
    user = result.scalar_one_or_none()
    if user is None:
        raise credentials_exception
    if not user.is_active:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="User account is inactive",
            headers={"WWW-Authenticate": "Bearer"},
        )

    _init_request_auth_state(request)
    request.state.auth_method = "api_key"
    request.state.api_key_scopes = api_key.scopes_list
    request.state.api_key_id = str(api_key.id)
    return user


async def get_current_user(
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(security)],
    db: DbSession,
    request: Request,
) -> User:
    """
    Resolve the authenticated user from a JWT access token or a personal
    access token (PAT). PATs are detected by their ``vlj_pat_`` prefix and
    resolve to their owning user subject to the key's granted scopes.
    """
    _init_request_auth_state(request)
    if credentials is None:
        raise credentials_exception

    token = credentials.credentials
    if token.startswith(API_KEY_TOKEN_PREFIX):
        return await _resolve_user_from_api_key(db, token, request)
    return await _resolve_user_from_token(db, token, ACCESS_TOKEN_TYPE, request)


async def get_current_user_from_cookie(
    db: DbSession,
    request: Request,
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(security)] = None,
) -> User:
    """
    Retrieve the authenticated user from bearer credentials or an access-token cookie.
    """
    _init_request_auth_state(request)
    token = None
    if credentials is not None:
        token = credentials.credentials
    else:
        token = request.cookies.get(get_auth_cookie_names()[0])
    if token is None:
        raise credentials_exception
    # Cookies only ever carry JWT access tokens.
    return await _resolve_user_from_token(db, token, ACCESS_TOKEN_TYPE, request)


CurrentUser = Annotated[User, Depends(get_current_user)]
CurrentUserFromCookie = Annotated[User, Depends(get_current_user_from_cookie)]


def require_scope(required: str):
    """
    Build a dependency that enforces a concrete API-key scope.

    JWT-authenticated sessions (and wildcard-scoped keys) always pass. A
    PAT-scoped request is rejected with 403 unless it holds the required scope
    or the wildcard scope.
    """

    async def _check(request: Request, _: CurrentUser) -> None:
        scopes = getattr(request.state, "api_key_scopes", ["*"])
        if "*" in scopes or required in scopes:
            return
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=f"Insufficient scope: requires '{required}'",
            headers={"WWW-Authenticate": 'Bearer error="insufficient_scope"'},
        )

    return _check


# Reusable scope guards for the data-plane endpoints.
ReadScope = Depends(require_scope("downloads:read"))
WriteScope = Depends(require_scope("downloads:write"))
KeysAdminScope = Depends(require_scope("keys:admin"))

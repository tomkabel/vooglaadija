from typing import Annotated

from fastapi import Depends, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.ext.asyncio import AsyncSession

from app.clerk_auth import verify_clerk_token
from core.database import get_db
from core.models.user import User

security = HTTPBearer(auto_error=False)


DbSession = Annotated[AsyncSession, Depends(get_db)]


async def get_current_user(
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(security)],
    db: DbSession,
) -> User:
    """Retrieve the authenticated user from a Clerk bearer token.

    Parameters:
        credentials: Bearer credentials from the Authorization header.
        db: Database session for user lookup/sync.

    Returns:
        User: The authenticated user.

    Raises:
        HTTPException: If the token is missing or invalid.
    """
    if credentials is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Could not validate credentials",
            headers={"WWW-Authenticate": "Bearer"},
        )

    user = await verify_clerk_token(db, credentials.credentials)
    if user is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Could not validate credentials",
            headers={"WWW-Authenticate": "Bearer"},
        )

    if not user.is_active:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="User account is inactive",
            headers={"WWW-Authenticate": "Bearer"},
        )

    return user


async def get_current_user_from_cookie(
    db: DbSession,
    request: Request,
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(security)] = None,
) -> User:
    """Retrieve the authenticated user from bearer credentials or a Clerk session cookie.

    Clerk's frontend SDK stores the session token in a cookie named
    `__session` (or a custom name). We accept tokens from either the
    Authorization header or the Clerk session cookie.

    Parameters:
        db: Database session for user lookup/sync.
        request: Request containing cookies.
        credentials: Optional bearer credentials.

    Returns:
        User: The authenticated user.
    """
    token = None
    if credentials is not None:
        token = credentials.credentials
    else:
        token = request.cookies.get("__session")

    user = await verify_clerk_token(db, token)
    if user is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Could not validate credentials",
            headers={"WWW-Authenticate": "Bearer"},
        )

    if not user.is_active:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="User account is inactive",
            headers={"WWW-Authenticate": "Bearer"},
        )

    return user


CurrentUser = Annotated[User, Depends(get_current_user)]
CurrentUserFromCookie = Annotated[User, Depends(get_current_user_from_cookie)]

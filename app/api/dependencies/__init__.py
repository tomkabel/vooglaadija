from typing import Annotated
from uuid import UUID

from fastapi import Depends, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import verify_token
from app.database import get_db
from app.models.user import User, not_deleted
from app.services.token_blacklist import is_token_blacklisted

security = HTTPBearer(auto_error=False)


DbSession = Annotated[AsyncSession, Depends(get_db)]


async def _resolve_user_from_token(
    db: AsyncSession,
    token: str | None,
    expected_type: str | None,
) -> User:
    credentials_exception = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Could not validate credentials",
        headers={"WWW-Authenticate": "Bearer"},
    )

    if not token:
        raise credentials_exception

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

    return user


async def get_current_user_from_cookie(
    db: DbSession,
    request: Request,
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(security)] = None,
) -> User:
    token = None
    if credentials is not None:
        token = credentials.credentials
    else:
        token = request.cookies.get("access_token")
    return await _resolve_user_from_token(db, token, expected_type=None)


async def get_current_user(
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(security)],
    db: DbSession,
) -> User:
    if credentials is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Could not validate credentials",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return await _resolve_user_from_token(db, credentials.credentials, expected_type=None)


CurrentUser = Annotated[User, Depends(get_current_user)]
CurrentUserFromCookie = Annotated[User, Depends(get_current_user_from_cookie)]

from typing import Annotated
from uuid import UUID

from fastapi import Depends, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import ACCESS_TOKEN_TYPE, get_auth_cookie_names, verify_token
from app.services.pat_service import PATService
from app.services.token_blacklist import is_token_blacklisted
from core.database import get_db
from core.models.user import User, not_deleted

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


async def _resolve_user_from_pat(
    db: AsyncSession,
    token: str | None,
) -> tuple[User, "PATAuthContext"] | None:
    if not token:
        return None

    service = PATService(db)
    pat = await service.authenticate(token)

    if pat is None:
        return None

    await service.record_usage(pat.id)
    await db.commit()

    from core.models.personal_access_token import PersonalAccessToken

    result = await db.execute(
        select(User).where(User.id == pat.user_id, not_deleted())
    )
    user: User | None = result.scalar_one_or_none()

    if user is None or not user.is_active:
        return None

    scopes = pat.scopes.split(",") if pat.scopes else []
    context = PATAuthContext(user=user, scopes=scopes, pat_id=pat.id)
    return user, context


async def get_current_user_from_cookie(
    db: DbSession,
    request: Request,
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(security)] = None,
) -> User:
    """
    Retrieve the authenticated user from bearer credentials or an access-token cookie.

    Parameters:
        db (DbSession): Database session used to load the user.
        request (Request): Request containing the access-token cookie when bearer credentials are unavailable.
        credentials (HTTPAuthorizationCredentials | None): Optional bearer credentials.

    Returns:
        User: The authenticated user.
    """
    token = None
    if credentials is not None:
        token = credentials.credentials
    else:
        token = request.cookies.get(get_auth_cookie_names()[0])
    return await _resolve_user_from_token(db, token, expected_type=ACCESS_TOKEN_TYPE)


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
    return await _resolve_user_from_token(
        db,
        credentials.credentials,
        expected_type=ACCESS_TOKEN_TYPE,
    )


async def get_current_user_with_pat(
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(security)],
    db: DbSession,
) -> User:
    """
    Authenticate via JWT or Personal Access Token.

    Supports both traditional JWT bearer tokens and long-lived PATs
    for machine/agents. PATs use the prefix 'vpat_'.

    Parameters:
        credentials: Bearer credentials from the Authorization header.
        db: Database session.

    Returns:
        User: The authenticated user.
    """
    if credentials is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Could not validate credentials",
            headers={"WWW-Authenticate": "Bearer"},
        )

    token = credentials.credentials

    try:
        user = await _resolve_user_from_token(db, token, expected_type=ACCESS_TOKEN_TYPE)
        return user
    except HTTPException:
        pass

    pat_result = await _resolve_user_from_pat(db, token)
    if pat_result is not None:
        return pat_result[0]

    raise HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Could not validate credentials",
        headers={"WWW-Authenticate": "Bearer"},
    )


class PATAuthContext:
    """Holds PAT authentication context including scopes."""

    def __init__(self, user: User, scopes: list[str], pat_id: UUID) -> None:
        self.user = user
        self.scopes = scopes
        self.pat_id = pat_id

    def has_scope(self, scope: str) -> bool:
        return scope in self.scopes


async def get_pat_auth_context(
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(security)],
    db: DbSession,
) -> PATAuthContext:
    """
    Authenticate via Personal Access Token and return the auth context with scopes.

    This dependency is used for endpoints that need to verify PAT scopes
    for fine-grained access control.

    Parameters:
        credentials: Bearer credentials from the Authorization header.
        db: Database session.

    Returns:
        PATAuthContext: The authentication context with user and scopes.

    Raises:
        HTTPException: If authentication fails or the token is not a PAT.
    """
    if credentials is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Could not validate credentials",
            headers={"WWW-Authenticate": "Bearer"},
        )

    token = credentials.credentials

    pat_result = await _resolve_user_from_pat(db, token)
    if pat_result is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Could not validate credentials",
            headers={"WWW-Authenticate": "Bearer"},
        )

    return pat_result[1]


CurrentUser = Annotated[User, Depends(get_current_user)]
CurrentUserFromCookie = Annotated[User, Depends(get_current_user_from_cookie)]
CurrentUserWithPAT = Annotated[User, Depends(get_current_user_with_pat)]
PATContext = Annotated[PATAuthContext, Depends(get_pat_auth_context)]

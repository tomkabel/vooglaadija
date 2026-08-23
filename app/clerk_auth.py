"""Clerk authentication module.

Handles JWT verification via Clerk's JWKS endpoint and user synchronization.
Replaces the previous custom JWT implementation.
"""

from typing import Any
from uuid import UUID

from fastapi import Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from core.config import settings
from core.logging_config import get_logger
from core.models.user import User, not_deleted

logger = get_logger(__name__)


async def verify_clerk_token(
    db: AsyncSession,
    token: str | None,
) -> User | None:
    """Verify a Clerk JWT token and return the corresponding local user.

    Uses Clerk's authenticate_request to validate the token via JWKS.
    The local user is synced/created on first sight from Clerk claims.

    Parameters:
        db: Database session for user lookup/sync.
        token: The Clerk session JWT from the Authorization header or cookie.

    Returns:
        The authenticated local user, or None if verification fails.
    """
    if not token:
        return None

    try:
        from clerk_backend_api import Clerk
        from clerk_backend_api.jwks_helpers import AuthenticateRequestOptions

        sdk = Clerk(bearer_auth=settings.clerk_secret_key)

        # Build an ASGI-like request dict for Clerk's authenticator
        request_data = {
            "headers": {"authorization": f"Bearer {token}"},
            "cookies": {},
        }

        request_state = sdk.authenticate_request(
            request_data,
            AuthenticateRequestOptions(
                authorized_parties=settings.clerk_authorized_parties.split(",")
                if settings.clerk_authorized_parties
                else None,
            ),
        )

        if not request_state.is_signed_in:
            return None

        claims = request_state.payload
        if not claims:
            return None

        clerk_user_id = claims.get("sub")
        if not clerk_user_id:
            return None

    except Exception:
        logger.warning("clerk_token_verification_failed", exc_info=True)
        return None

    user = await _sync_clerk_user(db, clerk_user_id, claims)
    return user


async def _sync_clerk_user(
    db: AsyncSession,
    clerk_user_id: str,
    claims: dict[str, Any],
) -> User | None:
    """Sync a Clerk user to the local database.

    Creates or updates the local user record from Clerk claims.

    Parameters:
        db: Database session.
        clerk_user_id: The Clerk user ID (subject claim).
        claims: The full JWT claims payload.

    Returns:
        The synced local user.
    """
    result = await db.execute(
        select(User).where(User.clerk_user_id == clerk_user_id, not_deleted())
    )
    user = result.scalar_one_or_none()

    email = _extract_email(claims)

    if user is None:
        user = User(
            id=UUID(clerk_user_id) if _is_uuid(clerk_user_id) else UUID(int=0),
            clerk_user_id=clerk_user_id,
            email=email or "",
        )
        db.add(user)
        try:
            await db.commit()
        except Exception:
            await db.rollback()
            logger.error("clerk_user_sync_failed", clerk_user_id=clerk_user_id, exc_info=True)
            return None
        await db.refresh(user)
        logger.info("clerk_user_synced", clerk_user_id=clerk_user_id, email=email)
    elif email and user.email != email:
        user.email = email
        try:
            await db.commit()
        except Exception:
            await db.rollback()

    return user


def _extract_email(claims: dict[str, Any]) -> str | None:
    """Extract the primary email from Clerk JWT claims."""
    email = claims.get("email")
    if isinstance(email, str):
        return email

    # v2 token format nests email_addresses
    email_addresses = claims.get("email_addresses")
    if isinstance(email_addresses, list) and email_addresses:
        return email_addresses[0]

    return None


def _is_uuid(value: str) -> bool:
    """Check if a string is a valid UUID."""
    try:
        UUID(value)
        return True
    except (ValueError, AttributeError):
        return False

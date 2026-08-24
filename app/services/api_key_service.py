"""Service for managing long-lived personal access tokens (API keys)."""

from __future__ import annotations

import hashlib
import secrets
from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from core.logging_config import get_logger
from core.models.api_key import API_KEY_TOKEN_PREFIX, ApiKey, WILDCARD_SCOPE

logger = get_logger(__name__)

# Number of raw-token characters shown back to the user for visual identification.
_KEY_PREFIX_VISIBLE_CHARS = 12
# Default lifetime for keys created without an explicit expiry (1 year).
_DEFAULT_EXPIRY_DAYS = 365
_MAX_EXPIRY_DAYS = 3650


class ApiKeyServiceError(Exception):
    """Base class for API key service errors."""


class ApiKeyNotFoundError(ApiKeyServiceError):
    """Raised when an API key id does not belong to the requesting user."""


class ApiKeyService:
    """CRUD and authentication for personal access tokens."""

    def __init__(self, db: AsyncSession) -> None:
        self.db = db

    @staticmethod
    def _generate_token() -> str:
        return f"{API_KEY_TOKEN_PREFIX}{secrets.token_hex(24)}"

    @staticmethod
    def _hash_token(raw_token: str) -> str:
        return hashlib.sha256(raw_token.encode("utf-8")).hexdigest()

    async def create(
        self,
        user_id: object,
        name: str,
        scopes: list[str],
        expires_in_days: int | None = None,
    ) -> tuple[ApiKey, str]:
        """Create a new API key and return (record, raw_token).

        The raw token is only available from this return value; the persisted
        value is a salted-free SHA-256 hash.
        """
        raw_token = self._generate_token()
        expires_at: datetime | None = None
        if expires_in_days:
            expires_at = datetime.now(UTC) + timedelta(days=expires_in_days)
        elif expires_in_days is None:
            expires_at = datetime.now(UTC) + timedelta(days=_DEFAULT_EXPIRY_DAYS)

        api_key = ApiKey(
            user_id=user_id,
            name=name,
            key_prefix=raw_token[:_KEY_PREFIX_VISIBLE_CHARS],
            key_hash=self._hash_token(raw_token),
            scopes=",".join(scopes) if WILDCARD_SCOPE not in scopes else WILDCARD_SCOPE,
            expires_at=expires_at,
        )
        self.db.add(api_key)
        await self.db.flush()
        await self.db.refresh(api_key)
        logger.info("api_key_created", user_id=str(user_id), key_id=str(api_key.id))
        return api_key, raw_token

    async def list_for_user(self, user_id: object) -> list[ApiKey]:
        result = await self.db.execute(
            select(ApiKey)
            .where(ApiKey.user_id == user_id)
            .order_by(ApiKey.created_at.desc())
        )
        return list(result.scalars().all())

    async def get_for_user(self, user_id: object, key_id: object) -> ApiKey | None:
        result = await self.db.execute(
            select(ApiKey).where(ApiKey.id == key_id, ApiKey.user_id == user_id)
        )
        return result.scalar_one_or_none()

    async def revoke(self, user_id: object, key_id: object) -> bool:
        """Revoke a key owned by the user. Returns False if not found."""
        api_key = await self.get_for_user(user_id, key_id)
        if api_key is None:
            return False
        api_key.revoked_at = datetime.now(UTC)
        await self.db.flush()
        logger.info("api_key_revoked", user_id=str(user_id), key_id=str(key_id))
        return True

    @classmethod
    async def authenticate(cls, db: AsyncSession, raw_token: str | None) -> ApiKey | None:
        """Resolve an API key from a raw bearer token.

        Returns the active ApiKey (with its ``user`` relationship loaded) or
        ``None`` when the token is missing, malformed, unknown, revoked, or
        expired. Best-effort updates ``last_used_at`` without raising.
        """
        if not raw_token or not raw_token.startswith(API_KEY_TOKEN_PREFIX):
            return None

        key_hash = cls._hash_token(raw_token)
        result = await db.execute(select(ApiKey).where(ApiKey.key_hash == key_hash))
        api_key = result.scalar_one_or_none()
        if api_key is None or not api_key.is_active:
            return None

        try:
            api_key.last_used_at = datetime.now(UTC)
            await db.flush()
        except Exception:  # pragma: no cover - metrics-only field
            logger.warning("api_key_last_used_update_failed", key_id=str(api_key.id))

        return api_key

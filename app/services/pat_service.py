"""Personal Access Token service for machine-first authentication."""

import hashlib
import secrets
from datetime import UTC, datetime, timedelta
from uuid import UUID

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.schemas.personal_access_token import PersonalAccessTokenScope
from core.models.personal_access_token import PersonalAccessToken

PREFIX = "vpat_"
TOKEN_BYTES = 32


class PATService:
    """Manage Personal Access Tokens for machine/agents."""

    def __init__(self, db: AsyncSession) -> None:
        self.db = db

    @staticmethod
    def generate_token() -> tuple[str, str]:
        plain = PREFIX + secrets.token_urlsafe(TOKEN_BYTES)
        hashed = hashlib.sha256(plain.encode()).hexdigest()
        return plain, hashed

    @staticmethod
    def hash_token(plain_token: str) -> str:
        return hashlib.sha256(plain_token.encode()).hexdigest()

    def _validate_scopes(self, scopes: list[str]) -> list[str]:
        valid = []
        for scope in scopes:
            if scope in PersonalAccessTokenScope.ALL_SCOPES:
                valid.append(scope)
        if not valid:
            valid = [PersonalAccessTokenScope.READ_DOWNLOADS]
        return valid

    async def create_token(
        self,
        user_id: UUID,
        name: str,
        scopes: list[str],
        expires_in_days: int | None = None,
    ) -> tuple[PersonalAccessToken, str]:
        valid_scopes = self._validate_scopes(scopes)
        plain_token, hashed = self.generate_token()

        expires_at = None
        if expires_in_days is not None:
            expires_at = datetime.now(UTC) + timedelta(days=expires_in_days)

        pat = PersonalAccessToken(
            user_id=user_id,
            name=name,
            hashed_token=hashed,
            scopes=",".join(valid_scopes),
            expires_at=expires_at,
        )
        self.db.add(pat)
        await self.db.flush()
        return pat, plain_token

    async def list_tokens(self, user_id: UUID) -> list[PersonalAccessToken]:
        result = await self.db.execute(
            select(PersonalAccessToken)
            .where(PersonalAccessToken.user_id == user_id)
            .order_by(PersonalAccessToken.created_at.desc())
        )
        return list(result.scalars().all())

    async def revoke_token(self, user_id: UUID, token_id: UUID) -> bool:
        result = await self.db.execute(
            update(PersonalAccessToken)
            .where(
                PersonalAccessToken.id == token_id,
                PersonalAccessToken.user_id == user_id,
                PersonalAccessToken.revoked_at.is_(None),
            )
            .values(revoked_at=datetime.now(UTC), is_active=False)
        )
        return result.rowcount > 0

    async def authenticate(self, plain_token: str) -> PersonalAccessToken | None:
        if not plain_token.startswith(PREFIX):
            return None

        hashed = self.hash_token(plain_token)
        result = await self.db.execute(
            select(PersonalAccessToken).where(
                PersonalAccessToken.hashed_token == hashed,
                PersonalAccessToken.is_active == True,  # noqa: E712
            )
        )
        pat = result.scalar_one_or_none()

        if pat is None:
            return None

        if pat.expires_at is not None and pat.expires_at < datetime.now(UTC):
            return None

        if pat.revoked_at is not None:
            return None

        return pat

    async def record_usage(self, pat_id: UUID) -> None:
        await self.db.execute(
            update(PersonalAccessToken)
            .where(PersonalAccessToken.id == pat_id)
            .values(last_used_at=datetime.now(UTC))
        )

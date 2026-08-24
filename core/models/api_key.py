from __future__ import annotations

import uuid
from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import DateTime, ForeignKey, String, Index
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.sql import func

from core.models.base import Base

if TYPE_CHECKING:
    from core.models.user import User

# Wildcard scope granted to JWT-authenticated sessions and to keys created
# with the all-access scope. Always checked before any concrete scope.
WILDCARD_SCOPE = "*"

# Hard prefix for raw personal access tokens. Endpoints use this to distinguish
# a PAT from a JWT without attempting a signature decode.
API_KEY_TOKEN_PREFIX = "vlj_pat_"


class ApiKey(Base):
    """Long-lived, revocable, scoped personal access token (machine auth).

    The raw token is only returned once at creation time. We persist a SHA-256
    hash of it (so a DB leak does not expose usable credentials) plus a short
    human-readable prefix shown in listings. ``scopes`` is a comma-separated
    list for SQLite/PostgreSQL portability (no ARRAY type dependency).
    """

    __tablename__ = "api_keys"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
    )
    user: Mapped[User] = relationship("User", back_populates="api_keys")

    name: Mapped[str] = mapped_column(String(64), nullable=False)
    key_prefix: Mapped[str] = mapped_column(String(20), nullable=False, index=True)
    key_hash: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    scopes: Mapped[str] = mapped_column(String(512), nullable=False, default="")

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    __table_args__ = (
        Index("ix_api_keys_user_id", "user_id"),
        Index("ix_api_keys_revoked_at", "revoked_at"),
    )

    @property
    def is_revoked(self) -> bool:
        return self.revoked_at is not None

    @property
    def is_expired(self) -> bool:
        if self.expires_at is None:
            return False
        return self.expires_at <= datetime.now(self.expires_at.tzinfo)

    @property
    def is_active(self) -> bool:
        return not self.is_revoked and not self.is_expired

    @property
    def scopes_list(self) -> list[str]:
        """Return the normalized, non-empty scope list for this key."""
        if not self.scopes:
            return []
        return [scope.strip() for scope in self.scopes.split(",") if scope.strip()]

    @property
    def grants_full_access(self) -> bool:
        return WILDCARD_SCOPE in self.scopes_list

    def has_scope(self, required: str) -> bool:
        """Check whether this key satisfies a concrete required scope."""
        if self.grants_full_access:
            return True
        return required in self.scopes_list

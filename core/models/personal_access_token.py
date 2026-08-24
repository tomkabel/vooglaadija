from __future__ import annotations

import datetime
import uuid
from typing import TYPE_CHECKING

from sqlalchemy import Boolean, DateTime, ForeignKey, Index, String, Text, text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.sql import func

from core.models.base import Base

if TYPE_CHECKING:
    from core.models.user import User


class PersonalAccessToken(Base):
    __tablename__ = "personal_access_tokens"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
    )
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    hashed_token: Mapped[str] = mapped_column(String(255), nullable=False, unique=True)
    scopes: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("'read:downloads'"))
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    last_used_at: Mapped[datetime.datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    expires_at: Mapped[datetime.datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    revoked_at: Mapped[datetime.datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    user: Mapped[User] = relationship("User", back_populates="personal_access_tokens")

    __table_args__ = (
        Index("ix_personal_access_tokens_user_id", "user_id"),
        Index("ix_personal_access_tokens_hashed_token", "hashed_token"),
    )

from __future__ import annotations

import uuid
from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import DateTime, ForeignKey, Index, String, Text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.sql import func

from core.models.base import Base

if TYPE_CHECKING:
    from core.models.download_job import DownloadJob


_PENDING_STATUS = "pending"


class Outbox(Base):
    """Transactional outbox for reliable job enqueueing.

    Lifecycle:

    - ``pending``   - written in the same DB transaction as the entity change
                      it represents. The relay picks it up and pushes the
                      corresponding Redis message.
    - ``processed`` - the relay successfully delivered the message. ``processed_at``
                      is set; the row is retained for observability and audit,
                      then reaped by ``cleanup_stale_outbox_entries`` after the
                      retention window expires.
    - ``failed``    - the relay could not deliver the message after exhausting
                      retries. The row is retained so operators can inspect.

    The partial unique index ``uq_outbox_pending_job_id`` ensures that at most
    one *pending* row exists per ``job_id`` at any time. This prevents the
    duplicate-processing window that the previous SELECT-then-INSERT
    idempotency check had under concurrent writers.
    """

    __tablename__ = "outbox"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    job_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("download_jobs.id"), nullable=False, index=True,
    )
    event_type: Mapped[str] = mapped_column(String(50), nullable=False)
    payload: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[str] = mapped_column(String(20), default=_PENDING_STATUS, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    processed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    job: Mapped[DownloadJob] = relationship("DownloadJob", back_populates="outbox_entries")

    __table_args__ = (
        Index("ix_outbox_status_created_at", "status", "created_at"),
        Index(
            "uq_outbox_pending_job_id",
            "job_id",
            unique=True,
            postgresql_where=(status == _PENDING_STATUS),
            sqlite_where=(status == _PENDING_STATUS),
        ),
    )

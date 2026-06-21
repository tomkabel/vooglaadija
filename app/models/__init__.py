"""Compatibility shim for database models.

Model definitions are owned by core.models.
"""

from core.models import DownloadJob, FailedJob, Outbox, User, not_deleted

__all__ = ["DownloadJob", "FailedJob", "Outbox", "User", "not_deleted"]

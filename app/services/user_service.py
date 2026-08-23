"""Shared user-domain service for REST and Web route layers."""

import os
import uuid
from dataclasses import dataclass

from sqlalchemy import delete, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from structlog.stdlib import BoundLogger

from app.services.auth_service import hash_password, verify_password
from app.utils.username import default_username_from_email
from app.utils.validators import validate_password
from core.config import settings
from core.logging_config import get_logger
from core.models.download_job import DownloadJob
from core.models.outbox import Outbox
from core.models.user import User, not_deleted
from core.utils.security import validate_path

logger = get_logger(__name__)


@dataclass(frozen=True)
class DeletedAccountResult:
    """Summary of account records removed by the service."""

    deleted_jobs: int


class UserServiceError(Exception):
    """Base class for user service domain errors."""


class DuplicateEmailError(UserServiceError):
    """Raised when an active user already owns an email address."""

    def __init__(self, message: str = "Email already registered") -> None:
        super().__init__(message)


class InvalidCurrentPasswordError(UserServiceError):
    """Raised when the current account password does not match."""

    def __init__(self, message: str = "Current password is incorrect") -> None:
        super().__init__(message)


class PasswordMismatchError(UserServiceError):
    """Raised when password confirmation does not match."""

    def __init__(self, message: str = "Passwords do not match") -> None:
        super().__init__(message)


class InvalidPasswordError(UserServiceError):
    """Raised when a new password fails project validation rules."""

    def __init__(self, message: str, code: str) -> None:
        self.code = code
        super().__init__(message)


class InvalidUsernameError(UserServiceError):
    """Raised when a submitted username fails account rules."""

    def __init__(self, message: str = "Username must be at least 3 characters") -> None:
        super().__init__(message)


class DeleteConfirmationError(UserServiceError):
    """Raised when account deletion confirmation text is missing or incorrect."""

    def __init__(self, message: str = "Please type DELETE to confirm account deletion") -> None:
        super().__init__(message)


class AccountFileCleanupError(UserServiceError):
    """Raised when account deletion cannot clean all download files."""

    def __init__(self, failed_paths: list[str]) -> None:
        self.failed_paths = failed_paths
        super().__init__("Could not remove all downloaded files")


class UserNotAvailableError(UserServiceError):
    """Raised when an account operation needs a current user and none is available."""

    def __init__(self, message: str = "User not found or inactive") -> None:
        super().__init__(message)


def _downloads_base_path() -> str:
    """Return the base path for stored downloads."""
    return os.path.join(settings.storage_path, "downloads")


def _cleanup_job_files(
    jobs: list[DownloadJob],
    service_logger: BoundLogger = logger,
) -> tuple[bool, list[str]]:
    """
    Clean the files associated with download jobs before their database records are removed.

    Parameters:
        jobs (list[DownloadJob]): Download jobs whose associated files should be removed.
        service_logger (BoundLogger): Logger used to record file-cleanup failures.

    Returns:
        tuple[bool, list[str]]: A success flag and the paths of files that could not be cleaned up.
    """
    file_cleanup_failures: list[str] = []
    for job in jobs:
        if not job.file_path:
            continue
        try:
            safe_path = validate_path(_downloads_base_path(), job.file_path)
            if os.path.isfile(safe_path):
                os.remove(safe_path)
        except (ValueError, PermissionError):
            service_logger.warning(
                "account_delete_file_cleanup_invalid_path",
                job_id=str(job.id),
                file_path=job.file_path,
            )
            file_cleanup_failures.append(job.file_path)
        except OSError as exc:
            service_logger.warning(
                "account_delete_file_cleanup_remove_failed",
                job_id=str(job.id),
                file_path=job.file_path,
                error=str(exc),
            )
            file_cleanup_failures.append(job.file_path)
        except Exception:
            service_logger.exception(
                "account_delete_file_cleanup_unexpected",
                job_id=str(job.id),
                file_path=job.file_path,
            )
            file_cleanup_failures.append(job.file_path)
    return (not file_cleanup_failures, file_cleanup_failures)


class UserService:
    """User business logic shared by API and Web routes."""

    def __init__(self, db: AsyncSession, user: User | None = None) -> None:
        self.db = db
        self.user = user

    async def register(self, email: str, password: str) -> User:
        """Create an active user account with project-standard defaults."""
        self._validate_new_password(password)
        result = await self.db.execute(select(User).where(User.email == email, not_deleted()))
        if result.scalar_one_or_none() is not None:
            raise DuplicateEmailError

        user = User(
            id=uuid.uuid4(),
            username=default_username_from_email(email),
            email=email,
            password_hash=await hash_password(password),
        )
        self.db.add(user)
        try:
            await self.db.commit()
        except IntegrityError as exc:
            await self.db.rollback()
            raise DuplicateEmailError from exc
        except Exception:
            await self.db.rollback()
            raise
        await self.db.refresh(user)
        return user

    async def change_password(
        self,
        current_password: str,
        new_password: str,
        new_password_confirm: str | None = None,
    ) -> User:
        """Change the current user's password and invalidate existing authentication tokens.

        Parameters:
                current_password (str): The user's existing password.
                new_password (str): The replacement password.
                new_password_confirm (str | None): Optional confirmation of the replacement password.

        Returns:
                User: The updated user.
        """
        user = self._current_user()
        if not await verify_password(current_password, user.password_hash):
            raise InvalidCurrentPasswordError
        if new_password_confirm is not None and new_password != new_password_confirm:
            raise PasswordMismatchError("New passwords do not match")
        self._validate_new_password(new_password)

        user.password_hash = await hash_password(new_password)
        user.token_version += 1
        try:
            await self.db.commit()
        except Exception:
            await self.db.rollback()
            raise
        await self.db.refresh(user)
        return user

    async def update_username(self, username: str) -> User:
        """Update the current user's display name."""
        user = self._current_user()
        clean_username = username.strip()
        if len(clean_username) < 3:
            raise InvalidUsernameError

        user.username = clean_username
        try:
            await self.db.commit()
        except Exception:
            await self.db.rollback()
            raise
        await self.db.refresh(user)
        return user

    async def delete_account(
        self,
        password: str,
        confirm_text: str | None = None,
    ) -> DeletedAccountResult:
        """
        Delete the current user account after validating the password and removing associated job files.

        Parameters:
                password (str): The current account password.
                confirm_text (str | None): Optional confirmation text, which must be `DELETE` when provided.

        Returns:
                DeletedAccountResult: The number of download jobs deleted.

        Raises:
                DeleteConfirmationError: If the confirmation text is provided and is not `DELETE`.
                InvalidCurrentPasswordError: If the password is incorrect.
                AccountFileCleanupError: If any associated job file cannot be removed.
        """
        user = self._current_user()
        if confirm_text is not None and confirm_text.strip().upper() != "DELETE":
            raise DeleteConfirmationError
        if not await verify_password(password, user.password_hash):
            raise InvalidCurrentPasswordError("Password is incorrect")

        result = await self.db.execute(select(DownloadJob).where(DownloadJob.user_id == user.id))
        jobs = list(result.scalars().all())
        all_cleaned, failed_paths = _cleanup_job_files(jobs, logger)
        if not all_cleaned:
            raise AccountFileCleanupError(failed_paths)

        try:
            job_ids = [job.id for job in jobs]
            if job_ids:
                # Outbox rows reference download_jobs with a plain FK (no
                # cascade); pending rows would either orphan or fail the
                # delete. Remove them explicitly (FailedJob cascades via
                # user_id ON DELETE CASCADE).
                await self.db.execute(delete(Outbox).where(Outbox.job_id.in_(job_ids)))
            for job in jobs:
                await self.db.delete(job)
            await self.db.delete(user)
            await self.db.commit()
        except Exception:
            await self.db.rollback()
            raise
        return DeletedAccountResult(deleted_jobs=len(jobs))

    def _current_user(self) -> User:
        """
        Retrieve the current user when the account is available.

        Returns:
                User: The active, non-deleted current user.

        Raises:
                UserNotAvailableError: If no current user exists, or the user is inactive or deleted.
        """
        if self.user is None or not self.user.is_active or self.user.deleted_at is not None:
            raise UserNotAvailableError
        return self.user

    @staticmethod
    def _validate_new_password(password: str) -> None:
        pw_error = validate_password(password)
        if pw_error:
            error_code = "password_too_short" if len(password) < 8 else "password_too_long"
            raise InvalidPasswordError(pw_error, error_code)

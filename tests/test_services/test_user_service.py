"""Direct UserService behavior tests."""

import uuid
from unittest.mock import patch

import pytest
from sqlalchemy import func, select

from app.services.auth_service import hash_password, verify_password
from app.services.user_service import (
    AccountFileCleanupError,
    DuplicateEmailError,
    InvalidCurrentPasswordError,
    InvalidPasswordError,
    InvalidUsernameError,
    PasswordMismatchError,
    UserService,
)
from core.models.download_job import DownloadJob
from core.models.user import User


async def _user(db_session, email: str = "user-service@example.com") -> User:
    user = User(
        id=uuid.uuid4(),
        email=email,
        username="initial",
        password_hash=await hash_password("securepassword123"),
    )
    db_session.add(user)
    await db_session.commit()
    return user


def _job(user_id: uuid.UUID, **overrides) -> DownloadJob:
    values = {
        "id": uuid.uuid4(),
        "user_id": user_id,
        "url": "https://www.youtube.com/watch?v=dQw4w9WgXcQ",
        "status": "completed",
    }
    values.update(overrides)
    return DownloadJob(**values)


@pytest.mark.asyncio
async def test_register_creates_user_with_default_username_and_hashed_password(db_session):
    """Registration creates a user with default username and async-hashed password."""
    user = await UserService(db_session).register("alice@example.com", "securepassword123")

    assert user.email == "alice@example.com"
    assert user.username == "alice"
    assert user.password_hash != "securepassword123"
    assert await verify_password("securepassword123", user.password_hash) is True


@pytest.mark.asyncio
async def test_register_duplicate_email_rolls_back_and_preserves_single_user(db_session):
    """Duplicate registration raises a domain error and leaves one stored user."""
    await UserService(db_session).register("duplicate@example.com", "securepassword123")

    with pytest.raises(DuplicateEmailError):
        await UserService(db_session).register("duplicate@example.com", "securepassword123")

    count_result = await db_session.execute(
        select(func.count()).where(User.email == "duplicate@example.com")
    )
    assert count_result.scalar_one() == 1


@pytest.mark.asyncio
async def test_change_password_updates_hash_and_increments_token_version(db_session):
    """Password changes update the hash and increment token_version for token revocation."""
    user = await _user(db_session)
    original_hash = user.password_hash
    original_version = user.token_version

    changed = await UserService(db_session, user=user).change_password(
        "securepassword123",
        "newpassword123",
        "newpassword123",
    )

    assert changed.password_hash != original_hash
    assert await verify_password("newpassword123", changed.password_hash) is True
    assert changed.token_version == original_version + 1


@pytest.mark.asyncio
async def test_change_password_rejects_wrong_current_password(db_session):
    """Password changes reject an incorrect current password without mutating the hash."""
    user = await _user(db_session)
    original_hash = user.password_hash

    with pytest.raises(InvalidCurrentPasswordError):
        await UserService(db_session, user=user).change_password(
            "wrongpassword",
            "newpassword123",
            "newpassword123",
        )

    assert user.password_hash == original_hash


@pytest.mark.asyncio
async def test_change_password_rejects_confirmation_mismatch(db_session):
    """Password changes reject mismatched new-password confirmation."""
    user = await _user(db_session)

    with pytest.raises(PasswordMismatchError):
        await UserService(db_session, user=user).change_password(
            "securepassword123",
            "newpassword123",
            "differentpassword123",
        )


@pytest.mark.asyncio
async def test_change_password_rejects_invalid_new_password(db_session):
    """Password changes preserve validator errors for invalid new passwords."""
    user = await _user(db_session)

    with pytest.raises(InvalidPasswordError) as exc_info:
        await UserService(db_session, user=user).change_password(
            "securepassword123",
            "short",
            "short",
        )

    assert exc_info.value.code == "password_too_short"


@pytest.mark.asyncio
async def test_update_username_trims_and_persists_name(db_session):
    """Username updates trim whitespace and persist the cleaned value."""
    user = await _user(db_session)

    updated = await UserService(db_session, user=user).update_username("  newname  ")

    assert updated.username == "newname"


@pytest.mark.asyncio
async def test_update_username_rejects_too_short_name(db_session):
    """Username updates reject cleaned names shorter than three characters."""
    user = await _user(db_session)

    with pytest.raises(InvalidUsernameError):
        await UserService(db_session, user=user).update_username(" ab ")


@pytest.mark.asyncio
async def test_delete_account_removes_jobs_user_and_download_file(db_session, tmp_path):
    """Account deletion removes files, jobs, and the user after password confirmation."""
    user = await _user(db_session)
    downloads_dir = tmp_path / "downloads"
    downloads_dir.mkdir()
    file_path = downloads_dir / "owned.mp3"
    file_path.write_text("download")
    job = _job(user.id, file_path=str(file_path))
    db_session.add(job)
    await db_session.commit()

    with patch("app.services.user_service.settings") as mock_settings:
        mock_settings.storage_path = str(tmp_path)
        result = await UserService(db_session, user=user).delete_account(
            "securepassword123",
            "DELETE",
        )

    assert result.deleted_jobs == 1
    assert file_path.exists() is False
    assert await db_session.get(User, user.id) is None
    assert await db_session.get(DownloadJob, job.id) is None


@pytest.mark.asyncio
async def test_delete_account_cleanup_failure_preserves_user_and_jobs(db_session, tmp_path):
    """Account deletion aborts before database deletion when file cleanup fails."""
    user = await _user(db_session)
    job = _job(user.id, file_path=str(tmp_path / ".." / "etc" / "passwd"))
    db_session.add(job)
    await db_session.commit()

    with patch("app.services.user_service.settings") as mock_settings:
        mock_settings.storage_path = str(tmp_path)
        with pytest.raises(AccountFileCleanupError):
            await UserService(db_session, user=user).delete_account("securepassword123", "DELETE")

    assert await db_session.get(User, user.id) is not None
    assert await db_session.get(DownloadJob, job.id) is not None


@pytest.mark.asyncio
async def test_delete_account_wrong_password_preserves_user_and_jobs(db_session):
    """Account deletion rejects a bad password before deleting user data."""
    user = await _user(db_session)
    job = _job(user.id)
    db_session.add(job)
    await db_session.commit()

    with pytest.raises(InvalidCurrentPasswordError):
        await UserService(db_session, user=user).delete_account("wrongpassword", "DELETE")

    assert await db_session.get(User, user.id) is not None
    assert await db_session.get(DownloadJob, job.id) is not None

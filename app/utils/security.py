"""Security utilities for path validation and sanitization."""

import os

from fastapi import HTTPException, status

from app.config import settings


def get_downloads_dir() -> str:
    """Get the resolved downloads directory path."""
    return os.path.realpath(os.path.join(settings.storage_path, "downloads"))


def validate_file_path(file_path: str) -> str:
    """Validate that file_path resolves within the downloads directory.

    Returns the resolved path if valid.
    Raises HTTPException(403) if path is outside the allowed directory.
    """
    resolved = os.path.realpath(file_path)
    safe_dir = get_downloads_dir()
    if not safe_dir.endswith(os.sep):
        safe_dir += os.sep
    if not resolved.startswith(safe_dir):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Access denied: invalid file path",
        )
    return resolved


def validate_path_within(base_path: str, target_path: str) -> str:
    """Validate that target_path resolves within base_path.

    Returns the resolved path if valid.
    Raises ValueError if the path escapes the base directory.
    """
    resolved_base = os.path.realpath(base_path)
    resolved_target = os.path.realpath(target_path)
    if not resolved_base.endswith(os.sep):
        resolved_base += os.sep
    if not resolved_target.startswith(resolved_base):
        raise ValueError(
            f"Path traversal detected: resolved path {resolved_target} "
            f"is outside allowed directory {resolved_base}"
        )
    return resolved_target

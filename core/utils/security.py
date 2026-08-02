"""Framework-neutral security utilities."""

import os


def validate_path(base_path: str, target_path: str, check_writable: bool = False) -> str:
    """
    Validate that a target path resolves within an allowed base directory.
    
    Parameters:
        base_path (str): The allowed base directory.
        target_path (str): The path to validate.
        check_writable (bool): Whether to require the target or its parent directory to be writable.
    
    Returns:
        str: The canonical resolved target path.
    
    Raises:
        ValueError: If the resolved target is outside the resolved base directory.
        PermissionError: If writability is required and the target location is not writable.
    """
    resolved_base = os.path.realpath(base_path)
    resolved_target = os.path.realpath(target_path)

    try:
        contained = os.path.commonpath([resolved_base, resolved_target]) == resolved_base
    except ValueError as exc:
        raise ValueError(
            f"Path traversal detected: resolved path {resolved_target} "
            f"is outside allowed directory {resolved_base}",
        ) from exc

    if not contained:
        raise ValueError(
            f"Path traversal detected: resolved path {resolved_target} "
            f"is outside allowed directory {resolved_base}",
        )

    if check_writable:
        writable_path = (
            resolved_target if os.path.exists(resolved_target) else os.path.dirname(resolved_target)
        )
        if not os.access(writable_path, os.W_OK):
            raise PermissionError(f"Path is not writable: {writable_path}")

    return resolved_target

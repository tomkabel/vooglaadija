"""Regression tests for Story 2.3 path validation consolidation."""

from __future__ import annotations

import ast
import os
from pathlib import Path
from unittest.mock import patch

import pytest

pytestmark = pytest.mark.slow


PROJECT_ROOT = Path(__file__).resolve().parents[1]
ACTIVE_SOURCE_ROOTS = ("app", "core", "worker")


def _iter_python_files(*roots: str) -> list[Path]:
    """Return active first-party Python files under the requested roots."""
    return [
        path
        for root in roots
        if (root_path := PROJECT_ROOT / root).exists()
        for path in root_path.rglob("*.py")
        if "__pycache__" not in path.parts
    ]


def test_only_core_security_defines_validate_path() -> None:
    """The active source tree defines exactly one canonical validate_path function."""
    definitions: list[Path] = []

    for path in _iter_python_files(*ACTIVE_SOURCE_ROOTS):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.FunctionDef) and node.name == "validate_path":
                definitions.append(path.relative_to(PROJECT_ROOT))

    assert definitions == [Path("core/utils/security.py")]


def test_obsolete_path_validators_are_not_defined_or_imported() -> None:
    """The removed validator names are absent from active source definitions and imports."""
    obsolete_names = {
        "validate" + "_file_path",
        "validate" + "_path_within",
        "_validate" + "_path_within",
    }
    matches: list[str] = []

    for path in _iter_python_files(*ACTIVE_SOURCE_ROOTS):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        relative_path = path.relative_to(PROJECT_ROOT)
        for node in ast.walk(tree):
            if isinstance(node, ast.FunctionDef) and node.name in obsolete_names:
                matches.append(f"{relative_path}:{node.lineno}: def {node.name}")
            elif isinstance(node, ast.ImportFrom):
                imported_names = {alias.name for alias in node.names}
                if imported_names & obsolete_names:
                    matches.append(
                        f"{relative_path}:{node.lineno}: from {node.module} import "
                        f"{', '.join(sorted(imported_names & obsolete_names))}"
                    )
                if node.module == "app.utils.security":
                    matches.append(f"{relative_path}:{node.lineno}: from app.utils.security")
            elif isinstance(node, ast.Import):
                for alias in node.names:
                    if alias.name == "app.utils.security":
                        matches.append(f"{relative_path}:{node.lineno}: import app.utils.security")

    assert matches == []


def test_validate_path_returns_resolved_target_for_valid_containment(tmp_path: Path) -> None:
    """A contained target path returns its canonical resolved filesystem path."""
    from core.utils.security import validate_path

    downloads_dir = tmp_path / "downloads"
    downloads_dir.mkdir()
    target = downloads_dir / "file.mp4"
    target.write_text("media")

    assert validate_path(str(downloads_dir), str(target)) == os.path.realpath(target)


def test_validate_path_rejects_parent_directory_escape(tmp_path: Path) -> None:
    """A target using parent-directory traversal outside the base raises ValueError."""
    from core.utils.security import validate_path

    downloads_dir = tmp_path / "downloads"
    downloads_dir.mkdir()
    target = downloads_dir / ".." / "escape.mp4"

    with pytest.raises(ValueError, match="Path traversal detected"):
        validate_path(str(downloads_dir), str(target))


def test_validate_path_rejects_absolute_path_outside_base(tmp_path: Path) -> None:
    """An absolute target outside the base directory raises ValueError."""
    from core.utils.security import validate_path

    downloads_dir = tmp_path / "downloads"
    outside_dir = tmp_path / "outside"
    downloads_dir.mkdir()
    outside_dir.mkdir()

    with pytest.raises(ValueError, match="Path traversal detected"):
        validate_path(str(downloads_dir), str(outside_dir / "file.mp4"))


def test_validate_path_rejects_sibling_prefix_directory(tmp_path: Path) -> None:
    """A sibling directory sharing the base path prefix is not treated as contained."""
    from core.utils.security import validate_path

    downloads_dir = tmp_path / "downloads"
    evil_dir = tmp_path / "downloads_evil"
    downloads_dir.mkdir()
    evil_dir.mkdir()

    with pytest.raises(ValueError, match="Path traversal detected"):
        validate_path(str(downloads_dir), str(evil_dir / "file.mp4"))


def test_validate_path_rejects_symlink_escape(tmp_path: Path) -> None:
    """A symlink inside the base that resolves outside the base raises ValueError."""
    from core.utils.security import validate_path

    downloads_dir = tmp_path / "downloads"
    outside_dir = tmp_path / "outside"
    downloads_dir.mkdir()
    outside_dir.mkdir()
    symlink_path = downloads_dir / "link"
    try:
        symlink_path.symlink_to(outside_dir, target_is_directory=True)
    except OSError as exc:
        pytest.skip(f"Symlink creation is unavailable on this platform: {exc}")

    with pytest.raises(ValueError, match="Path traversal detected"):
        validate_path(str(downloads_dir), str(symlink_path / "file.mp4"))


def test_validate_path_check_writable_rejects_unwritable_parent(tmp_path: Path) -> None:
    """check_writable=True raises PermissionError when the target parent is not writable."""
    from core.utils.security import validate_path

    downloads_dir = tmp_path / "downloads"
    downloads_dir.mkdir()

    with (
        patch("core.utils.security.os.path.exists", return_value=False),
        patch("core.utils.security.os.access", return_value=False),
    ):
        with pytest.raises(PermissionError, match="not writable"):
            validate_path(str(downloads_dir), str(downloads_dir / "file.mp4"), check_writable=True)

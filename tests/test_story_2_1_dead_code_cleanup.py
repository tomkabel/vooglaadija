"""Regression tests for Story 2.1 dead-code cleanup."""

import ast
import importlib
import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SOURCE_ROOTS = ("app", "core", "worker", "tests", "scripts", "alembic")
THIS_FILE = Path(__file__).name


def _iter_text_files(*roots: str) -> list[Path]:
    return [
        path
        for root in roots
        if (root_path := PROJECT_ROOT / root).exists()
        for path in root_path.rglob("*")
        if path.is_file() and "__pycache__" not in path.parts
    ]


def _iter_python_files(*roots: str) -> list[Path]:
    return [path for path in _iter_text_files(*roots) if path.suffix == ".py"]


def test_dead_code_files_and_ignored_src_artifact_are_absent():
    """The tracked dead-code files and ignored src artifact directory stay deleted."""
    removed_paths = [
        "app/services/retry_service.py",
        "app/templates/partials/_error.html",
        "app/templates/partials/_status_badge.html",
        "tests/test_services/test_retry_service.py",
        "src",
    ]

    existing_paths = [path for path in removed_paths if (PROJECT_ROOT / path).exists()]

    assert existing_paths == []


@pytest.mark.parametrize(
    "module_name",
    [
        ".".join(("app", "services", "retry_service")),
    ],
)
def test_deleted_service_modules_are_not_importable(module_name):
    """Deleted service modules are not importable through legacy module paths."""
    sys.modules.pop(module_name, None)

    with pytest.raises(ModuleNotFoundError):
        importlib.import_module(module_name)


def test_deleted_symbols_and_template_partials_have_no_active_references():
    """Active source and tests no longer reference deleted modules, classes, or partials."""
    retry_module_name = ".".join(("app", "services", "retry_service"))
    deleted_symbols = [
        retry_module_name,
        "retry" + "_service",
        "YTDLP" + "Error",
        "_error" + ".html",
        "_status" + "_badge.html",
    ]
    matches: list[str] = []

    for path in _iter_text_files(*SOURCE_ROOTS):
        if path.name == THIS_FILE:
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            continue
        for symbol in deleted_symbols:
            if symbol in text:
                matches.append(f"{path.relative_to(PROJECT_ROOT)}: {symbol}")

    assert matches == []


def test_template_tree_does_not_include_removed_partials():
    """Active templates do not include the removed error or status-badge partials."""
    removed_partials = {"partials/_error.html", "partials/_status_badge.html"}
    matches: list[str] = []

    for path in _iter_text_files("app/templates"):
        text = path.read_text(encoding="utf-8")
        for partial in removed_partials:
            if partial in text:
                matches.append(f"{path.relative_to(PROJECT_ROOT)}: {partial}")

    assert matches == []


def test_storage_error_has_one_canonical_source_definition():
    """StorageError is defined only by app.utils.exceptions."""
    class_definitions: list[Path] = []

    for path in _iter_python_files("app", "core", "worker"):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.ClassDef) and node.name == "StorageError":
                class_definitions.append(path.relative_to(PROJECT_ROOT))

    assert class_definitions == [Path("app/utils/exceptions.py")]


def test_yt_dlp_service_uses_canonical_storage_error():
    """yt_dlp_service exposes and raises the canonical StorageError class."""
    from app.services import yt_dlp_service
    from app.utils.exceptions import StorageError

    assert yt_dlp_service.StorageError is StorageError

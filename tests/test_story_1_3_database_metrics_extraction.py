"""Regression tests for Story 1.3 database and metrics extraction."""

import ast
import importlib
import sys
from pathlib import Path
from typing import get_args
from unittest.mock import patch

import pytest

_SCAN_ROOTS = ("app", "worker", "tests", "alembic", "scripts", "core")
_DATABASE_SHIM_MODULE = ".".join(("app", "database"))
_METRICS_SHIM_MODULE = ".".join(("app", "metrics"))
_EXPECTED_ALEMBIC_VERSION_FILES = {
    "001_initial.py",
    "002_add_title_to_download_jobs.py",
    "003_add_error_category_and_failed_jobs.py",
    "004_add_token_version_to_users.py",
}
_LEGACY_MODULES = {_DATABASE_SHIM_MODULE, _METRICS_SHIM_MODULE}


def _iter_python_files() -> list[Path]:
    return [
        path
        for root in _SCAN_ROOTS
        if (root_path := Path(root)).exists()
        for path in root_path.rglob("*.py")
    ]


def _legacy_import_references(path: Path) -> list[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    references: list[str] = []

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name in _LEGACY_MODULES or any(
                    alias.name.startswith(f"{module}.") for module in _LEGACY_MODULES
                ):
                    references.append(f"{path}:{node.lineno}: import {alias.name}")
        elif isinstance(node, ast.ImportFrom):
            module = node.module or ""
            if module in _LEGACY_MODULES or any(
                module.startswith(f"{legacy_module}.") for legacy_module in _LEGACY_MODULES
            ):
                references.append(f"{path}:{node.lineno}: from {module} import ...")
        elif isinstance(node, ast.Attribute) and node.attr in {"database", "metrics"}:
            if isinstance(node.value, ast.Name) and node.value.id == "app":
                references.append(f"{path}:{node.lineno}: app.{node.attr}")

    return references


def test_legacy_database_and_metrics_modules_are_removed():
    """The legacy app database and metrics modules should not be importable."""
    for module_name in sorted(_LEGACY_MODULES):
        sys.modules.pop(module_name, None)

        with pytest.raises(ModuleNotFoundError):
            importlib.import_module(module_name)


def test_core_database_owns_public_database_objects():
    """The canonical core database module owns public database helpers."""
    core_database = importlib.import_module("core.database")

    public_exports = {
        "Base",
        "get_engine",
        "get_async_session_factory",
        "get_async_session",
        "get_db",
        "_EngineFactory",
    }

    assert public_exports <= set(core_database.__dict__)


def test_core_metrics_owns_public_metric_exports():
    """The canonical core metrics module owns public metric exports."""
    core_metrics = importlib.import_module("core.metrics")

    for name in core_metrics.__all__:
        assert name in core_metrics.__dict__


def test_database_factory_reads_patched_core_database_url_before_first_engine_creation():
    """The core database factory reads the patched settings URL on first engine creation."""
    import core.database as database_module
    from core.config import settings

    factory = database_module._EngineFactory()
    engine = object()
    original_database_url = settings.database_url
    patched_database_url = "sqlite+aiosqlite:///story_1_3_database_extraction.db"

    settings.database_url = patched_database_url
    try:
        with patch("core.database.create_async_engine", return_value=engine) as mock_create_engine:
            assert factory.get_engine() is engine
    finally:
        settings.database_url = original_database_url

    assert mock_create_engine.call_args.args[0] == patched_database_url


def test_fastapi_db_session_dependency_uses_core_database_callable():
    """FastAPI database dependency wiring uses the canonical core database callable."""
    from app.api.dependencies import DbSession
    from core.database import get_db

    depends = get_args(DbSession)[1]

    assert depends.dependency is get_db


def test_core_database_uses_existing_core_model_metadata():
    """The canonical database module keeps the existing core model metadata."""
    from core.database import Base
    from core.models.base import Base as CoreBase

    assert Base is CoreBase
    assert Base.metadata is CoreBase.metadata
    assert set(Base.metadata.tables) == {"download_jobs", "failed_jobs", "outbox", "users"}


def test_internal_code_has_no_legacy_database_or_metrics_imports():
    """Internal Python code references canonical database and metrics modules only."""
    matches: list[str] = []
    for path in _iter_python_files():
        matches.extend(_legacy_import_references(path))

    assert matches == []


def test_database_metrics_extraction_added_no_alembic_migration():
    """Database and metrics extraction does not introduce a database migration."""
    migration_files = {path.name for path in Path("alembic/versions").glob("*.py")}

    assert migration_files == _EXPECTED_ALEMBIC_VERSION_FILES

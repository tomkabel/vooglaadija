"""Tests for shared core model ownership and import boundaries."""

import ast
import importlib
import tomllib
from pathlib import Path

import pytest

from core.database import Base as DatabaseBase
from core.models import DownloadJob, FailedJob, Outbox, User, not_deleted
from core.models.base import Base

PROJECT_ROOT = Path(__file__).resolve().parents[2]
CORE_MODEL_FILES = {
    "__init__.py",
    "base.py",
    "download_job.py",
    "failed_job.py",
    "outbox.py",
    "user.py",
}
EXPECTED_TABLE_COLUMNS = {
    "download_jobs": {
        "id",
        "user_id",
        "url",
        "status",
        "file_path",
        "title",
        "file_name",
        "error",
        "error_category",
        "retry_count",
        "max_retries",
        "next_retry_at",
        "created_at",
        "updated_at",
        "completed_at",
        "expires_at",
    },
    "failed_jobs": {
        "id",
        "original_job_id",
        "user_id",
        "url",
        "error_category",
        "retry_history",
        "final_error",
        "final_error_category",
        "retry_count",
        "max_retries_at_failure",
        "title",
        "created_at",
        "failed_at",
        "expires_at",
    },
    "outbox": {
        "id",
        "job_id",
        "event_type",
        "payload",
        "status",
        "created_at",
        "processed_at",
    },
    "users": {
        "id",
        "username",
        "email",
        "password_hash",
        "is_active",
        "deleted_at",
        "token_version",
        "created_at",
        "updated_at",
    },
}


def test_core_models_own_model_exports():
    """Core model exports should be the canonical ORM classes."""
    assert DownloadJob.__module__ == "core.models.download_job"
    assert FailedJob.__module__ == "core.models.failed_job"
    assert Outbox.__module__ == "core.models.outbox"
    assert User.__module__ == "core.models.user"


def test_app_model_shim_reexports_core_models():
    """The temporary app.models shim should not create duplicate ORM classes."""
    app_model_shim = importlib.import_module("app.models")

    assert app_model_shim.DownloadJob is DownloadJob
    assert app_model_shim.FailedJob is FailedJob
    assert app_model_shim.Outbox is Outbox
    assert app_model_shim.User is User
    assert app_model_shim.not_deleted is not_deleted
    assert app_model_shim.__all__ == ["DownloadJob", "FailedJob", "Outbox", "User", "not_deleted"]


def test_app_database_reexports_core_base():
    """The database compatibility shim keeps the core base import-compatible."""
    assert DatabaseBase is Base
    assert set(Base.metadata.tables) == {"download_jobs", "failed_jobs", "outbox", "users"}


def test_core_models_package_contains_expected_story_files():
    """The core model package should contain the Story 1.1 model file set."""
    model_dir = PROJECT_ROOT / "core" / "models"
    actual_files = {path.name for path in model_dir.glob("*.py")}

    assert CORE_MODEL_FILES <= actual_files


def test_app_model_shim_has_no_model_class_definitions():
    """The compatibility shim should not contain duplicate ORM model classes."""
    shim_path = PROJECT_ROOT / "app" / "models" / "__init__.py"
    tree = ast.parse(shim_path.read_text())

    assert not [node.name for node in ast.walk(tree) if isinstance(node, ast.ClassDef)]


def test_core_models_share_one_declarative_metadata():
    """Every moved model should be registered on the shared core metadata."""
    for model in (DownloadJob, FailedJob, Outbox, User):
        assert model.__table__.metadata is Base.metadata


@pytest.mark.parametrize(("table_name", "expected_columns"), EXPECTED_TABLE_COLUMNS.items())
def test_core_model_table_column_contract(table_name, expected_columns):
    """Moved models should preserve their existing table column names."""
    assert set(Base.metadata.tables[table_name].columns.keys()) == expected_columns


def test_core_model_foreign_key_contract():
    """Moved models should preserve their foreign key targets."""
    download_fks = {
        foreign_key.parent.name: foreign_key.column.table.name
        for foreign_key in Base.metadata.tables["download_jobs"].foreign_keys
    }
    failed_fks = {
        foreign_key.parent.name: foreign_key.column.table.name
        for foreign_key in Base.metadata.tables["failed_jobs"].foreign_keys
    }

    assert download_fks == {"user_id": "users"}
    assert failed_fks == {"original_job_id": "download_jobs", "user_id": "users"}


def test_core_model_index_contract():
    """Moved models should preserve named indexes used by queries and migrations."""
    outbox_indexes = {index.name for index in Base.metadata.tables["outbox"].indexes}
    user_indexes = {index.name for index in Base.metadata.tables["users"].indexes}

    assert "ix_outbox_status_created_at" in outbox_indexes
    assert "ix_users_email_active" in user_indexes


def test_no_internal_imports_from_legacy_model_package():
    """Internal Python files should use core model imports after the migration."""
    old_import_patterns = (
        "from " + "app.models",
        "import " + "app.models",
        "from " + "app import models",
    )
    search_roots = ("app", "worker", "tests", "alembic")
    offenders: list[str] = []

    for root in search_roots:
        for path in (PROJECT_ROOT / root).rglob("*.py"):
            source = path.read_text()
            if any(pattern in source for pattern in old_import_patterns):
                offenders.append(str(path.relative_to(PROJECT_ROOT)))

    assert offenders == []


def test_core_package_does_not_import_api_or_worker_layers():
    """The shared core package should not depend on app or worker modules."""
    forbidden_patterns = (
        "from " + "app.",
        "import " + "app.",
        "from " + "worker.",
        "import " + "worker.",
    )
    offenders: list[str] = []

    for path in (PROJECT_ROOT / "core").rglob("*.py"):
        source = path.read_text()
        if any(pattern in source for pattern in forbidden_patterns):
            offenders.append(str(path.relative_to(PROJECT_ROOT)))

    assert offenders == []


def test_security_tooling_scans_core_package():
    """Security scan configuration should include moved core source files."""
    pyproject = tomllib.loads((PROJECT_ROOT / "pyproject.toml").read_text())
    security_scripts = pyproject["tool"]["hatch"]["envs"]["security"]["scripts"]
    bandit_targets = pyproject["tool"]["bandit"]["targets"]

    assert "core/" in security_scripts["scan-bandit"]
    assert "core/" in security_scripts["bandit-report"]
    assert "core/" in bandit_targets

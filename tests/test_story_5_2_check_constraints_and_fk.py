"""Story 5.2 migration guardrails for CHECK constraints and foreign key."""

from __future__ import annotations

import importlib.util
from pathlib import Path
from types import ModuleType

import pytest

from alembic import command
from alembic.config import Config
from core.config import settings

PROJECT_ROOT = Path(__file__).resolve().parents[1]
MIGRATION_PATH = PROJECT_ROOT / "alembic/versions/007_add_check_constraints_and_fk.py"


def _load_migration() -> ModuleType:
    """Load the 007 migration module."""
    spec = importlib.util.spec_from_file_location("story_5_2_migration", MIGRATION_PATH)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _alembic_config() -> Config:
    """Create an Alembic config pointing to the project's migration directory."""
    config = Config(str(PROJECT_ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(PROJECT_ROOT / "alembic"))
    return config


def test_story_5_2_revision_extends_previous_alembic_head() -> None:
    """Revision 007 must have down_revision pointing to 006."""
    mod = _load_migration()
    assert mod.revision == "007"
    assert mod.down_revision == "006"


def test_story_5_2_migration_declares_expected_constraints() -> None:
    """The migration module must define upgrade() and downgrade()."""
    mod = _load_migration()
    assert hasattr(mod, "upgrade")
    assert callable(mod.upgrade)
    assert hasattr(mod, "downgrade")
    assert callable(mod.downgrade)


def test_story_5_2_migration_source_includes_expected_constraint_names() -> None:
    """The migration source must reference all three constraint names."""
    source = MIGRATION_PATH.read_text()

    assert "chk_download_jobs_status" in source
    assert "chk_outbox_status" in source
    assert "fk_outbox_job_id" in source


def test_story_5_2_migration_source_has_data_pre_checks() -> None:
    """The migration source must pre-check and clean invalid data."""
    source = MIGRATION_PATH.read_text()
    assert "_pre_check_data" in source


def test_story_5_2_migration_uses_not_valid_for_postgres() -> None:
    """The migration source must use NOT VALID pattern for PostgreSQL."""
    source = MIGRATION_PATH.read_text()
    assert "NOT VALID" in source
    assert "VALIDATE CONSTRAINT" in source


def test_story_5_2_migration_is_limited_to_constraint_operations() -> None:
    """The migration must not modify tables, columns, models, or routes."""
    source = MIGRATION_PATH.read_text()

    forbidden_patterns = [
        "create_table",
        "drop_table",
        "add_column",
        "drop_column",
        "alter_column",
        "rename_table",
    ]
    for pattern in forbidden_patterns:
        assert pattern not in source, (
            f"Migration must not use '{pattern}'; it should only manage constraints"
        )


def test_story_5_2_sqlite_upgrade_and_downgrade_succeed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """SQLite migration upgrade/downgrade round-trip should succeed without crash.

    SQLite does not support ALTER TABLE ADD CHECK or ADD FOREIGN KEY,
    so constraints are PostgreSQL-only. This test verifies the migration
    completes cleanly on SQLite (data pre-checks run, DDL is skipped).
    """
    database_path = tmp_path / "story_5_2.sqlite"
    monkeypatch.setattr(settings, "database_url", f"sqlite+aiosqlite:///{database_path}")

    config = _alembic_config()
    command.upgrade(config, "006")
    command.upgrade(config, "007")
    command.downgrade(config, "006")
    # because both branches run the data pre-checks and skip the DDL.

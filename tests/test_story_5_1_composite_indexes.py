"""Story 5.1 migration guardrails for composite index hardening."""

from __future__ import annotations

import importlib.util
from pathlib import Path
from types import ModuleType

import pytest
from sqlalchemy import create_engine, inspect

from alembic import command
from alembic.config import Config
from core.config import settings

PROJECT_ROOT = Path(__file__).resolve().parents[1]
MIGRATION_PATH = PROJECT_ROOT / "alembic/versions/005_add_composite_indexes.py"

DOWNLOAD_INDEXES = {
    "ix_download_jobs_user_id_status",
    "ix_download_jobs_user_id_created_at",
    "ix_download_jobs_status_updated_at",
}
FAILED_INDEXES = {"ix_failed_jobs_user_id_failed_at"}
ALL_STORY_INDEXES = DOWNLOAD_INDEXES | FAILED_INDEXES
USER_INDEXES_TO_PRESERVE = {"ix_users_email_active", "ix_users_deleted_at"}


def _load_migration() -> ModuleType:
    spec = importlib.util.spec_from_file_location("story_5_1_migration", MIGRATION_PATH)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _alembic_config() -> Config:
    config = Config(str(PROJECT_ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(PROJECT_ROOT / "alembic"))
    return config


def _index_names(database_path: Path, table_name: str) -> set[str]:
    engine = create_engine(f"sqlite:///{database_path}")
    try:
        return {index["name"] for index in inspect(engine).get_indexes(table_name)}
    finally:
        engine.dispose()


@pytest.mark.unit
def test_story_5_1_revision_extends_previous_alembic_head() -> None:
    """The Story 5.1 migration should be the next revision after 004."""
    assert MIGRATION_PATH.exists()

    migration = _load_migration()

    assert migration.revision == "005"
    assert migration.down_revision == "004"
    assert callable(migration.upgrade)
    assert callable(migration.downgrade)


@pytest.mark.unit
def test_story_5_1_migration_declares_expected_indexes_and_columns() -> None:
    """Static guardrails keep the migration scoped to the requested indexes."""
    source = MIGRATION_PATH.read_text(encoding="utf-8")

    for index_name in ALL_STORY_INDEXES:
        assert index_name in source

    assert "ix_users_email" in source
    assert "ix_users_email_active" not in source
    assert "ix_users_deleted_at" not in source
    assert '"user_id", "status"' in source
    assert '"user_id", sa.text("created_at DESC")' in source
    assert '"status", "updated_at"' in source
    assert '"user_id", sa.text("failed_at DESC")' in source


@pytest.mark.unit
def test_story_5_1_postgresql_branch_uses_concurrent_autocommit_sql() -> None:
    """PostgreSQL index DDL must use autocommit and CONCURRENTLY."""
    source = MIGRATION_PATH.read_text(encoding="utf-8")

    assert 'dialect.name == "postgresql"' in source
    assert "autocommit_block()" in source
    assert source.count("CREATE INDEX CONCURRENTLY") == 5
    assert source.count("DROP INDEX CONCURRENTLY IF EXISTS") == 5

    for index_name in ALL_STORY_INDEXES:
        assert f"CREATE INDEX CONCURRENTLY {index_name}" in source
        assert f"DROP INDEX CONCURRENTLY IF EXISTS {index_name}" in source

    assert "DROP INDEX CONCURRENTLY IF EXISTS ix_users_email" in source
    assert "CREATE INDEX CONCURRENTLY ix_users_email ON users (email)" in source


@pytest.mark.unit
def test_story_5_1_migration_is_limited_to_index_operations() -> None:
    """The migration should not change tables, columns, constraints, or data."""
    source = MIGRATION_PATH.read_text(encoding="utf-8")
    prohibited_operations = (
        "op.add_column",
        "op.drop_column",
        "op.alter_column",
        "op.create_table",
        "op.drop_table",
        "op.create_foreign_key",
        "op.drop_constraint",
        "op.create_unique_constraint",
        "op.bulk_insert",
    )

    for operation in prohibited_operations:
        assert operation not in source


@pytest.mark.unit
def test_story_5_1_sqlite_upgrade_and_downgrade_manage_expected_indexes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """SQLite migration execution should mirror the structural index changes."""
    database_path = tmp_path / "story_5_1.sqlite"
    monkeypatch.setattr(settings, "database_url", f"sqlite+aiosqlite:///{database_path}")

    config = _alembic_config()
    command.upgrade(config, "005")

    assert DOWNLOAD_INDEXES <= _index_names(database_path, "download_jobs")
    assert FAILED_INDEXES <= _index_names(database_path, "failed_jobs")
    user_indexes_after_upgrade = _index_names(database_path, "users")
    assert "ix_users_email" not in user_indexes_after_upgrade
    assert USER_INDEXES_TO_PRESERVE <= user_indexes_after_upgrade

    command.downgrade(config, "004")

    assert DOWNLOAD_INDEXES.isdisjoint(_index_names(database_path, "download_jobs"))
    assert FAILED_INDEXES.isdisjoint(_index_names(database_path, "failed_jobs"))
    user_indexes_after_downgrade = _index_names(database_path, "users")
    assert "ix_users_email" in user_indexes_after_downgrade
    assert USER_INDEXES_TO_PRESERVE <= user_indexes_after_downgrade

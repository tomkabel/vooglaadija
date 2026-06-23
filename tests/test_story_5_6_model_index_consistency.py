"""Story 5.6 guardrails for ORM model and migration index consistency."""

from __future__ import annotations

from pathlib import Path

import pytest
from alembic.config import Config
from sqlalchemy import create_engine, inspect

from alembic import command
from core.config import settings
from core.models.base import Base

PROJECT_ROOT = Path(__file__).resolve().parents[1]
COMPOSITE_MIGRATION_PATH = PROJECT_ROOT / "alembic/versions/005_add_composite_indexes.py"
ORIGINAL_JOB_INDEX_MIGRATION_PATH = (
    PROJECT_ROOT / "alembic/versions/006_add_failed_job_original_job_id_index.py"
)

MIGRATION_ONLY_COMPOSITE_INDEXES = {
    "ix_download_jobs_user_id_status": "download_jobs",
    "ix_download_jobs_user_id_created_at": "download_jobs",
    "ix_download_jobs_status_updated_at": "download_jobs",
    "ix_failed_jobs_user_id_failed_at": "failed_jobs",
}
EXPECTED_HEAD_INDEXES = {
    "users": {
        "ix_users_deleted_at",
        "ix_users_email_active",
    },
    "download_jobs": {
        "ix_download_jobs_expires_at",
        "ix_download_jobs_status_updated_at",
        "ix_download_jobs_user_id_created_at",
        "ix_download_jobs_user_id_status",
    },
    "failed_jobs": {
        "ix_failed_jobs_error_category",
        "ix_failed_jobs_expires_at",
        "ix_failed_jobs_original_job_id",
        "ix_failed_jobs_user_id",
        "ix_failed_jobs_user_id_failed_at",
    },
    "outbox": {
        "ix_outbox_job_id",
        "ix_outbox_status",
        "ix_outbox_status_created_at",
    },
}


pytestmark = pytest.mark.unit


def _index_names(table_name: str) -> set[str]:
    return {index.name for index in Base.metadata.tables[table_name].indexes}


def _index_columns(table_name: str) -> dict[str, tuple[str, ...]]:
    return {
        index.name: tuple(column.name for column in index.columns)
        for index in Base.metadata.tables[table_name].indexes
    }


def _alembic_config() -> Config:
    config = Config(str(PROJECT_ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(PROJECT_ROOT / "alembic"))
    return config


def _database_index_names(database_path: Path, table_name: str) -> set[str]:
    engine = create_engine(f"sqlite:///{database_path}")
    try:
        return {index["name"] for index in inspect(engine).get_indexes(table_name)}
    finally:
        engine.dispose()


def test_user_metadata_matches_active_database_indexes() -> None:
    """User metadata should keep active indexes and omit the dropped email index."""
    user_indexes = _index_names("users")

    assert "ix_users_email_active" in user_indexes
    assert "ix_users_deleted_at" in user_indexes
    assert "ix_users_email" not in user_indexes


def test_failed_job_metadata_includes_single_column_database_indexes() -> None:
    """FailedJob metadata should represent every single-column migration index."""
    failed_job_indexes = _index_columns("failed_jobs")

    assert failed_job_indexes["ix_failed_jobs_original_job_id"] == ("original_job_id",)
    assert failed_job_indexes["ix_failed_jobs_user_id"] == ("user_id",)
    assert failed_job_indexes["ix_failed_jobs_error_category"] == ("error_category",)
    assert failed_job_indexes["ix_failed_jobs_expires_at"] == ("expires_at",)


def test_download_job_metadata_keeps_only_single_column_model_index() -> None:
    """DownloadJob metadata should not duplicate Story 5.1 migration-only composites."""
    download_job_indexes = _index_names("download_jobs")

    assert "ix_download_jobs_expires_at" in download_job_indexes
    assert "ix_download_jobs_user_id_status" not in download_job_indexes
    assert "ix_download_jobs_user_id_created_at" not in download_job_indexes
    assert "ix_download_jobs_status_updated_at" not in download_job_indexes


def test_outbox_metadata_matches_database_indexes() -> None:
    """Outbox metadata should represent its single-column and composite indexes."""
    outbox_indexes = _index_names("outbox")

    assert "ix_outbox_job_id" in outbox_indexes
    assert "ix_outbox_status" in outbox_indexes
    assert "ix_outbox_status_created_at" in outbox_indexes


def test_story_5_1_composites_are_documented_as_migration_only() -> None:
    """Story 5.1 composite indexes stay migration-only because DESC indexes need dialect DDL."""
    migration_source = COMPOSITE_MIGRATION_PATH.read_text(encoding="utf-8")

    for index_name, table_name in MIGRATION_ONLY_COMPOSITE_INDEXES.items():
        assert index_name in migration_source
        assert index_name not in _index_names(table_name)

    assert '"user_id", "status"' in migration_source
    assert '"user_id", sa.text("created_at DESC")' in migration_source
    assert '"status", "updated_at"' in migration_source
    assert '"user_id", sa.text("failed_at DESC")' in migration_source


def test_story_5_6_original_job_index_migration_is_focused() -> None:
    """Story 5.6 migration should only add the missing original_job_id index."""
    migration_source = ORIGINAL_JOB_INDEX_MIGRATION_PATH.read_text(encoding="utf-8")
    compact_source = " ".join(migration_source.split())

    assert 'revision: str = "006"' in migration_source
    assert 'down_revision: str | None = "005"' in migration_source
    assert "ix_failed_jobs_original_job_id" in migration_source
    assert '["original_job_id"]' in migration_source
    assert "CREATE INDEX CONCURRENTLY IF NOT EXISTS" in compact_source
    assert "DROP INDEX CONCURRENTLY IF EXISTS ix_failed_jobs_original_job_id" in compact_source

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
        assert operation not in migration_source


def test_alembic_head_index_contract_matches_model_metadata(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Alembic head should match model indexes plus documented migration-only composites."""
    database_path = tmp_path / "story_5_6.sqlite"
    monkeypatch.setattr(settings, "database_url", f"sqlite+aiosqlite:///{database_path}")

    command.upgrade(_alembic_config(), "head")

    for table_name, expected_indexes in EXPECTED_HEAD_INDEXES.items():
        assert _database_index_names(database_path, table_name) == expected_indexes

    for index_name, table_name in MIGRATION_ONLY_COMPOSITE_INDEXES.items():
        assert index_name in _database_index_names(database_path, table_name)
        assert index_name not in _index_names(table_name)

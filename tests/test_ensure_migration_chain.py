"""Tests for migration-chain validation safety."""

from __future__ import annotations

from unittest.mock import patch

import pytest

from scripts import ensure_migration_chain


@pytest.mark.unit
def test_broken_chain_fails_safe_without_override(monkeypatch, capsys):
    """A missing revision should block startup instead of stamping head silently."""
    monkeypatch.setenv("DATABASE_URL", "postgresql+asyncpg://user:pass@db/test")
    monkeypatch.delenv("ALLOW_MIGRATION_CHAIN_STAMP", raising=False)

    with (
        patch.object(
            ensure_migration_chain, "_get_available_revisions", return_value={"002", "003"}
        ),
        patch.object(ensure_migration_chain, "_get_db_revision", return_value="001"),
        patch("alembic.config.Config"),
        patch("alembic.script.ScriptDirectory.from_config") as script_dir,
        patch.object(ensure_migration_chain, "_stamp_to_head") as stamp_mock,
    ):
        script_dir.return_value.get_current_head.return_value = "003"
        exit_code = ensure_migration_chain.main()

    captured = capsys.readouterr()
    assert exit_code == 1
    assert "Refusing to stamp" in captured.err
    stamp_mock.assert_not_called()


@pytest.mark.unit
def test_broken_chain_can_be_explicitly_overridden(monkeypatch):
    """Unsafe stamping stays possible only behind an explicit operator override."""
    monkeypatch.setenv("DATABASE_URL", "postgresql+asyncpg://user:pass@db/test")
    monkeypatch.setenv("ALLOW_MIGRATION_CHAIN_STAMP", "1")

    with (
        patch.object(
            ensure_migration_chain, "_get_available_revisions", return_value={"002", "003"}
        ),
        patch.object(ensure_migration_chain, "_get_db_revision", return_value="001"),
        patch("alembic.config.Config"),
        patch("alembic.script.ScriptDirectory.from_config") as script_dir,
        patch.object(ensure_migration_chain, "_stamp_to_head") as stamp_mock,
        patch("subprocess.run") as subprocess_mock,
    ):
        script_dir.return_value.get_current_head.return_value = "003"
        # Mock the alembic upgrade head call that follows stamping
        subprocess_mock.return_value.returncode = 0
        subprocess_mock.return_value.stdout = ""
        subprocess_mock.return_value.stderr = ""
        exit_code = ensure_migration_chain.main()

    assert exit_code == 0
    stamp_mock.assert_called_once_with("postgresql+asyncpg://user:pass@db/test", "003")


@pytest.mark.unit
def test_revision_lookup_failure_blocks_startup(monkeypatch, capsys):
    """Database connectivity/read failures should fail instead of masquerading as a fresh DB."""
    monkeypatch.setenv("DATABASE_URL", "postgresql+asyncpg://user:pass@db/test")

    with (
        patch.object(
            ensure_migration_chain, "_get_available_revisions", return_value={"002", "003"}
        ),
        patch("alembic.config.Config"),
        patch("alembic.script.ScriptDirectory.from_config") as script_dir,
        patch.object(
            ensure_migration_chain,
            "_get_db_revision",
            side_effect=ensure_migration_chain.RevisionLookupError("cannot connect to database"),
        ),
    ):
        script_dir.return_value.get_current_head.return_value = "003"
        exit_code = ensure_migration_chain.main()

    captured = capsys.readouterr()
    assert exit_code == 1
    assert "cannot connect to database" in captured.err

"""Regression tests for Story 1.2 configuration extraction."""

import importlib
import sys
from pathlib import Path
from unittest.mock import patch

import pytest
from httpx import ASGITransport, AsyncClient

pytestmark = pytest.mark.slow


_LEGACY_CONFIG_MODULE = ".".join(("app", "config"))
_SCAN_ROOTS = ("app", "worker", "tests", "alembic", "scripts", "core")
_BOUNDARY_CHECKER = Path("scripts/import_analysis.py")
_EXPECTED_ALEMBIC_VERSION_FILES = {
    "001_initial.py",
    "002_add_title_to_download_jobs.py",
    "003_add_error_category_and_failed_jobs.py",
    "004_add_token_version_to_users.py",
    "005_add_composite_indexes.py",
    "006_add_failed_job_original_job_id_index.py",
    "007_add_check_constraints_and_fk.py",
    "008_add_last_error_to_download_jobs.py",
    "009_add_outbox_pending_unique_index.py",
    "010_fix_outbox_and_job_status_constraints.py",
}


def _iter_python_files() -> list[Path]:
    return [
        path
        for root in _SCAN_ROOTS
        if (root_path := Path(root)).exists()
        for path in root_path.rglob("*.py")
    ]


def test_internal_code_has_no_legacy_config_imports():
    """Internal Python code references the canonical config module only."""
    forbidden_fragments = (
        f"from {_LEGACY_CONFIG_MODULE}",
        f"import {_LEGACY_CONFIG_MODULE}",
        _LEGACY_CONFIG_MODULE,
    )

    matches: list[str] = []
    for path in _iter_python_files():
        if path == _BOUNDARY_CHECKER:
            continue
        for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            if any(fragment in line for fragment in forbidden_fragments):
                matches.append(f"{path}:{line_number}: {line.strip()}")

    assert matches == []


def test_legacy_app_config_module_is_removed():
    """The legacy configuration module should not be importable after shim removal."""
    sys.modules.pop(_LEGACY_CONFIG_MODULE, None)

    with pytest.raises(ModuleNotFoundError):
        importlib.import_module(_LEGACY_CONFIG_MODULE)


def test_config_extraction_added_no_alembic_migration():
    """Config extraction does not introduce a database migration."""
    migration_files = {path.name for path in Path("alembic/versions").glob("*.py")}

    assert migration_files == _EXPECTED_ALEMBIC_VERSION_FILES


def test_database_factory_reads_patched_core_database_url():
    """The API database engine factory reads the patched core settings singleton."""
    import core.database as database_module
    from core.config import settings

    factory = database_module._EngineFactory()
    engine = object()
    original_database_url = settings.database_url
    patched_database_url = "sqlite+aiosqlite:///story_1_2_config_extraction.db"

    settings.database_url = patched_database_url
    try:
        with patch("core.database.create_async_engine", return_value=engine) as mock_create_engine:
            assert factory.get_engine() is engine
    finally:
        settings.database_url = original_database_url

    assert mock_create_engine.call_args.args[0] == patched_database_url


@pytest.mark.asyncio
async def test_asgi_root_request_uses_core_config_singleton():
    """An ASGI request runs through an app wired to the core settings singleton."""
    import app.main as main_module
    import core.database as database_module
    from core.config import settings

    assert main_module.settings is settings
    assert database_module.settings is settings

    async with AsyncClient(
        transport=ASGITransport(app=main_module.app),
        base_url="http://test",
        follow_redirects=False,
    ) as client:
        response = await client.get("/")

    assert response.status_code == 303
    assert response.headers["location"] == "/web/login"

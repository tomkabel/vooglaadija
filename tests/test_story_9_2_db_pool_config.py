"""Regression tests for Story 9.2 database pool configuration."""

from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest
import yaml

from tests.test_config import _make_production_settings

PROJECT_ROOT = Path(__file__).resolve().parents[1]


class ComposeLoader(yaml.SafeLoader):
    """YAML loader that understands Docker Compose custom tags used in this repo."""


def _compose_override(loader: ComposeLoader, node: yaml.Node) -> Any:
    if isinstance(node, yaml.SequenceNode):
        return loader.construct_sequence(node)
    if isinstance(node, yaml.MappingNode):
        return loader.construct_mapping(node)
    return loader.construct_scalar(node)


ComposeLoader.add_constructor("!override", _compose_override)


def _read_project_file(*parts: str) -> str:
    return PROJECT_ROOT.joinpath(*parts).read_text()


def _load_yaml_file(*parts: str) -> dict[str, Any]:
    return yaml.load(_read_project_file(*parts), Loader=ComposeLoader)


def test_database_factory_uses_configured_pool_settings():
    """The database engine factory forwards configured pool settings to SQLAlchemy."""
    import core.database as database_module

    configured_settings = _make_production_settings(
        secret_key="a-valid-secret-key-that-is-at-least-32-chars-long",
        database_url="postgresql+asyncpg://u:p@localhost/db",
        db_pool_size=4,
        db_max_overflow=2,
        db_pool_timeout=15,
        db_pool_recycle=900,
    )
    factory = database_module._EngineFactory()
    engine = object()

    with (
        patch.object(database_module, "settings", configured_settings),
        patch("core.database.create_async_engine", return_value=engine) as mock_create_engine,
    ):
        assert factory.get_engine() is engine

    kwargs = mock_create_engine.call_args.kwargs
    assert kwargs["pool_size"] == 4
    assert kwargs["max_overflow"] == 2
    assert kwargs["pool_timeout"] == 15
    assert kwargs["pool_recycle"] == 900
    assert kwargs["pool_pre_ping"] is True


def test_database_factory_omits_queue_pool_kwargs_for_sqlite_urls():
    """The database engine factory omits unsupported queue-pool kwargs for SQLite URLs."""
    import core.database as database_module

    configured_settings = _make_production_settings(
        secret_key="a-valid-secret-key-that-is-at-least-32-chars-long",
        database_url="sqlite+aiosqlite:///:memory:",
        db_pool_size=4,
        db_max_overflow=2,
        db_pool_timeout=15,
        db_pool_recycle=900,
    )
    factory = database_module._EngineFactory()
    engine = object()

    with (
        patch.object(database_module, "settings", configured_settings),
        patch("core.database.create_async_engine", return_value=engine) as mock_create_engine,
    ):
        assert factory.get_engine() is engine

    kwargs = mock_create_engine.call_args.kwargs
    assert "pool_size" not in kwargs
    assert "max_overflow" not in kwargs
    assert "pool_timeout" not in kwargs
    assert kwargs["pool_recycle"] == 900
    assert kwargs["pool_pre_ping"] is True


@pytest.mark.asyncio
async def test_database_factory_still_supports_sqlite_engine_creation():
    """The database engine factory still creates a SQLite async engine for tests."""
    import core.database as database_module

    configured_settings = _make_production_settings(
        secret_key="a-valid-secret-key-that-is-at-least-32-chars-long",
        database_url="sqlite+aiosqlite:///:memory:",
        db_pool_size=4,
        db_max_overflow=2,
        db_pool_timeout=15,
        db_pool_recycle=900,
    )
    factory = database_module._EngineFactory()

    with patch.object(database_module, "settings", configured_settings):
        engine = factory.get_engine()

    try:
        assert engine.url.drivername == "sqlite+aiosqlite"
    finally:
        await engine.dispose()


@pytest.mark.unit
def test_base_compose_exposes_db_pool_defaults_in_common_env():
    """Base compose exposes DB pool defaults shared by API and worker processes."""
    common_env = _load_yaml_file("docker-compose.yml")["x-common-env"]

    assert common_env["DB_POOL_SIZE"] == "${DB_POOL_SIZE:-10}"
    assert common_env["DB_MAX_OVERFLOW"] == "${DB_MAX_OVERFLOW:-5}"
    assert common_env["DB_POOL_TIMEOUT"] == "${DB_POOL_TIMEOUT:-30}"
    assert common_env["DB_POOL_RECYCLE"] == "${DB_POOL_RECYCLE:-1800}"


@pytest.mark.unit
def test_worker_overrides_pool_without_api_override():
    """The worker service gets a smaller pool while the API inherits common defaults."""
    services = _load_yaml_file("docker-compose.yml")["services"]
    api_env = services["api"]["environment"]
    worker_env = services["worker"]["environment"]

    assert worker_env["DB_POOL_SIZE"] == "${WORKER_DB_POOL_SIZE:-3}"
    assert worker_env["DB_MAX_OVERFLOW"] == "${WORKER_DB_MAX_OVERFLOW:-2}"
    assert "WORKER_DB_POOL_SIZE" not in api_env
    assert "WORKER_DB_MAX_OVERFLOW" not in api_env
    assert api_env["DB_POOL_SIZE"] == "${DB_POOL_SIZE:-10}"
    assert api_env["DB_MAX_OVERFLOW"] == "${DB_MAX_OVERFLOW:-5}"


@pytest.mark.unit
def test_env_example_documents_db_pool_tuning_knobs():
    """The env template documents application and worker DB pool tuning knobs."""
    env_example = _read_project_file(".env.example")

    assert "# DB_POOL_SIZE=10" in env_example
    assert "# DB_MAX_OVERFLOW=5" in env_example
    assert "# DB_POOL_TIMEOUT=30" in env_example
    assert "# DB_POOL_RECYCLE=1800" in env_example
    assert "# WORKER_DB_POOL_SIZE=3" in env_example
    assert "# WORKER_DB_MAX_OVERFLOW=2" in env_example

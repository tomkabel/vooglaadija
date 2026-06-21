"""Regression tests for Story 1.4 Redis, logging, and queue extraction."""

import importlib
import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, call, patch

import pytest

_SCAN_ROOTS = ("app", "worker", "tests", "alembic", "scripts", "core")
_THIS_FILE = Path(__file__).name
_EXPECTED_ALEMBIC_VERSION_FILES = {
    "001_initial.py",
    "002_add_title_to_download_jobs.py",
    "003_add_error_category_and_failed_jobs.py",
    "004_add_token_version_to_users.py",
}
_OLD_IMPORT_PATTERNS = (
    "from app.services.redis_client",
    "import app.services.redis_client",
    "app.services.redis_client",
    "from app.logging_config",
    "import app.logging_config",
    "app.logging_config",
    "from worker.queue",
    "import worker.queue",
    "worker.queue",
)


def _iter_python_files() -> list[Path]:
    return [
        path
        for root in _SCAN_ROOTS
        if (root_path := Path(root)).exists()
        for path in root_path.rglob("*.py")
    ]


def test_redis_client_shim_reexports_core_objects():
    """The Redis compatibility shim exposes canonical core Redis objects."""
    app_redis = importlib.import_module("app.services.redis_client")
    core_redis = importlib.import_module("core.redis_client")

    public_exports = (
        "CHAOS_CIRCUIT_BREAKER_KEY",
        "SCENARIO_KEY_MAP",
        "KEY_TO_SCENARIO_FIELD",
        "get_redis_client",
        "reset_redis_client",
        "close_redis_client",
        "check_worker_health",
        "check_chaos_key",
        "get_all_chaos_status",
        "delete_chaos_keys",
    )

    for name in public_exports:
        assert getattr(app_redis, name) is getattr(core_redis, name)


def test_redis_client_shim_does_not_own_client_state_or_settings():
    """The Redis compatibility shim does not own Redis state or constructors."""
    app_redis = importlib.import_module("app.services.redis_client")

    assert "_redis_state" not in app_redis.__dict__
    assert "logger" not in app_redis.__dict__
    assert "settings" not in app_redis.__dict__
    assert "aioredis" not in app_redis.__dict__


def test_logging_config_shim_reexports_core_logging_objects():
    """The logging compatibility shim exposes canonical core logging objects."""
    app_logging = importlib.import_module("app.logging_config")
    core_logging = importlib.import_module("core.logging_config")

    public_exports = (
        "configure_logging",
        "get_logger",
        "add_timestamp",
        "add_service_context",
        "rename_event_key",
        "LoggerAdapter",
    )

    for name in public_exports:
        assert getattr(app_logging, name) is getattr(core_logging, name)


def test_logging_config_shim_does_not_configure_structlog():
    """The logging compatibility shim does not own structlog configuration state."""
    app_logging = importlib.import_module("app.logging_config")

    assert "structlog" not in app_logging.__dict__
    assert "logging" not in app_logging.__dict__
    assert "Processor" not in app_logging.__dict__


@pytest.mark.unit
@pytest.mark.asyncio
async def test_core_queue_enqueue_job_uses_patchable_core_redis_proxy():
    """The queue enqueue helper pushes to Redis through core.queue.redis_client."""
    from core.queue import enqueue_job

    mock_redis = MagicMock()
    mock_redis.lpush = AsyncMock()

    with patch("core.queue.redis_client", mock_redis):
        await enqueue_job("story-1-4-job")

    mock_redis.lpush.assert_called_once_with("download_queue", "story-1-4-job")


@pytest.mark.unit
@pytest.mark.asyncio
async def test_core_queue_retry_push_deduplicates_existing_jobs():
    """The retry queue helper skips zadd when zscore finds an existing job."""
    from core.queue import push_to_retry_queue

    mock_redis = MagicMock()
    mock_redis.zscore = AsyncMock(return_value=123.0)
    mock_redis.zadd = AsyncMock()

    with patch("core.queue.redis_client", mock_redis):
        added = await push_to_retry_queue("550e8400-e29b-41d4-a716-446655440014", 123.0)

    assert added is False
    mock_redis.zscore.assert_called_once_with("retry_queue", "550e8400-e29b-41d4-a716-446655440014")
    mock_redis.zadd.assert_not_called()


@pytest.mark.unit
@pytest.mark.asyncio
async def test_core_queue_download_push_deduplicates_before_lpush():
    """The download queue helper removes existing entries before pushing once."""
    from core.queue import push_to_download_queue

    job_id = "550e8400-e29b-41d4-a716-446655440015"
    mock_redis = MagicMock()
    mock_redis.lrem = AsyncMock(return_value=1)
    mock_redis.lpush = AsyncMock(return_value=1)

    with patch("core.queue.redis_client", mock_redis):
        added = await push_to_download_queue(job_id)

    assert added is True
    assert mock_redis.mock_calls == [
        call.lrem("download_queue", 0, job_id),
        call.lpush("download_queue", job_id),
    ]


def test_redis_singleton_reads_patched_core_redis_url_before_first_creation():
    """The core Redis singleton reads patched settings before first client creation."""
    import core.redis_client as redis_module
    from core.config import settings

    redis_module.reset_redis_client()
    original_redis_url = settings.redis_url
    patched_redis_url = "redis://story-1-4-redis:6379/9"
    client = object()

    settings.redis_url = patched_redis_url
    try:
        with patch("redis.asyncio.from_url", return_value=client) as mock_from_url:
            assert redis_module.get_redis_client() is client
    finally:
        settings.redis_url = original_redis_url
        redis_module.reset_redis_client()

    assert mock_from_url.call_args.args[0] == patched_redis_url
    assert mock_from_url.call_args.kwargs["decode_responses"] is True
    assert mock_from_url.call_args.kwargs["socket_connect_timeout"] == 5
    assert mock_from_url.call_args.kwargs["socket_timeout"] == 5
    assert mock_from_url.call_args.kwargs["retry_on_timeout"] is False


def test_worker_queue_module_is_removed():
    """The old worker.queue module is not importable after queue extraction."""
    sys.modules.pop("worker.queue", None)

    with pytest.raises(ModuleNotFoundError):
        importlib.import_module("worker.queue")


def test_internal_code_has_no_old_redis_logging_or_queue_paths():
    """Internal Python code references canonical Redis, logging, and queue modules only."""
    matches: list[str] = []
    for path in _iter_python_files():
        if path.name == _THIS_FILE:
            continue
        text = path.read_text(encoding="utf-8")
        for pattern in _OLD_IMPORT_PATTERNS:
            if pattern in text:
                matches.append(f"{path}: {pattern}")

    assert matches == []


def test_app_code_has_no_worker_imports():
    """Application modules do not import from the worker package."""
    matches: list[str] = []
    for path in Path("app").rglob("*.py"):
        text = path.read_text(encoding="utf-8")
        if "from worker." in text or "import worker." in text:
            matches.append(str(path))

    assert matches == []


def test_redis_logging_queue_extraction_added_no_alembic_migration():
    """Redis, logging, and queue extraction does not introduce a database migration."""
    migration_files = {path.name for path in Path("alembic/versions").glob("*.py")}

    assert migration_files == _EXPECTED_ALEMBIC_VERSION_FILES

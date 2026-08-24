"""Tests for Settings model_validator — new in this PR."""

import os
import subprocess
import sys
from pathlib import Path

import pytest
from pydantic import ValidationError

# Env var names that conftest sets and that affect Settings
_CONFTEST_ENV_VARS = ("TESTING", "SECRET_KEY", "SECRET_KEY_PREVIOUS", "DATABASE_URL")
_DB_POOL_ENV_VARS = ("DB_POOL_SIZE", "DB_MAX_OVERFLOW", "DB_POOL_TIMEOUT", "DB_POOL_RECYCLE")


def _make_production_settings(**kwargs):
    """Create a fresh Settings instance as if in production (TESTING not set).

    Temporarily removes test env vars so the production validation path runs,
    then restores them after the call. The global settings singleton is left untouched.
    """
    # Import the Settings class (not the singleton) for direct instantiation
    from core.config import Settings

    saved = {}
    for k in _CONFTEST_ENV_VARS:
        saved[k] = os.environ.pop(k, None)
    try:
        return Settings(**kwargs)
    finally:
        for k, v in saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v


class TestSettingsTestingMode:
    """Settings behaviour when TESTING=1 (current test session)."""

    def test_legacy_app_config_module_is_removed(self):
        """The legacy API config module is removed after core extraction."""
        import importlib
        import sys

        legacy_module = ".".join(("app", "config"))
        sys.modules.pop(legacy_module, None)

        with pytest.raises(ModuleNotFoundError):
            importlib.import_module(legacy_module)

    def test_testing_env_skips_validation_and_has_sqlite(self):
        """TESTING=1 skips production validation; database_url uses SQLite."""
        from core.config import settings

        assert "sqlite" in settings.database_url

    def test_secret_key_is_set_in_test_env(self):
        """In test env, SECRET_KEY comes from the conftest override."""
        from core.config import settings

        assert settings.secret_key != ""
        assert len(settings.secret_key) >= 32

    def test_secret_key_previous_defaults_to_empty_string(self):
        """SECRET_KEY_PREVIOUS defaults to disabled in test settings."""
        from core.config import settings

        assert settings.secret_key_previous == ""

    def test_secret_key_previous_reads_env(self, monkeypatch):
        """SECRET_KEY_PREVIOUS maps to the optional previous-secret setting."""
        from core.config import Settings

        monkeypatch.setenv("SECRET_KEY_PREVIOUS", "previous-secret-from-env")

        settings = Settings(_env_file=None)

        assert settings.secret_key_previous == "previous-secret-from-env"

    def test_settings_instance_is_populated(self):
        """Settings should have all required fields populated in test mode."""
        from core.config import settings

        assert settings.database_url
        assert settings.secret_key
        assert settings.redis_url
        assert settings.storage_path

    def test_testing_env_ignores_invalid_db_pool_environment(self, monkeypatch):
        """TESTING=1 ignores malformed DB pool env vars and keeps test settings usable."""
        from core.config import Settings

        for env_name in _DB_POOL_ENV_VARS:
            monkeypatch.setenv(env_name, "not-a-number")

        # Clear DATABASE_URL from env to ensure testing defaults apply
        monkeypatch.delenv("DATABASE_URL", raising=False)

        settings = Settings(_env_file=None)

        assert settings.database_url == "sqlite+aiosqlite:///:memory:"
        assert settings.db_pool_size == 10
        assert settings.db_max_overflow == 5
        assert settings.db_pool_timeout == 30
        assert settings.db_pool_recycle == 1800

    def test_redis_url_is_a_non_empty_string(self):
        """redis_url must be a non-empty string regardless of specific value.

        The assertion was loosened from an exact match to a truthy check so that
        CI environments can override REDIS_URL. This regression test ensures the
        value remains a non-empty string (not None, not empty str).
        """
        from core.config import settings

        assert isinstance(settings.redis_url, str)
        assert len(settings.redis_url) > 0


class TestPaginationInfoSchema:
    """PaginationInfo schema — new in this PR."""

    def test_valid_pagination_info(self):
        from app.schemas.download import PaginationInfo

        p = PaginationInfo(page=1, per_page=20, total=100)
        assert p.page == 1
        assert p.per_page == 20
        assert p.total == 100

    def test_pagination_info_zero_total(self):
        from app.schemas.download import PaginationInfo

        p = PaginationInfo(page=1, per_page=20, total=0)
        assert p.total == 0

    def test_pagination_info_large_page(self):
        from app.schemas.download import PaginationInfo

        p = PaginationInfo(page=100, per_page=100, total=9999)
        assert p.page == 100


class TestDownloadListResponseSchema:
    """DownloadListResponse now requires pagination — new in this PR."""

    def test_requires_pagination_field(self):
        from app.schemas.download import DownloadListResponse, PaginationInfo

        response = DownloadListResponse(
            downloads=[],
            pagination=PaginationInfo(page=1, per_page=20, total=0),
        )
        assert response.pagination.page == 1
        assert response.pagination.total == 0

    def test_missing_pagination_raises_validation_error(self):
        from app.schemas.download import DownloadListResponse

        with pytest.raises((ValidationError, TypeError)):
            DownloadListResponse(downloads=[])  # type: ignore[PGH003]


class TestTokenDataRemoved:
    """TokenData was removed from app.schemas.token in this PR."""

    def test_token_data_not_present(self):
        import app.schemas.token as token_module

        assert not hasattr(token_module, "TokenData"), (
            "TokenData should have been removed from token schemas"
        )

    def test_token_and_token_refresh_still_present(self):
        from app.schemas.token import Token, TokenRefresh

        t = Token(access_token="a", refresh_token="r", token_type="bearer")
        assert t.access_token == "a"
        tr = TokenRefresh(refresh_token="r")
        assert tr.refresh_token == "r"


class TestStorageErrorInExceptions:
    """StorageError was added to app.utils.exceptions in this PR."""

    def test_storage_error_importable(self):
        """StorageError is importable from the canonical exceptions module."""
        from app.utils.exceptions import StorageError

        err = StorageError("storage failed")
        assert str(err) == "storage failed"
        assert isinstance(err, Exception)

    def test_storage_error_is_catchable_as_exception(self):
        """StorageError can be caught as its canonical exception type."""
        from app.utils.exceptions import StorageError

        with pytest.raises(StorageError):
            raise StorageError("test error")


class TestSettingsProductionValidation:
    """Settings validation in production mode (TESTING not set).

    These tests temporarily clear test env vars, reload Settings in production
    mode, then restore everything.
    """

    def test_weak_secret_key_change_me_raises(self):
        """'change-me' secret key raises ValueError."""
        with pytest.raises((ValidationError, ValueError)):
            _make_production_settings(
                secret_key="change-me",
                database_url="postgresql+asyncpg://u:p@localhost/db",
            )

    def test_short_secret_key_raises(self):
        """SECRET_KEY shorter than 32 chars raises ValueError."""
        with pytest.raises(
            (ValidationError, ValueError),
            match="SECRET_KEY must be at least 32 characters",
        ):
            _make_production_settings(
                secret_key="tooshort",
                database_url="postgresql+asyncpg://u:p@localhost/db",
            )

    def test_empty_secret_key_raises(self):
        """Empty SECRET_KEY raises ValueError."""
        with pytest.raises((ValidationError, ValueError)):
            _make_production_settings(
                secret_key="",
                database_url="postgresql+asyncpg://u:p@localhost/db",
            )

    def test_no_database_url_and_no_db_password_raises(self):
        """Missing both DATABASE_URL and DB_PASSWORD raises ValueError."""
        with pytest.raises((ValidationError, ValueError)):
            _make_production_settings(
                secret_key="a-valid-secret-key-that-is-at-least-32-chars",
                database_url="",
                db_password="",
            )

    def test_wildcard_cors_raises(self):
        """CORS_ORIGINS='*' fails closed in production settings."""
        with pytest.raises((ValidationError, ValueError), match="CORS_ORIGINS cannot be"):
            _make_production_settings(
                secret_key="a-valid-secret-key-that-is-at-least-32-chars-long",
                database_url="postgresql+asyncpg://u:p@localhost/db",
                cors_origins="*",
            )

    def test_valid_cors_origins_are_accepted(self):
        """HTTP and HTTPS CORS origins, including ports, are accepted and normalized."""
        s = _make_production_settings(
            secret_key="a-valid-secret-key-that-is-at-least-32-chars-long",
            database_url="postgresql+asyncpg://u:p@localhost/db",
            cors_origins="http://example.com, https://example.com:8443",
        )

        assert s.cors_origins == "http://example.com,https://example.com:8443"

    @pytest.mark.parametrize(
        "cors_origin",
        [
            "not-a-url",
            "ftp://example.com",
            "http://example.com/path",
            "https://example.com:bad",
            "https://example.com:0",
            "https://user:pass@example.com",
            "http://exam ple.com",
        ],
    )
    def test_invalid_cors_origins_raise(self, cors_origin):
        """Malformed or unsupported CORS_ORIGINS entries raise ValueError."""
        with pytest.raises((ValidationError, ValueError), match="Invalid CORS origin"):
            _make_production_settings(
                secret_key="a-valid-secret-key-that-is-at-least-32-chars-long",
                database_url="postgresql+asyncpg://u:p@localhost/db",
                cors_origins=cors_origin,
            )

    @pytest.mark.parametrize(
        ("port_field", "port_value"),
        [
            ("db_port", "0"),
            ("db_port", "65536"),
            ("db_port", "not-a-port"),
            ("redis_port", "0"),
            ("redis_port", "65536"),
            ("redis_port", "not-a-port"),
        ],
    )
    def test_invalid_configured_ports_raise(self, port_field, port_value):
        """DB_PORT and REDIS_PORT outside 1-65535 or non-numeric raise ValueError."""
        with pytest.raises((ValidationError, ValueError), match=port_field.upper()):
            _make_production_settings(
                secret_key="a-valid-secret-key-that-is-at-least-32-chars-long",
                database_url="postgresql+asyncpg://u:p@localhost/db",
                **{port_field: port_value},
            )

    def test_unwritable_storage_path_raises(self, tmp_path, monkeypatch):
        """An unwritable STORAGE_PATH raises ValueError with the resolved path."""
        blocked_path = tmp_path / "blocked-storage"
        resolved_path = blocked_path.resolve()

        def fake_access(path, mode):
            return Path(path) != resolved_path

        monkeypatch.setattr(os, "access", fake_access)

        with pytest.raises(
            (ValidationError, ValueError),
            match=f"Storage path not writable: {resolved_path}",
        ):
            _make_production_settings(
                secret_key="a-valid-secret-key-that-is-at-least-32-chars-long",
                database_url="postgresql+asyncpg://u:p@localhost/db",
                storage_path=str(blocked_path),
            )

    def test_environment_field_is_absent(self):
        """Settings no longer exposes the removed environment field."""
        s = _make_production_settings(
            secret_key="a-valid-secret-key-that-is-at-least-32-chars-long",
            database_url="postgresql+asyncpg://u:p@localhost/db",
        )

        assert not hasattr(s, "environment")

    def test_core_config_import_fails_fast_for_invalid_production_config(self, tmp_path):
        """The settings singleton fails during module import for invalid production config."""
        env = os.environ.copy()
        env.pop("TESTING", None)
        env["DATABASE_URL"] = "postgresql+asyncpg://u:p@localhost/db"
        env["SECRET_KEY"] = "a1b2c3d4e5f6a7b8c9d0e1f2a3b4c5d6e7f8a9b0c1d2e3f4a5b6c7d8e9f0a1b2"
        env["CORS_ORIGINS"] = "not-a-url"
        env["STORAGE_PATH"] = str(tmp_path / "startup-storage")

        result = subprocess.run(
            [sys.executable, "-c", "import core.config"],
            check=False,
            capture_output=True,
            env=env,
            text=True,
        )

        assert result.returncode != 0
        assert "Invalid CORS origin: 'not-a-url'" in result.stderr

    def test_database_url_constructed_from_components(self):
        """DATABASE_URL is built from DB_USER/DB_PASSWORD/DB_NAME when not set directly."""
        s = _make_production_settings(
            secret_key="a-valid-secret-key-that-is-at-least-32-chars-long",
            database_url="",
            db_user="myuser",
            db_password="mypassword",
            db_name="mydb",
        )
        assert "myuser" in s.database_url
        assert "mypassword" in s.database_url
        assert "mydb" in s.database_url
        assert "postgresql+asyncpg" in s.database_url

    def test_valid_settings_succeeds(self):
        """Settings with valid values should not raise."""
        s = _make_production_settings(
            secret_key="a-valid-secret-key-that-is-at-least-32-chars-long",
            database_url="postgresql+asyncpg://u:p@localhost/db",
        )
        assert s.secret_key == "a-valid-secret-key-that-is-at-least-32-chars-long"

    def test_db_pool_settings_default_to_production_values(self, monkeypatch):
        """DB pool settings default to the documented production values."""
        for env_name in _DB_POOL_ENV_VARS:
            monkeypatch.delenv(env_name, raising=False)

        s = _make_production_settings(
            secret_key="a-valid-secret-key-that-is-at-least-32-chars-long",
            database_url="postgresql+asyncpg://u:p@localhost/db",
        )

        assert s.db_pool_size == 10
        assert s.db_max_overflow == 5
        assert s.db_pool_timeout == 30
        assert s.db_pool_recycle == 1800

    def test_db_pool_settings_accept_constructor_overrides(self):
        """DB pool settings can be overridden by explicit Settings constructor values."""
        s = _make_production_settings(
            secret_key="a-valid-secret-key-that-is-at-least-32-chars-long",
            database_url="postgresql+asyncpg://u:p@localhost/db",
            db_pool_size=12,
            db_max_overflow=7,
            db_pool_timeout=45,
            db_pool_recycle=2400,
        )

        assert s.db_pool_size == 12
        assert s.db_max_overflow == 7
        assert s.db_pool_timeout == 45
        assert s.db_pool_recycle == 2400

    def test_db_pool_settings_read_environment_overrides(self, monkeypatch):
        """DB pool settings read DB_POOL_* environment variable overrides."""
        monkeypatch.setenv("DB_POOL_SIZE", "13")
        monkeypatch.setenv("DB_MAX_OVERFLOW", "8")
        monkeypatch.setenv("DB_POOL_TIMEOUT", "50")
        monkeypatch.setenv("DB_POOL_RECYCLE", "2100")

        s = _make_production_settings(
            secret_key="a-valid-secret-key-that-is-at-least-32-chars-long",
            database_url="postgresql+asyncpg://u:p@localhost/db",
        )

        assert s.db_pool_size == 13
        assert s.db_max_overflow == 8
        assert s.db_pool_timeout == 50
        assert s.db_pool_recycle == 2100

    @pytest.mark.parametrize(
        ("field_name", "value", "message"),
        [
            ("db_pool_size", 0, "Invalid DB_POOL_SIZE"),
            ("db_pool_size", -1, "Invalid DB_POOL_SIZE"),
            ("db_max_overflow", -1, "Invalid DB_MAX_OVERFLOW"),
            ("db_pool_timeout", 0, "Invalid DB_POOL_TIMEOUT"),
            ("db_pool_timeout", -1, "Invalid DB_POOL_TIMEOUT"),
            ("db_pool_recycle", 0, "Invalid DB_POOL_RECYCLE"),
            ("db_pool_recycle", -1, "Invalid DB_POOL_RECYCLE"),
        ],
    )
    def test_invalid_db_pool_ranges_raise(self, field_name, value, message):
        """DB pool settings reject values outside the supported ranges."""
        with pytest.raises((ValidationError, ValueError), match=message):
            _make_production_settings(
                secret_key="a-valid-secret-key-that-is-at-least-32-chars-long",
                database_url="postgresql+asyncpg://u:p@localhost/db",
                **{field_name: value},
            )

    @pytest.mark.parametrize(
        ("env_name", "message"),
        [
            ("DB_POOL_SIZE", "Invalid DB_POOL_SIZE"),
            ("DB_MAX_OVERFLOW", "Invalid DB_MAX_OVERFLOW"),
            ("DB_POOL_TIMEOUT", "Invalid DB_POOL_TIMEOUT"),
            ("DB_POOL_RECYCLE", "Invalid DB_POOL_RECYCLE"),
        ],
    )
    def test_invalid_db_pool_environment_strings_raise(self, env_name, message, monkeypatch):
        """DB pool settings reject non-numeric environment variable values."""
        monkeypatch.setenv(env_name, "not-a-number")

        with pytest.raises((ValidationError, ValueError), match=message):
            _make_production_settings(
                secret_key="a-valid-secret-key-that-is-at-least-32-chars-long",
                database_url="postgresql+asyncpg://u:p@localhost/db",
            )

    def test_known_weak_default_keys_rejected(self):
        """Keys with genuinely low Shannon entropy should be rejected."""
        weak_keys = [
            "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",  # 0.0 bits/char
        ]
        for key in weak_keys:
            with pytest.raises((ValidationError, ValueError)):
                _make_production_settings(
                    secret_key=key,
                    database_url="postgresql+asyncpg://u:p@localhost/db",
                )

    def test_high_entropy_key_accepted(self):
        """A high-entropy key (like secrets.token_hex output) should pass."""
        s = _make_production_settings(
            secret_key="a1b2c3d4e5f6a7b8c9d0e1f2a3b4c5d6e7f8a9b0c1d2e3f4a5b6c7d8e9f0a1b2",
            database_url="postgresql+asyncpg://u:p@localhost/db",
        )
        assert len(s.secret_key) >= 64


class TestWorkerConcurrencyConfig:
    """WORKER_CONCURRENCY validation and DB-pool coupling."""

    def test_worker_concurrency_default_is_two(self):
        """Default worker concurrency enables a small parallel pool out of the box."""
        s = _make_production_settings(
            secret_key="a-valid-secret-key-that-is-at-least-32-chars-long",
            database_url="postgresql+asyncpg://u:p@localhost/db",
        )
        assert s.worker_concurrency == 2

    def test_worker_concurrency_accepts_valid_range(self):
        """Concurrency within [1, 32] is accepted."""
        s = _make_production_settings(
            secret_key="a-valid-secret-key-that-is-at-least-32-chars-long",
            database_url="postgresql+asyncpg://u:p@localhost/db",
            worker_concurrency=8,
            db_pool_size=12,
            db_max_overflow=4,
        )
        assert s.worker_concurrency == 8

    def test_worker_concurrency_below_one_rejected(self):
        """Concurrency must be at least 1."""
        with pytest.raises((ValidationError, ValueError), match="Invalid WORKER_CONCURRENCY"):
            _make_production_settings(
                secret_key="a-valid-secret-key-that-is-at-least-32-chars-long",
                database_url="postgresql+asyncpg://u:p@localhost/db",
                worker_concurrency=0,
            )

    def test_worker_concurrency_above_max_rejected(self):
        """Concurrency above 32 is rejected."""
        with pytest.raises((ValidationError, ValueError), match="Invalid WORKER_CONCURRENCY"):
            _make_production_settings(
                secret_key="a-valid-secret-key-that-is-at-least-32-chars-long",
                database_url="postgresql+asyncpg://u:p@localhost/db",
                worker_concurrency=64,
                db_pool_size=80,
                db_max_overflow=20,
            )

    def test_worker_concurrency_requires_sufficient_db_pool(self):
        """Worker concurrency must be backed by a DB pool of at least N+2."""
        with pytest.raises((ValidationError, ValueError), match="for the worker"):
            _make_production_settings(
                secret_key="a-valid-secret-key-that-is-at-least-32-chars-long",
                database_url="postgresql+asyncpg://u:p@localhost/db",
                worker_concurrency=6,
                db_pool_size=4,
                db_max_overflow=0,
            )


class TestYtDlpWarmPoolConfig:
    """YT_DLP_WARM_POOL / YT_DLP_POOL_SIZE validation (#162)."""

    def test_warm_pool_enabled_by_default(self):
        """The warm pool is on by default to kill the per-job import cold start."""
        s = _make_production_settings(
            secret_key="a-valid-secret-key-that-is-at-least-32-chars-long",
            database_url="postgresql+asyncpg://u:p@localhost/db",
        )
        assert s.yt_dlp_warm_pool is True
        assert s.yt_dlp_pool_size == 2

    def test_warm_pool_size_below_one_rejected(self):
        with pytest.raises((ValidationError, ValueError), match="Invalid YT_DLP_POOL_SIZE"):
            _make_production_settings(
                secret_key="a-valid-secret-key-that-is-at-least-32-chars-long",
                database_url="postgresql+asyncpg://u:p@localhost/db",
                yt_dlp_pool_size=0,
            )

    def test_warm_pool_size_above_max_rejected(self):
        with pytest.raises((ValidationError, ValueError), match="Invalid YT_DLP_POOL_SIZE"):
            _make_production_settings(
                secret_key="a-valid-secret-key-that-is-at-least-32-chars-long",
                database_url="postgresql+asyncpg://u:p@localhost/db",
                yt_dlp_pool_size=64,
            )

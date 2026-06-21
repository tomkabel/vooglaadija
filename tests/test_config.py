"""Tests for Settings model_validator — new in this PR."""

import os
import warnings

import pytest
from pydantic import ValidationError

# Env var names that conftest sets and that affect Settings
_CONFTEST_ENV_VARS = ("TESTING", "SECRET_KEY", "DATABASE_URL")


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

    def test_compatibility_shim_reexports_core_singletons(self):
        """The temporary API config shim re-exports the canonical core objects."""
        import importlib

        api_config = importlib.import_module("app" + ".config")
        core_config = importlib.import_module("core.config")

        assert api_config.settings is core_config.settings
        assert api_config.Settings is core_config.Settings

    def test_testing_env_skips_validation_and_has_sqlite(self):
        """TESTING=1 skips production validation; database_url uses SQLite."""
        from core.config import settings

        assert "sqlite" in settings.database_url

    def test_secret_key_is_set_in_test_env(self):
        """In test env, SECRET_KEY comes from the conftest override."""
        from core.config import settings

        assert settings.secret_key != ""
        assert len(settings.secret_key) >= 32

    def test_settings_instance_is_populated(self):
        """Settings should have all required fields populated in test mode."""
        from core.config import settings

        assert settings.database_url
        assert settings.secret_key
        assert settings.redis_url
        assert settings.storage_path

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
        from app.utils.exceptions import StorageError

        err = StorageError("storage failed")
        assert str(err) == "storage failed"
        assert isinstance(err, Exception)

    def test_yt_dlp_error_still_present(self):
        from app.utils.exceptions import YTDLPError

        err = YTDLPError("yt-dlp failed")
        assert isinstance(err, Exception)

    def test_storage_error_and_yt_dlp_error_are_distinct(self):
        from app.utils.exceptions import StorageError, YTDLPError

        assert not issubclass(StorageError, YTDLPError)
        assert not issubclass(YTDLPError, StorageError)

    def test_storage_error_is_catchable_as_exception(self):
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
        with pytest.raises((ValidationError, ValueError)):
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

    def test_wildcard_cors_issues_warning(self):
        """CORS_ORIGINS='*' raises a UserWarning about insecure configuration."""
        with warnings.catch_warnings(record=True) as w:
            warnings.simplefilter("always")
            _make_production_settings(
                secret_key="a-valid-secret-key-that-is-at-least-32-chars-long",
                database_url="postgresql+asyncpg://u:p@localhost/db",
                cors_origins="*",
            )
        assert any("allowing all origins" in str(warning.message) for warning in w), (
            "Expected warning about wildcard CORS origins"
        )

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

"""Unit tests for browser-downloader settings in core.config."""

from __future__ import annotations

import pytest


class TestBrowserDownloaderSettingsDefaults:
    """Verify the four new settings exist with documented defaults.

    Phase 2 wires these via env vars; defaults must keep the system
    pre-Phase-2-safe (browser_downloader_enabled=False means the worker
    routes everything to yt-dlp, matching prior behavior).
    """

    @pytest.mark.unit
    def test_browser_downloader_enabled_defaults_to_false(self) -> None:
        from core.config import Settings

        s = Settings(_env_file=None)  # type: ignore[call-arg]
        assert s.browser_downloader_enabled is False

    @pytest.mark.unit
    def test_browser_downloader_endpoint_default(self) -> None:
        from core.config import Settings

        s = Settings(_env_file=None)  # type: ignore[call-arg]
        assert s.browser_downloader_endpoint == "http://browser-downloader:3000"

    @pytest.mark.unit
    def test_browser_downloader_timeout_default_is_300(self) -> None:
        from core.config import Settings

        s = Settings(_env_file=None)  # type: ignore[call-arg]
        assert s.browser_downloader_timeout == 300

    @pytest.mark.unit
    def test_browser_downloader_cb_use_redis_defaults_to_false(self) -> None:
        from core.config import Settings

        s = Settings(_env_file=None)  # type: ignore[call-arg]
        assert s.browser_downloader_cb_use_redis is False


class TestBrowserDownloaderSettingsValidation:
    """Failure modes for the new validators.

    The project's `Settings.validate_and_construct` skips validators in
    `TESTING=1` mode (see `_is_testing_enabled` in core/config.py), so we
    exercise `_validate_browser_downloader` directly to assert the rules.
    """

    @pytest.mark.unit
    def test_invalid_endpoint_url_raises(self) -> None:
        from core.config import Settings

        s = Settings(_env_file=None)  # type: ignore[call-arg]
        s.browser_downloader_endpoint = "not-a-url"
        with pytest.raises(ValueError, match="BROWSER_DOWNLOADER_ENDPOINT"):
            s._validate_browser_downloader()

    @pytest.mark.unit
    def test_timeout_zero_is_rejected(self) -> None:
        from core.config import Settings

        s = Settings(_env_file=None)  # type: ignore[call-arg]
        s.browser_downloader_timeout = 0
        with pytest.raises(ValueError, match="BROWSER_DOWNLOADER_TIMEOUT"):
            s._validate_browser_downloader()

    @pytest.mark.unit
    def test_empty_endpoint_is_rejected(self) -> None:
        from core.config import Settings

        s = Settings(_env_file=None)  # type: ignore[call-arg]
        s.browser_downloader_endpoint = ""
        with pytest.raises(ValueError, match="BROWSER_DOWNLOADER_ENDPOINT"):
            s._validate_browser_downloader()

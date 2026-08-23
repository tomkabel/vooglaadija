"""Tests for the Phase 2 routing decision in worker.job_executor.

The I/O matrix in spec-gh-140-p2-worker-integration.md:
- tiktok/instagram/twitter/x URL + feature on → browser executor
- youtube URL → yt-dlp
- unknown host → yt-dlp (fallthrough, current behavior)
- feature off forces yt-dlp even for TikTok
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from worker import job_executor
from worker.job_executor import _resolve_executor_kind, select_executor


class TestSelectExecutor:
    """select_executor is the pure routing function. It does not touch settings."""

    @pytest.mark.unit
    @pytest.mark.parametrize(
        "url",
        [
            "https://www.tiktok.com/@user/video/123",
            "https://vm.tiktok.com/x",
            "https://www.instagram.com/reel/abc",
            "https://instagr.am/p/x",
            "https://twitter.com/u/status/1",
            "https://x.com/u/status/1",
        ],
    )
    def test_browser_platforms(self, url: str) -> None:
        assert select_executor(url) == "browser"

    @pytest.mark.unit
    def test_tco_routes_to_youtube(self) -> None:
        # t.co is a generic shortener that may redirect to YouTube; it must not
        # be routed to the browser executor (see select_executor TODO).
        assert select_executor("https://t.co/abc") == "youtube"

    @pytest.mark.unit
    @pytest.mark.parametrize(
        "url",
        [
            "https://www.youtube.com/watch?v=abc",
            "https://youtu.be/abc",
            "https://example.com/foo",
            "https://vimeo.com/123",
        ],
    )
    def test_youtube_or_unknown(self, url: str) -> None:
        assert select_executor(url) == "youtube"


class TestResolveExecutorKind:
    """_resolve_executor_kind respects the browser_downloader_enabled flag."""

    @pytest.mark.unit
    def test_tiktok_url_with_feature_off_returns_youtube(self) -> None:
        with patch.object(job_executor.settings, "browser_downloader_enabled", False):
            assert _resolve_executor_kind("https://tiktok.com/@u/v/1") == "youtube"

    @pytest.mark.unit
    def test_tiktok_url_with_feature_on_returns_browser(self) -> None:
        with patch.object(job_executor.settings, "browser_downloader_enabled", True):
            assert _resolve_executor_kind("https://tiktok.com/@u/v/1") == "browser"

    @pytest.mark.unit
    def test_youtube_url_with_feature_on_returns_youtube(self) -> None:
        with patch.object(job_executor.settings, "browser_downloader_enabled", True):
            assert _resolve_executor_kind("https://youtu.be/abc") == "youtube"

    @pytest.mark.unit
    def test_unknown_host_with_feature_on_returns_youtube(self) -> None:
        with patch.object(job_executor.settings, "browser_downloader_enabled", True):
            assert _resolve_executor_kind("https://example.com/foo") == "youtube"

    @pytest.mark.unit
    def test_instagram_url_with_feature_off_returns_youtube(self) -> None:
        with patch.object(job_executor.settings, "browser_downloader_enabled", False):
            assert _resolve_executor_kind("https://instagram.com/p/x") == "youtube"


class TestExecuteRoutesToBrowserExecutor:
    """End-to-end: `execute()` invokes the browser path for TikTok + feature on."""

    @pytest.mark.asyncio
    @pytest.mark.unit
    async def test_browser_path_invokes_browser_executor_for_tiktok(self) -> None:
        from core.models.download_job import DownloadJob
        from worker.job_executor import execute

        job = DownloadJob(
            id="550e8400-e29b-41d4-a716-446655440000",
            user_id="550e8400-e29b-41d4-a716-446655440005",
            url="https://tiktok.com/@u/v/1",
            status="processing",
            retry_count=0,
        )

        mock_browser = AsyncMock(
            return_value=("/storage/abc.mp4", "abc.mp4", None),
        )
        with (
            patch.object(job_executor.settings, "browser_downloader_enabled", True),
            patch.object(job_executor.settings, "feature_throttle_preemptive_enabled", False),
            patch.object(job_executor, "extract_media_browser", mock_browser),
            patch.object(
                job_executor,
                "extract_media_with_circuit_breaker",
                AsyncMock(),
            ) as mock_ytdlp,
        ):
            db = AsyncMock()
            db.execute = AsyncMock()
            db.commit = AsyncMock()
            # First db.execute call updates the row to completed; subsequent
            # calls re-select. We return a fake result with rowcount=1.
            update_result = AsyncMock()
            update_result.rowcount = 1
            update_result.scalar_one_or_none = AsyncMock(return_value=job)
            db.execute.return_value = update_result

            await execute(db, job, start_time=0.0)

        mock_browser.assert_awaited_once()
        mock_ytdlp.assert_not_awaited()

    @pytest.mark.asyncio
    @pytest.mark.unit
    async def test_youtube_path_skips_browser_executor(self) -> None:
        from core.models.download_job import DownloadJob
        from worker.job_executor import execute

        job = DownloadJob(
            id="550e8400-e29b-41d4-a716-446655440000",
            user_id="550e8400-e29b-41d4-a716-446655440005",
            url="https://youtu.be/abc",
            status="processing",
            retry_count=0,
        )

        mock_ytdlp = AsyncMock(
            return_value=("/storage/yt.mp4", "yt.mp4", "Title"),
        )
        with (
            patch.object(job_executor.settings, "browser_downloader_enabled", True),
            patch.object(job_executor.settings, "feature_throttle_preemptive_enabled", False),
            patch.object(job_executor, "extract_media_browser", AsyncMock()) as mock_browser,
            patch.object(
                job_executor,
                "extract_media_with_circuit_breaker",
                mock_ytdlp,
            ),
        ):
            db = AsyncMock()
            update_result = AsyncMock()
            update_result.rowcount = 1
            update_result.scalar_one_or_none = AsyncMock(return_value=job)
            db.execute = AsyncMock(return_value=update_result)
            db.commit = AsyncMock()

            await execute(db, job, start_time=0.0)

        mock_ytdlp.assert_awaited_once()
        mock_browser.assert_not_awaited()

    @pytest.mark.asyncio
    @pytest.mark.unit
    async def test_feature_off_routes_tiktok_to_ytdlp(self) -> None:
        from core.models.download_job import DownloadJob
        from worker.job_executor import execute

        job = DownloadJob(
            id="550e8400-e29b-41d4-a716-446655440000",
            user_id="550e8400-e29b-41d4-a716-446655440005",
            url="https://tiktok.com/@u/v/1",
            status="processing",
            retry_count=0,
        )

        mock_ytdlp = AsyncMock(
            return_value=("/storage/yt.mp4", "yt.mp4", None),
        )
        with (
            patch.object(job_executor.settings, "browser_downloader_enabled", False),
            patch.object(job_executor.settings, "feature_throttle_preemptive_enabled", False),
            patch.object(job_executor, "extract_media_browser", AsyncMock()) as mock_browser,
            patch.object(
                job_executor,
                "extract_media_with_circuit_breaker",
                mock_ytdlp,
            ),
        ):
            db = AsyncMock()
            update_result = AsyncMock()
            update_result.rowcount = 1
            update_result.scalar_one_or_none = AsyncMock(return_value=job)
            db.execute = AsyncMock(return_value=update_result)
            db.commit = AsyncMock()

            await execute(db, job, start_time=0.0)

        mock_ytdlp.assert_awaited_once()
        mock_browser.assert_not_awaited()

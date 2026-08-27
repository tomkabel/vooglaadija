"""yt_dlp service tests."""

from __future__ import annotations

import asyncio
import tempfile
import uuid
from collections.abc import Generator
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

from app.services.yt_dlp_service import _get_platform, extract_media_url
from app.utils.exceptions import StorageError
from app.utils.validators import is_youtube_url


class TestIsYoutubeUrl:
    """Tests for URL validation."""

    def test_valid_youtube_watch_url(self) -> None:
        assert is_youtube_url("https://www.youtube.com/watch?v=dQw4w9WgXcQ") is True

    def test_valid_youtube_short_url(self) -> None:
        assert is_youtube_url("https://youtu.be/dQw4w9WgXcQ") is True

    def test_valid_youtube_nocookie_url(self) -> None:
        assert is_youtube_url("https://www.youtube-nocookie.com/watch?v=dQw4w9WgXcQ") is True

    def test_valid_youtube_shorts_url(self) -> None:
        assert is_youtube_url("https://www.youtube.com/shorts/dQw4w9WgXcQ") is True

    def test_valid_youtube_mobile_url(self) -> None:
        assert is_youtube_url("https://m.youtube.com/watch?v=dQw4w9WgXcQ") is True

    def test_invalid_google_url(self) -> None:
        assert is_youtube_url("https://www.google.com") is False

    def test_invalid_vimeo_url(self) -> None:
        assert is_youtube_url("https://vimeo.com/123456") is False

    def test_invalid_random_url(self) -> None:
        assert is_youtube_url("https://example.com/video") is False

    def test_invalid_not_url(self) -> None:
        assert is_youtube_url("not-a-url") is False

    def test_invalid_empty_string(self) -> None:
        assert is_youtube_url("") is False

    def test_invalid_ftp_url(self) -> None:
        assert is_youtube_url("ftp://youtube.com/video") is False

    def test_case_insensitive(self) -> None:
        assert is_youtube_url("https://WWW.YOUTUBE.COM/watch?v=dQw4w9WgXcQ") is True

    def test_subdomain_bypass_rejected(self) -> None:
        """Critical: subdomain bypass must be rejected."""
        assert is_youtube_url("https://youtube.com.evil.com/watch?v=abc") is False
        assert is_youtube_url("https://notyoutube.com/watch?v=abc") is False


def _make_subprocess_mock(title: str = "Test Video", ext: str | None = "mp4") -> AsyncMock:
    """Helper to create a mock for _extract_via_subprocess."""
    mock = AsyncMock()
    mock.return_value = {"title": title, "ext": ext}
    return mock


class _AsyncLineStream:
    """Minimal async iterator matching asyncio StreamReader line iteration."""

    def __init__(self, lines: list[bytes] | None = None) -> None:
        self._lines = list(lines or [])

    def __aiter__(self):
        return self

    async def __anext__(self) -> bytes:
        if not self._lines:
            raise StopAsyncIteration
        return self._lines.pop(0)


def _make_process(
    *,
    stdout: list[bytes] | None = None,
    stderr: list[bytes] | None = None,
    returncode: int | None = 0,
    pid: int = 12345,
) -> AsyncMock:
    """Create a subprocess mock for the streaming extraction implementation."""
    mock_process = AsyncMock()
    stdout_lines = stdout if stdout is not None else [b'{"title": "T", "ext": "mp4"}\n']
    stderr_lines = stderr if stderr is not None else []
    mock_process.stdout = _AsyncLineStream(stdout_lines)
    mock_process.stderr = _AsyncLineStream(stderr_lines)
    mock_process.returncode = returncode
    mock_process.pid = pid
    mock_process.wait = AsyncMock(return_value=0 if returncode is None else returncode)
    return mock_process


def _discard_awaitable(awaitable) -> None:
    """Prevent un-awaited coroutine/future warnings when mocked wait_for times out."""
    if hasattr(awaitable, "cancel"):
        awaitable.cancel()
    elif hasattr(awaitable, "close"):
        awaitable.close()


class TestFormatSpecFor:
    """_format_spec_for honors YT_DLP_PREFER_PROGRESSIVE (#170)."""

    def test_youtube_default_uses_merged_chain(self) -> None:
        """Default (progressive off) returns the merged-combo YouTube chain."""
        from app.services.yt_dlp_service import (
            YOUTUBE_FORMAT,
            _format_spec_for,
        )

        fmt, _sort = _format_spec_for("youtube")
        assert fmt == YOUTUBE_FORMAT
        assert "bestvideo*+bestaudio" in fmt
        assert "bestvideo+bestaudio" in fmt
        assert "worstvideo*+bestaudio" in fmt
        assert " / best" in fmt
        assert " / worst" in fmt
        # The old chain enshrined dead format-ID selectors (`res:1080+h264`,
        # `res:720`) that yt-dlp treats as literal format IDs; assert they are gone.
        assert "res:1080+h264" not in fmt
        assert "res:720" not in fmt

    def test_youtube_progressive_enabled_uses_progressive_first(self, monkeypatch) -> None:
        """When the setting is on, YouTube returns the progressive-first chain."""
        from app.services.yt_dlp_service import (
            YOUTUBE_FORMAT_PROGRESSIVE,
            _format_spec_for,
            settings,
        )

        monkeypatch.setattr(settings, "yt_dlp_prefer_progressive", True)
        fmt, _sort = _format_spec_for("youtube")
        assert fmt == YOUTUBE_FORMAT_PROGRESSIVE
        # Progressive single-stream entry leads; DASH is excluded with the
        # substring filter (`protocol!*=dash`), since bare `dash` is never a
        # real yt-dlp protocol value.
        assert fmt.startswith("best[ext=mp4][protocol!*=dash]")
        assert "[protocol!*=dash]" in fmt
        assert "bestvideo*+bestaudio" in fmt
        assert "res:1080+h264" not in fmt
        assert "res:720" not in fmt

    def test_non_youtube_always_single_stream(self) -> None:
        from app.services.yt_dlp_service import _format_spec_for

        fmt, _sort = _format_spec_for("tiktok")
        assert fmt == "best"


class TestFormatSpecValidity:
    """The format chains are parseable by yt-dlp and select as intended.

    The warm-pool tests only cover metadata mode, so these guard the format
    strings against malformed or ineffective selectors — the dead
    ``res:1080+h264`` / ``res:720`` fragments used to be enshrined in the
    contract tests without ever being executed against yt-dlp (issues #169/#170).
    """

    @staticmethod
    def _parse_segments(fmt: str) -> list[str]:
        return [seg.strip() for seg in fmt.split("/") if seg.strip()]

    @pytest.mark.parametrize(
        "fmt",
        ["YOUTUBE_FORMAT", "YOUTUBE_FORMAT_PROGRESSIVE", "GENERIC_FORMAT"],
    )
    def test_every_segment_parses(self, fmt: str) -> None:
        """Every '/'-separated fallback segment must be a valid yt-dlp selector."""
        import yt_dlp

        from app.services import yt_dlp_service

        spec = getattr(yt_dlp_service, fmt)
        for segment in self._parse_segments(spec):
            yt_dlp.YoutubeDL().build_format_selector(segment)  # raises if invalid

    def test_no_dead_format_id_fragments(self) -> None:
        """Bare `res:N`/codec-name tokens are format IDs, not filters."""
        from app.services.yt_dlp_service import YOUTUBE_FORMAT, YOUTUBE_FORMAT_PROGRESSIVE

        for fmt in (YOUTUBE_FORMAT, YOUTUBE_FORMAT_PROGRESSIVE):
            assert "res:1080+h264" not in fmt
            assert "res:720" not in fmt

    def test_progressive_chain_excludes_dash_protocols(self) -> None:
        """`[protocol!*=dash]` skips http_dash_segments and picks a progressive mp4."""
        import yt_dlp

        selector = yt_dlp.YoutubeDL().build_format_selector("best[ext=mp4][protocol!*=dash]")
        formats = [
            {
                "format_id": "dash",
                "ext": "mp4",
                "protocol": "http_dash_segments",
                "vcodec": "vp9",
                "acodec": "none",
                "height": 1080,
                "width": 1920,
                "tbr": 2000,
            },
            {
                "format_id": "prog",
                "ext": "mp4",
                "protocol": "https",
                "vcodec": "avc1",
                "acodec": "mp4a",
                "height": 720,
                "width": 1280,
                "tbr": 2500,
            },
            {
                "format_id": "webm",
                "ext": "webm",
                "protocol": "https",
                "vcodec": "vp9",
                "acodec": "opus",
                "height": 480,
                "width": 854,
                "tbr": 800,
            },
        ]
        selected = selector({"id": "x", "formats": formats})
        assert [f["format_id"] for f in selected] == ["prog"]


class TestExtractMediaUrl:
    """Tests for extract_media_url function."""

    @pytest.fixture
    def temp_storage_path(self) -> Generator[Path, None, None]:
        with tempfile.TemporaryDirectory() as tmpdir:
            yield Path(tmpdir)

    @pytest.mark.asyncio
    async def test_extract_media_url_returns_tuple(
        self, temp_storage_path: Path, sample_url: str
    ) -> None:
        mock_extract = _make_subprocess_mock()
        with (
            patch("app.services.yt_dlp_service._extract_via_subprocess", mock_extract),
            patch("app.services.yt_dlp_service.os.path.isfile", return_value=True),
        ):
            result = await extract_media_url(sample_url, str(temp_storage_path))

            assert isinstance(result, tuple)
            assert len(result) == 3
            assert isinstance(result[0], str)
            assert isinstance(result[1], str)
            assert result[2] is None or isinstance(result[2], str)

    @pytest.mark.asyncio
    async def test_extract_media_url_creates_download_dir(
        self, temp_storage_path: Path, sample_url: str
    ) -> None:
        download_dir = temp_storage_path / "downloads"
        assert not download_dir.exists()

        mock_extract = _make_subprocess_mock()
        with (
            patch("app.services.yt_dlp_service._extract_via_subprocess", mock_extract),
            patch("app.services.yt_dlp_service.os.path.isfile", return_value=True),
        ):
            await extract_media_url(sample_url, str(temp_storage_path))
            assert download_dir.exists()

    @pytest.mark.asyncio
    async def test_extract_media_url_uses_uuid_filename(
        self, temp_storage_path: Path, sample_url: str
    ) -> None:
        mock_extract = _make_subprocess_mock()
        with (
            patch("app.services.yt_dlp_service._extract_via_subprocess", mock_extract),
            patch("app.services.yt_dlp_service.os.path.isfile", return_value=True),
        ):
            file_path, _, _ = await extract_media_url(sample_url, str(temp_storage_path))

            # file_path should contain a UUID, NOT the title
            file_id = Path(file_path).stem
            uuid.UUID(file_id)  # Should not raise

    @pytest.mark.asyncio
    async def test_extract_media_url_path_uses_only_uuid(
        self, temp_storage_path: Path, sample_url: str
    ) -> None:
        """Critical: file path must NOT contain the video title (prevents injection)."""
        mock_extract = _make_subprocess_mock(title="../../etc/passwd")
        with (
            patch("app.services.yt_dlp_service._extract_via_subprocess", mock_extract),
            patch("app.services.yt_dlp_service.os.path.isfile", return_value=True),
        ):
            file_path, file_name, _ = await extract_media_url(sample_url, str(temp_storage_path))

            # file_path must NOT contain path traversal
            assert "../../etc/passwd" not in file_path
            assert ".." not in file_path
            # file_name is sanitized (for display only)
            assert ".." not in file_name

    @pytest.mark.asyncio
    async def test_extract_media_url_file_extension(
        self, temp_storage_path: Path, sample_url: str
    ) -> None:
        mock_extract = _make_subprocess_mock(ext="webm")
        with (
            patch("app.services.yt_dlp_service._extract_via_subprocess", mock_extract),
            patch("app.services.yt_dlp_service.os.path.isfile", return_value=True),
        ):
            file_path, file_name, _ = await extract_media_url(sample_url, str(temp_storage_path))

            assert file_path.endswith(".webm")
            assert file_name.endswith(".webm")

    @pytest.mark.asyncio
    async def test_extract_media_url_fallback_extension(
        self, temp_storage_path: Path, sample_url: str
    ) -> None:
        mock_extract = _make_subprocess_mock(ext=None)
        with (
            patch("app.services.yt_dlp_service._extract_via_subprocess", mock_extract),
            patch("app.services.yt_dlp_service.os.path.isfile", return_value=True),
        ):
            file_path, file_name, _ = await extract_media_url(sample_url, str(temp_storage_path))

            assert file_path.endswith(".mp4")
            assert file_name.endswith(".mp4")

    @pytest.mark.asyncio
    async def test_extract_media_url_sanitizes_title_for_filename(
        self, temp_storage_path: Path, sample_url: str
    ) -> None:
        """Title in file_name is sanitized (display only)."""
        mock_extract = _make_subprocess_mock(title="My Cool Video")
        with (
            patch("app.services.yt_dlp_service._extract_via_subprocess", mock_extract),
            patch("app.services.yt_dlp_service.os.path.isfile", return_value=True),
        ):
            _, file_name, _ = await extract_media_url(sample_url, str(temp_storage_path))

            assert "My Cool Video" in file_name

    @pytest.mark.asyncio
    async def test_extract_media_url_yt_dlp_options(
        self, temp_storage_path: Path, sample_url: str
    ) -> None:
        """Verify the asyncio.create_subprocess_exec call uses correct options."""
        captured_calls: list = []

        async def mock_subprocess_exec(*args, **kwargs):
            captured_calls.append({"args": args, "kwargs": kwargs})
            return _make_process(stdout=[b'{"title": "Test", "ext": "mp4"}\n'])

        with (
            patch(
                "app.services.yt_dlp_service.asyncio.create_subprocess_exec", mock_subprocess_exec
            ),
            patch("app.services.yt_dlp_service._check_ssrf", new_callable=AsyncMock),
            patch("app.services.yt_dlp_service.os.path.isfile", return_value=True),
        ):
            await extract_media_url(sample_url, str(temp_storage_path))

            # Verify create_subprocess_exec was called
            assert len(captured_calls) == 1
            call_kwargs = captured_calls[0]["kwargs"]
            # Verify start_new_session=True is passed for proper process group handling
            assert call_kwargs.get("start_new_session") is True
            # Verify stdout and stderr pipes are configured
            assert call_kwargs.get("stdout") == asyncio.subprocess.PIPE
            assert call_kwargs.get("stderr") == asyncio.subprocess.PIPE

    @pytest.mark.asyncio
    async def test_extract_media_url_timeout_propagates(
        self, temp_storage_path: Path, sample_url: str
    ) -> None:
        """Verify asyncio.TimeoutError is raised when extraction times out."""

        call_count = 0
        real_wait_for = asyncio.wait_for

        async def mock_wait_for(coro, timeout=None):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                # First call (stream readers) times out
                _discard_awaitable(coro)
                raise TimeoutError("timed out")
            else:
                # Subsequent calls (cleanup: process.wait()) use real wait_for
                return await real_wait_for(coro, timeout=timeout)

        mock_process = _make_process(stdout=[b'{"title": "Test", "ext": "mp4"}\n'], pid=1234)

        async def mock_subprocess_exec(*args, **kwargs):
            return mock_process

        with (
            patch(
                "app.services.yt_dlp_service.asyncio.create_subprocess_exec", mock_subprocess_exec
            ),
            patch("app.services.yt_dlp_service._check_ssrf", new_callable=AsyncMock),
            patch("app.services.yt_dlp_service.asyncio.wait_for", mock_wait_for),
            patch("app.services.yt_dlp_service.os.getpgid", return_value=1234),
            patch("app.services.yt_dlp_service.os.killpg"),
        ):
            with pytest.raises(asyncio.TimeoutError):
                await extract_media_url(sample_url, str(temp_storage_path))

    @pytest.mark.asyncio
    async def test_extract_media_url_makedirs_failure(
        self, temp_storage_path: Path, sample_url: str
    ) -> None:
        """Verify StorageError is raised when download directory creation fails."""
        with (
            patch(
                "app.services.yt_dlp_service.os.makedirs", side_effect=OSError("Permission denied")
            ),
        ):
            with pytest.raises(StorageError) as exc_info:
                await extract_media_url(sample_url, str(temp_storage_path))
            assert "Failed to create download directory" in str(exc_info.value)

    @pytest.mark.asyncio
    async def test_extract_media_url_missing_output_file(
        self, temp_storage_path: Path, sample_url: str
    ) -> None:
        """Verify StorageError is raised when output file is not found."""
        mock_extract = _make_subprocess_mock()
        with (
            patch("app.services.yt_dlp_service._extract_via_subprocess", mock_extract),
            patch("app.services.yt_dlp_service.os.path.isfile", return_value=False),
        ):
            with pytest.raises(StorageError) as exc_info:
                await extract_media_url(sample_url, str(temp_storage_path))
            assert "Expected output file not found" in str(exc_info.value)

    @pytest.mark.asyncio
    async def test_extract_media_url_converts_path_validation_error_to_storage_error(
        self, temp_storage_path: Path, sample_url: str
    ) -> None:
        """Invalid output paths from the canonical validator are converted to StorageError."""
        mock_extract = _make_subprocess_mock()
        with (
            patch("app.services.yt_dlp_service._extract_via_subprocess", mock_extract),
            patch(
                "app.services.yt_dlp_service.validate_path",
                side_effect=ValueError("Path traversal detected"),
            ),
        ):
            with pytest.raises(StorageError) as exc_info:
                await extract_media_url(sample_url, str(temp_storage_path))

        assert "Path traversal detected" in str(exc_info.value)


# Helper functions for TestExtractViaSubprocessTimeoutHandling
def create_mock_wait_for_timeout_first_call():
    call_count = 0
    real_wait_for = asyncio.wait_for

    async def mock_wait_for(coro, timeout=None):
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            _discard_awaitable(coro)
            raise TimeoutError("extraction timed out")
        return await real_wait_for(coro, timeout=timeout)

    return mock_wait_for


def mock_killpg_raises_lookup_error(pgid, sig):
    raise ProcessLookupError(f"Process group {pgid} not found")


async def mock_subprocess_exec_returns_process(*args, **kwargs):
    return _make_process(pid=12345)


class TestExtractViaSubprocessTimeoutHandling:
    """Tests for TimeoutError handling in _extract_via_subprocess.

    The PR changed asyncio.TimeoutError → TimeoutError (bare built-in) in both
    the main except clause and the finally cleanup block. These tests verify the
    correct behaviour of both paths.
    """

    @pytest.mark.asyncio
    async def test_timeout_error_in_finally_cleanup_is_silenced(self) -> None:
        """When SIGKILL cleanup times out in the finally block, it must not propagate.

        The finally block catches TimeoutError from the second wait_for call and
        passes silently. A RuntimeError from the subprocess is expected to surface
        instead, not a TimeoutError from the cleanup.
        """
        from app.services.yt_dlp_service import _extract_via_subprocess

        call_count = 0
        real_wait_for = asyncio.wait_for

        async def mock_wait_for(coro, timeout=None):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                # First call: stream readers complete normally but with error exit
                return await real_wait_for(coro, timeout=timeout)
            else:
                # Subsequent cleanup calls (process.wait()) — simulate hung process
                _discard_awaitable(coro)
                raise TimeoutError("cleanup timed out")

        # None returncode triggers final cleanup after RuntimeError is raised.
        mock_process = _make_process(
            stdout=[],
            stderr=[b"process failed\n"],
            returncode=None,
            pid=99999,
        )

        async def mock_subprocess_exec(*args, **kwargs):
            return mock_process

        with (
            patch(
                "app.services.yt_dlp_service.asyncio.create_subprocess_exec",
                mock_subprocess_exec,
            ),
            patch("app.services.yt_dlp_service._check_ssrf", new_callable=AsyncMock),
            patch("app.services.yt_dlp_service.asyncio.wait_for", mock_wait_for),
            patch("app.services.yt_dlp_service.os.getpgid", return_value=99999),
            patch("app.services.yt_dlp_service.os.killpg"),
        ):
            # Should raise RuntimeError from yt-dlp failure, NOT TimeoutError from cleanup
            with pytest.raises(RuntimeError):
                await _extract_via_subprocess("https://www.youtube.com/watch?v=test", "/tmp/out")

    @pytest.mark.asyncio
    async def test_timeout_raises_timeout_error_not_asyncio_timeout_error(self) -> None:
        """TimeoutError (bare) is raised on extraction timeout — not a different type.

        In Python 3.11+ asyncio.TimeoutError is an alias for the built-in
        TimeoutError, but we explicitly verify the raised exception is a TimeoutError
        instance after the PR change.
        """
        from app.services.yt_dlp_service import _extract_via_subprocess

        call_count = 0
        real_wait_for = asyncio.wait_for

        async def mock_wait_for(coro, timeout=None):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                _discard_awaitable(coro)
                raise TimeoutError("extraction timed out")
            return await real_wait_for(coro, timeout=timeout)

        mock_process = _make_process(pid=12345)

        async def mock_subprocess_exec(*args, **kwargs):
            return mock_process

        with (
            patch(
                "app.services.yt_dlp_service.asyncio.create_subprocess_exec",
                mock_subprocess_exec,
            ),
            patch("app.services.yt_dlp_service._check_ssrf", new_callable=AsyncMock),
            patch("app.services.yt_dlp_service.asyncio.wait_for", mock_wait_for),
            patch("app.services.yt_dlp_service.os.getpgid", return_value=12345),
            patch("app.services.yt_dlp_service.os.killpg"),
        ):
            with pytest.raises(TimeoutError):
                await _extract_via_subprocess("https://www.youtube.com/watch?v=test", "/tmp/out")

    @pytest.mark.asyncio
    async def test_sigterm_sent_before_sigkill_on_timeout(self) -> None:
        """On extraction timeout, SIGTERM is sent first, then SIGKILL if needed.

        The except-TimeoutError block sends SIGTERM, waits, and escalates to
        SIGKILL only when SIGTERM does not terminate the process within 5 s.
        """
        import signal as _signal

        from app.services.yt_dlp_service import _extract_via_subprocess

        call_count = 0
        real_wait_for = asyncio.wait_for
        killed_with: list[int] = []

        async def mock_wait_for(coro, timeout=None):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                # Stream readers time out
                _discard_awaitable(coro)
                raise TimeoutError("timed out")
            elif call_count == 2:
                # First cleanup wait (after SIGTERM) also times out — forces SIGKILL
                _discard_awaitable(coro)
                raise TimeoutError("still running")
            else:
                # Final cleanup after SIGKILL succeeds
                return await real_wait_for(coro, timeout=timeout)

        def mock_killpg(pgid, sig):
            killed_with.append(sig)

        mock_process = _make_process(pid=55555)

        async def mock_subprocess_exec(*args, **kwargs):
            return mock_process

        with (
            patch(
                "app.services.yt_dlp_service.asyncio.create_subprocess_exec",
                mock_subprocess_exec,
            ),
            patch("app.services.yt_dlp_service._check_ssrf", new_callable=AsyncMock),
            patch("app.services.yt_dlp_service.asyncio.wait_for", mock_wait_for),
            patch("app.services.yt_dlp_service.os.getpgid", return_value=55555),
            patch("app.services.yt_dlp_service.os.killpg", mock_killpg),
        ):
            with pytest.raises(TimeoutError):
                await _extract_via_subprocess("https://www.youtube.com/watch?v=test", "/tmp/out")

        # SIGTERM must precede SIGKILL
        assert _signal.SIGTERM in killed_with, "Expected SIGTERM to be sent on timeout"
        assert _signal.SIGKILL in killed_with, (
            "Expected SIGKILL escalation when SIGTERM insufficient"
        )
        sigterm_idx = killed_with.index(_signal.SIGTERM)
        sigkill_idx = killed_with.index(_signal.SIGKILL)
        assert sigterm_idx < sigkill_idx, "SIGTERM must be sent before SIGKILL"

    @pytest.mark.asyncio
    async def test_process_lookup_error_on_killpg_is_handled(self) -> None:
        """When os.killpg raises ProcessLookupError, it must be silently handled.

        This can happen if the process group already terminated between the
        timeout detection and the kill attempt.
        """
        from app.services.yt_dlp_service import _extract_via_subprocess

        with (
            patch(
                "app.services.yt_dlp_service.asyncio.create_subprocess_exec",
                mock_subprocess_exec_returns_process,
            ),
            patch("app.services.yt_dlp_service._check_ssrf", new_callable=AsyncMock),
            patch(
                "app.services.yt_dlp_service.asyncio.wait_for",
                create_mock_wait_for_timeout_first_call(),
            ),
            patch("app.services.yt_dlp_service.os.getpgid", return_value=12345),
            patch("app.services.yt_dlp_service.os.killpg", mock_killpg_raises_lookup_error),
        ):
            with pytest.raises(TimeoutError):
                await _extract_via_subprocess("https://www.youtube.com/watch?v=test", "/tmp/out")


@pytest.mark.asyncio
async def test_process_lookup_error_on_killpg_in_finally_block() -> None:
    """When os.killpg raises ProcessLookupError in finally, it must be silently handled.

    This covers the finally block cleanup when process is still running but
    killpg fails because the process group already terminated.
    """
    from app.services.yt_dlp_service import _extract_via_subprocess

    mock_process = _make_process(returncode=None, pid=12345)

    async def mock_subprocess_exec(*args, **kwargs):
        return mock_process

    async def mock_wait_for(coro, timeout=None):
        return await coro

    def mock_killpg_raises(pgid, sig):
        raise ProcessLookupError(f"Process group {pgid} not found")

    with (
        patch(
            "app.services.yt_dlp_service.asyncio.create_subprocess_exec",
            mock_subprocess_exec,
        ),
        patch("app.services.yt_dlp_service._check_ssrf", new_callable=AsyncMock),
        patch("app.services.yt_dlp_service.asyncio.wait_for", mock_wait_for),
        patch("app.services.yt_dlp_service.os.killpg", mock_killpg_raises),
    ):
        result = await _extract_via_subprocess("https://www.youtube.com/watch?v=test", "/tmp/out")
        assert result == {"title": "T", "ext": "mp4"}


@pytest.mark.asyncio
async def test_error_payload_in_stdout_raises_runtime_error() -> None:
    """When yt-dlp returns JSON with 'error' key in stdout, raise RuntimeError."""
    from app.services.yt_dlp_service import _extract_via_subprocess

    mock_process = _make_process(
        stdout=[b'{"error": "Video unavailable"}\n'],
        returncode=1,
        pid=12345,
    )

    async def mock_subprocess_exec_2(*args, **kwargs):
        return mock_process

    with (
        patch(
            "app.services.yt_dlp_service.asyncio.create_subprocess_exec",
            mock_subprocess_exec_2,
        ),
        patch("app.services.yt_dlp_service._check_ssrf", new_callable=AsyncMock),
        patch("app.services.yt_dlp_service.os.killpg"),
    ):
        with pytest.raises(RuntimeError, match="yt-dlp extraction failed"):
            await _extract_via_subprocess("https://www.youtube.com/watch?v=test", "/tmp/out")


class TestFormatFallbackChain:
    """Tests for format fallback chain functionality in _extract_via_subprocess."""

    @pytest.fixture
    async def captured_script(self) -> str:
        """Capture the generated script from _extract_via_subprocess."""
        from app.services.yt_dlp_service import _extract_via_subprocess

        captured_scripts: list[str] = []

        async def capturing_subprocess_exec(*args, **kwargs):
            captured_scripts.append(args[2])
            return _make_process(pid=12345)

        with (
            patch(
                "app.services.yt_dlp_service.asyncio.create_subprocess_exec",
                capturing_subprocess_exec,
            ),
            patch("app.services.yt_dlp_service._check_ssrf", new_callable=AsyncMock),
        ):
            await _extract_via_subprocess("https://www.youtube.com/watch?v=test", "/tmp/out")

        return captured_scripts[0]

    @pytest.mark.asyncio
    async def test_format_fallback_chain_in_script(self, captured_script: str) -> None:
        """Verify the generated script encodes the full YouTube chain as a single
        native yt-dlp format string with '/' fallback (issue #169: previously a
        per-spec loop that only ever ran the first entry)."""
        # Every segment of the merged-combo chain must appear in the single format value.
        assert "bestvideo*+bestaudio" in captured_script
        assert "bestvideo+bestaudio" in captured_script
        assert "worstvideo*+bestaudio" in captured_script
        # The native '/' separators wire the whole chain as yt-dlp's own fallback.
        assert " / best" in captured_script
        assert " / worst" in captured_script
        # The old chain enshrined dead format-ID selectors — assert they're gone.
        assert "res:1080+h264" not in captured_script
        assert "res:720" not in captured_script
        # format_sort still biases toward 1080p/h264 on the first segment.
        assert '"res:1080"' in captured_script
        assert '"codec:h264"' in captured_script
        # The new model uses ONE extract_info call, not a per-spec Python loop.
        assert '"format"' in captured_script
        assert "for i, format_spec" not in captured_script

    @pytest.mark.asyncio
    async def test_prefer_free_formats_enabled(self, captured_script: str) -> None:
        """Verify prefer_free_formats is True in the yt-dlp options."""
        assert '"prefer_free_formats": True' in captured_script

    @pytest.mark.asyncio
    async def test_check_formats_missable(self, captured_script: str) -> None:
        """Verify check_formats is set to 'missable' in the yt-dlp options."""
        assert '"check_formats": "missable"' in captured_script

    @pytest.mark.asyncio
    async def test_extractor_args_player_clients(self, captured_script: str) -> None:
        """Verify all 4 player clients are included (tv, web, default, mobile)."""
        assert '"player_client": ["tv", "web", "default", "mobile"]' in captured_script

    @pytest.mark.asyncio
    async def test_format_unavailable_continues_to_next(self, captured_script: str) -> None:
        """The fallback chain is encoded as a single native yt-dlp format string
        whose '/' separators make yt-dlp degrade across the whole chain in one
        extract_info call (issue #169). There is no longer a per-format Python
        loop that only caught one narrow error string."""
        assert '"format":' in captured_script
        assert "bestvideo*+bestaudio" in captured_script
        assert " / best" in captured_script
        assert " / worst" in captured_script
        assert "res:1080+h264" not in captured_script
        assert "res:720" not in captured_script
        # Degradation is yt-dlp's responsibility now: no hand-rolled loop that
        # only continued on 'Requested format ... not available'.
        assert "for i, format_spec" not in captured_script
        assert '"Requested format" in err_str' not in captured_script


class TestGetPlatform:
    """Tests for _get_platform platform detection function."""

    def test_youtube_watch_url(self) -> None:
        assert _get_platform("https://www.youtube.com/watch?v=dQw4w9WgXcQ") == "youtube"

    def test_youtube_short_url(self) -> None:
        assert _get_platform("https://youtu.be/dQw4w9WgXcQ") == "youtube"

    def test_youtube_music_url(self) -> None:
        assert _get_platform("https://music.youtube.com/watch?v=abc") == "youtube"

    def test_youtube_nocookie_url(self) -> None:
        assert _get_platform("https://www.youtube-nocookie.com/watch?v=abc") == "youtube"

    def test_youtube_mobile_url(self) -> None:
        assert _get_platform("https://m.youtube.com/watch?v=abc") == "youtube"

    def test_vimeo_url(self) -> None:
        assert _get_platform("https://vimeo.com/76979871") == "vimeo"

    def test_dailymotion_url(self) -> None:
        assert _get_platform("https://www.dailymotion.com/video/x84sh87") == "dailymotion"

    def test_twitch_url(self) -> None:
        assert _get_platform("https://clips.twitch.tv/SmilingPluckySashimiBibleThump") == "twitch"

    def test_tiktok_url(self) -> None:
        assert (
            _get_platform("https://www.tiktok.com/@khaby.lame/video/7008477449723292934")
            == "tiktok"
        )

    def test_instagram_url(self) -> None:
        assert _get_platform("https://www.instagram.com/reel/DGcoPAktJAT/") == "instagram"

    def test_unknown_domain_defaults_to_youtube(self) -> None:
        assert _get_platform("https://example.com/video") == "youtube"

    def test_subdomain_bypass_rejected_for_youtube(self) -> None:
        """Exact domain matching prevents fake subdomains from matching."""
        assert _get_platform("https://youtube.com.evil.com/watch?v=abc") != "youtube"

    def test_subdomain_bypass_rejected_for_tiktok(self) -> None:
        assert _get_platform("https://tiktok.com.evil.com/video/123") != "tiktok"

    def test_empty_url_returns_youtube(self) -> None:
        assert _get_platform("not-a-url") == "youtube"


class TestPlatformFormatChains:
    """Tests verifying platform-specific format chains are routed correctly."""

    @pytest.fixture
    async def captured_script_tiktok(self) -> str:
        """Capture the generated script for a TikTok URL."""
        from app.services.yt_dlp_service import _extract_via_subprocess

        captured_scripts: list[str] = []

        async def capturing_subprocess_exec(*args, **kwargs):
            """
            Capture the subprocess script and return a mocked process.

            Returns:
                A mocked subprocess process with a fixed process ID.
            """
            captured_scripts.append(args[2])
            return _make_process(pid=12346)

        with (
            patch(
                "app.services.yt_dlp_service.asyncio.create_subprocess_exec",
                capturing_subprocess_exec,
            ),
            patch("app.services.yt_dlp_service._check_ssrf", new_callable=AsyncMock),
        ):
            await _extract_via_subprocess("https://www.tiktok.com/@test/video/123", "/tmp/out")

        return captured_scripts[0]

    @pytest.fixture
    async def captured_script_instagram(self) -> str:
        """Capture the generated script for an Instagram URL."""
        from app.services.yt_dlp_service import _extract_via_subprocess

        captured_scripts: list[str] = []

        async def capturing_subprocess_exec(*args, **kwargs):
            """
            Capture the subprocess script and return a mock process for testing.

            Returns:
                Mock subprocess process with PID 12347.
            """
            captured_scripts.append(args[2])
            return _make_process(pid=12347)

        with (
            patch(
                "app.services.yt_dlp_service.asyncio.create_subprocess_exec",
                capturing_subprocess_exec,
            ),
            patch("app.services.yt_dlp_service._check_ssrf", new_callable=AsyncMock),
        ):
            await _extract_via_subprocess("https://www.instagram.com/reel/test/", "/tmp/out")

        return captured_scripts[0]

    @pytest.mark.asyncio
    async def test_tiktok_excludes_youtube_specific_opts(self, captured_script_tiktok: str) -> None:
        """TikTok extraction must NOT include YouTube-only format options."""
        assert '"prefer_free_formats"' not in captured_script_tiktok
        assert '"check_formats"' not in captured_script_tiktok

    @pytest.mark.asyncio
    async def test_tiktok_excludes_youtube_player_clients(
        self, captured_script_tiktok: str
    ) -> None:
        """TikTok extraction must NOT include YouTube player_client extractor args."""
        assert '"player_client"' not in captured_script_tiktok

    @pytest.mark.asyncio
    async def test_tiktok_uses_simple_format_chain(self, captured_script_tiktok: str) -> None:
        """TikTok extraction uses simple best format, not the 5-entry YouTube chain."""
        assert '"bestvideo*+bestaudio/best"' not in captured_script_tiktok
        assert '"bestvideo+bestaudio/best"' not in captured_script_tiktok
        assert '"res:1080"' not in captured_script_tiktok
        assert '"best"' in captured_script_tiktok

    @pytest.mark.asyncio
    async def test_platform_in_error_message(self, captured_script_tiktok: str) -> None:
        """Failure message includes platform prefix like [tiktok]."""
        assert "[{platform}]" in captured_script_tiktok or "[tiktok]" in captured_script_tiktok

    @pytest.mark.asyncio
    async def test_instagram_excludes_youtube_specific_opts(
        self, captured_script_instagram: str
    ) -> None:
        """Instagram extraction must NOT include YouTube-only format options."""
        assert '"prefer_free_formats"' not in captured_script_instagram
        assert '"check_formats"' not in captured_script_instagram

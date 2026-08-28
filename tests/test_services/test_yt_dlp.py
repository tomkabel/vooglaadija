"""yt_dlp service tests."""

from __future__ import annotations

import asyncio
import signal
import tempfile
import uuid
from collections.abc import Generator
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

import app.services.yt_dlp_service as yt_dlp_service
from app.services.yt_dlp_service import (
    YtDlpProcessPool,
    _WorkerJobError,
    _build_extract_request,
    _extract_via_subprocess,
    _get_platform,
    _terminate_worker_on_timeout,
    extract_media_url,
    reset_yt_dlp_pool,
)
from app.services.yt_dlp_worker import make_ydl_opts
from app.utils.exceptions import StorageError
from app.utils.validators import is_youtube_url


@pytest.fixture(autouse=True)
async def _reset_pool() -> Generator[None, None, None]:
    """Ensure the module-level worker pool is fresh for every test."""
    await reset_yt_dlp_pool()
    yield
    await reset_yt_dlp_pool()


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


class _HangStream:
    """Async stream that never yields, to simulate a hung worker read."""

    def __aiter__(self):
        return self

    async def __anext__(self) -> bytes:
        await asyncio.sleep(1000)


def _make_process(
    *,
    stdout: list[bytes] | None = None,
    stderr: list[bytes] | None = None,
    returncode: int | None = 0,
    pid: int = 12345,
) -> AsyncMock:
    """Create a subprocess mock for the streaming extraction implementation."""
    mock_process = AsyncMock()
    stdout_lines = (
        stdout if stdout is not None else [b'{"title": "T", "ext": "mp4", "_stderr": ""}\n']
    )
    stderr_lines = stderr if stderr is not None else []
    mock_process.stdout = _AsyncLineStream(stdout_lines)
    mock_process.stderr = _AsyncLineStream(stderr_lines)
    mock_process.returncode = returncode
    mock_process.pid = pid
    mock_process.wait = AsyncMock(return_value=0 if returncode is None else returncode)
    stdin = MagicMock()
    stdin.write = MagicMock()
    stdin.drain = AsyncMock()
    mock_process.stdin = stdin
    return mock_process


def _make_fake_worker(
    stdout_lines: list[bytes] | None = None,
    *,
    pid: int = 12345,
    hang: bool = False,
    returncode: int | None = None,
    process: AsyncMock | None = None,
) -> object:
    """Build a minimal worker-shaped object for YtDlpProcessPool._run_on_worker."""

    class _FakeWorker:
        pass

    worker = _FakeWorker()
    worker.stdout = _HangStream() if hang else _AsyncLineStream(stdout_lines or [])
    stdin = MagicMock()
    stdin.write = MagicMock()
    stdin.drain = AsyncMock()
    worker.stdin = stdin
    if process is None:
        process = AsyncMock()
        process.pid = pid
        process.returncode = returncode
        process.wait = AsyncMock(return_value=0)
    worker.process = process
    return worker


def _discard_awaitable(awaitable) -> None:
    """Prevent un-awaited coroutine/future warnings when mocked wait_for times out."""
    if hasattr(awaitable, "cancel"):
        awaitable.cancel()
    elif hasattr(awaitable, "close"):
        awaitable.close()


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
        self, temp_storage_path: Path, sample_url: str, monkeypatch
    ) -> None:
        """Verify the worker pool spawns persistent workers with correct options.

        The pool lazily spawns one long-lived worker per concurrency slot using
        ``python <yt_dlp_worker.py>`` (not a per-call ``python -c`` script).
        """
        # Force a single-worker pool so exactly one spawn occurs.
        monkeypatch.setattr(yt_dlp_service, "_EXTRACTION_CONCURRENCY", 1)
        await reset_yt_dlp_pool()

        captured_calls: list = []
        captured_procs: list = []

        def mock_subprocess_exec(*args, **kwargs):
            captured_calls.append({"args": args, "kwargs": kwargs})
            proc = _make_process(
                stdout=[b'{"title": "Test", "ext": "mp4", "_stderr": ""}\n'],
                returncode=None,
            )
            captured_procs.append(proc)
            return proc

        with (
            patch(
                "app.services.yt_dlp_service.asyncio.create_subprocess_exec",
                mock_subprocess_exec,
            ),
            patch("app.services.yt_dlp_service._check_ssrf", new_callable=AsyncMock),
            patch("app.services.yt_dlp_service.os.path.isfile", return_value=True),
        ):
            await extract_media_url(sample_url, str(temp_storage_path))

            # One worker spawned for the single-slot pool.
            assert len(captured_calls) == 1
            call_kwargs = captured_calls[0]["kwargs"]
            # Persistent worker is launched as a dedicated script file.
            assert call_kwargs.get("start_new_session") is True
            assert call_kwargs.get("stdout") == asyncio.subprocess.PIPE
            # stderr is redirected to DEVNULL so the persistent worker never
            # blocks on a full pipe; per-job diagnostics come back via stdout.
            assert call_kwargs.get("stderr") == asyncio.subprocess.DEVNULL
            args = captured_calls[0]["args"]
            assert args[0] == yt_dlp_service.sys.executable
            assert args[1].endswith("yt_dlp_worker.py")

            # The request payload written to the worker contains the format chain.
            import json

            written = captured_procs[0].stdin.write.call_args.args[0]
            written_text = written.decode() if isinstance(written, bytes) else written
            request = json.loads(written_text)
            assert request["url"] == sample_url
            assert "fallback_chain" in request
            assert request["youtube_opts"] is True

    @pytest.mark.asyncio
    async def test_extract_media_url_makedirs_failure(
        self, temp_storage_path: Path, sample_url: str
    ) -> None:
        """Verify StorageError is raised when download directory creation fails."""
        with (
            patch(
                "app.services.yt_dlp_service.os.makedirs",
                side_effect=OSError("Permission denied"),
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


class TestBuildExtractRequest:
    """Tests for the per-job request payload built for pooled workers."""

    def test_youtube_request_contains_full_chain(self) -> None:
        req = _build_extract_request("https://www.youtube.com/watch?v=abc", "/tmp/out")
        assert req["url"] == "https://www.youtube.com/watch?v=abc"
        assert req["output_template"] == "/tmp/out"
        assert req["platform"] == "youtube"
        assert req["youtube_opts"] is True
        assert req["fallback_chain"] == yt_dlp_service.FORMAT_FALLBACK_CHAIN
        assert req["extractor_args"] == {
            "youtube": {"player_client": ["tv", "web", "default", "mobile"]}
        }
        assert set(req["output_fields"]) == yt_dlp_service._OUTPUT_FIELDS

    def test_tiktok_request_excludes_youtube_opts(self) -> None:
        req = _build_extract_request("https://www.tiktok.com/@u/video/1", "/tmp/out")
        assert req["platform"] == "tiktok"
        assert req["youtube_opts"] is False
        assert req["extractor_args"] == {}
        assert req["fallback_chain"] == yt_dlp_service._GENERIC_FORMAT_CHAIN
        # YouTube-only format options must not be present.
        assert "bestvideo*+bestaudio/best" not in req["fallback_chain"][0]["format"]

    def test_instagram_request_excludes_youtube_opts(self) -> None:
        req = _build_extract_request("https://www.instagram.com/reel/test/", "/tmp/out")
        assert req["platform"] == "instagram"
        assert req["youtube_opts"] is False
        assert req["extractor_args"] == {}

    def test_request_is_json_serializable(self) -> None:
        import json

        req = _build_extract_request("https://example.com/video", "/tmp/out")
        # Demonstrates the request is sent over the worker pipe as a JSON line.
        assert json.loads(json.dumps(req)) == req


class TestWorkerMakeYdlOpts:
    """Tests for the yt-dlp option builder used inside the persistent worker."""

    def test_youtube_opts_include_prefer_free_formats(self) -> None:
        req = _build_extract_request("https://www.youtube.com/watch?v=abc", "/tmp/out")
        opts = make_ydl_opts(req, req["fallback_chain"][0])
        assert opts["prefer_free_formats"] is True
        assert opts["check_formats"] == "missable"
        assert opts["extractor_args"] == {
            "youtube": {"player_client": ["tv", "web", "default", "mobile"]}
        }

    def test_youtube_opts_format_chain_entries(self) -> None:
        req = _build_extract_request("https://www.youtube.com/watch?v=abc", "/tmp/out")
        opts = make_ydl_opts(req, req["fallback_chain"][0])
        assert "res:1080" in opts["format_sort"]
        assert "codec:h264" in opts["format_sort"]

    def test_non_youtube_opts_exclude_youtube_only(self) -> None:
        req = _build_extract_request("https://www.tiktok.com/@u/video/1", "/tmp/out")
        opts = make_ydl_opts(req, req["fallback_chain"][0])
        assert "prefer_free_formats" not in opts
        assert "check_formats" not in opts
        assert "extractor_args" not in opts

    def test_cookiesfrombrowser_list_becomes_tuple(self) -> None:
        req = {
            "output_template": "/tmp/out",
            "youtube_opts": False,
            "cookies_opts": {"cookiesfrombrowser": ["chrome"]},
            "extractor_args": {},
        }
        opts = make_ydl_opts(req, {"format": "best", "format_sort": ["quality"]})
        assert opts["cookiesfrombrowser"] == ("chrome",)


class TestPoolRunOnWorker:
    """Tests for YtDlpProcessPool._run_on_worker against the worker protocol."""

    @pytest.mark.asyncio
    async def test_success_returns_result_and_strips_stderr(self) -> None:
        worker = _make_fake_worker(
            [b'{"title": "T", "ext": "mp4", "_stderr": "some warn"}\n']
        )
        pool = YtDlpProcessPool(size=1, timeout=5)
        result, stderr = await pool._run_on_worker(worker, {"url": "x"}, None)
        assert result == {"title": "T", "ext": "mp4"}
        assert stderr == "some warn"

    @pytest.mark.asyncio
    async def test_error_line_raises_runtime_error(self) -> None:
        worker = _make_fake_worker([b'{"error": "Video unavailable", "_stderr": ""}\n'])
        pool = YtDlpProcessPool(size=1, timeout=5)
        with pytest.raises(RuntimeError, match="yt-dlp extraction failed"):
            await pool._run_on_worker(worker, {"url": "x"}, None)

    @pytest.mark.asyncio
    async def test_progress_lines_forwarded_to_callback(self) -> None:
        lines = [
            b'{"progress": true, "percent": 10.0}\n',
            b'{"progress": true, "percent": 20.0}\n',
            b'{"title": "T", "ext": "mp4", "_stderr": ""}\n',
        ]
        worker = _make_fake_worker(lines)
        pool = YtDlpProcessPool(size=1, timeout=5)
        received: list = []

        async def cb(d):
            received.append(d)

        await pool._run_on_worker(worker, {"url": "x"}, cb)
        assert [p["percent"] for p in received] == [10.0, 20.0]

    @pytest.mark.asyncio
    async def test_worker_death_without_output_raises(self) -> None:
        worker = _make_fake_worker([], returncode=1)
        pool = YtDlpProcessPool(size=1, timeout=5)
        with pytest.raises(RuntimeError, match="without producing a result"):
            await pool._run_on_worker(worker, {"url": "x"}, None)

    @pytest.mark.asyncio
    async def test_timeout_kills_worker_and_raises(self) -> None:
        worker = _make_fake_worker([], hang=True, pid=55555)
        pool = YtDlpProcessPool(size=1, timeout=0.05)

        killed: list = []

        def fake_wait_for(coro, timeout=None):
            _discard_awaitable(coro)
            raise TimeoutError("timed out")

        def fake_killpg(pgid, sig):
            killed.append(sig)

        with (
            patch("app.services.yt_dlp_service.os.getpgid", return_value=55555),
            patch("app.services.yt_dlp_service.os.killpg", fake_killpg),
            patch("asyncio.wait_for", fake_wait_for),
        ):
            with pytest.raises(TimeoutError):
                await pool._run_on_worker(worker, {"url": "x"}, None)

        # The timed-out worker's process group must be killed.
        assert signal.SIGTERM in killed


class TestPoolResilience:
    """Regression tests for the four review-comment fixes on PR #165.

    Covers: no worker leak on routine extraction errors, throttle detection on
    the failure path, fail-fast when the pool shrinks, and idempotent startup.
    """

    @pytest.mark.asyncio
    async def test_healthy_worker_reused_after_extraction_error(self) -> None:
        """A routine error result must NOT leak the worker process.

        CRITICAL fix: previously the error-result path marked the (still-alive)
        worker dead and respawned a replacement, leaking a full ``yt_dlp``
        process (~50-100MB) on every failed extraction. The healthy worker
        must be returned to the pool and reused.
        """
        spawn_calls = {"n": 0}

        async def fake_spawn():
            spawn_calls["n"] += 1
            return _make_fake_worker(
                [b'{"error": "Video unavailable", "_stderr": "some diag"}\n']
            )

        pool = YtDlpProcessPool(size=1, timeout=5)
        pool._spawn_worker = fake_spawn
        await pool._ensure_started()
        assert spawn_calls["n"] == 1

        with pytest.raises(_WorkerJobError):
            await pool.submit({"url": "x"}, None)

        # Same worker returned to the pool (not respawned) -> no leak.
        assert not pool._free.empty()
        assert len(pool._workers) == 1
        assert spawn_calls["n"] == 1

    @pytest.mark.asyncio
    async def test_submit_fails_fast_when_pool_has_no_workers(self) -> None:
        """WARNING fix: a shrunk-to-zero pool must fail fast, not hang forever.

        When every respawn failed, ``_free.get()`` would block indefinitely
        because the semaphore still admits concurrent jobs. It must raise.
        """
        pool = YtDlpProcessPool(size=1, timeout=5)
        pool._workers = []  # simulate: nothing could be started
        pool._started = True

        with pytest.raises(RuntimeError, match="no workers could be started"):
            await pool.submit({"url": "x"}, None)

    @pytest.mark.asyncio
    async def test_submit_fails_fast_when_capacity_exceeded(self) -> None:
        """WARNING fix: a request beyond the live-worker capacity fails fast."""
        pool = YtDlpProcessPool(size=1, timeout=5)
        # One worker checked out and in flight, free queue empty.
        pool._workers = [_make_fake_worker([b"{}"]) ]
        pool._checked_out = 1
        pool._started = True  # skip (real) startup; we only exercise the checkout guard
        pool._free._queue.clear()

        with pytest.raises(RuntimeError, match="no available worker"):
            await pool.submit({"url": "x"}, None)

    @pytest.mark.asyncio
    async def test_ensure_started_idempotent_after_partial_failure(self) -> None:
        """SUGGESTION fix: a partial spawn failure must not deadlock on retry.

        Previously a midway spawn failure left workers 1..k-1 queued, then the
        next ``_ensure_started`` re-spawned all N and overflowed the bounded
        queue (maxsize == N), deadlocking every subsequent submit.
        """
        pool = YtDlpProcessPool(size=3, timeout=5)
        order = {"n": 0}

        async def flaky_spawn():
            order["n"] += 1
            if order["n"] == 2:
                raise OSError("transient spawn failure")
            return _make_fake_worker([b'{"title": "T", "ext": "mp4", "_stderr": ""}\n'])

        pool._spawn_worker = flaky_spawn
        await pool._ensure_started()
        assert len(pool._workers) == 1
        assert pool._started is False

        order["n"] = 0

        async def ok_spawn():
            order["n"] += 1
            return _make_fake_worker([b'{"title": "T", "ext": "mp4", "_stderr": ""}\n'])

        pool._spawn_worker = ok_spawn
        # Must complete (no deadlock) and top up only the 2 missing slots.
        await pool._ensure_started()
        assert len(pool._workers) == 3
        assert order["n"] == 2
        assert pool._started is True
        assert pool._free.qsize() == 3


class TestTerminateWorkerOnTimeout:
    """Tests for the timeout kill semantics (SIGTERM -> SIGKILL + orphan walk)."""

    @pytest.mark.asyncio
    async def test_sigterm_before_sigkill(self) -> None:
        killed: list = []
        proc = AsyncMock()
        proc.pid = 55555
        proc.wait = AsyncMock(return_value=0)

        real_wait_for = asyncio.wait_for
        wait_calls = {"n": 0}

        def fake_wait_for(coro, timeout=None):
            wait_calls["n"] += 1
            if wait_calls["n"] == 1:
                # After SIGTERM the (mock) process is still alive -> escalate.
                _discard_awaitable(coro)
                raise TimeoutError("still running")
            return real_wait_for(coro, timeout=timeout)

        def fake_killpg(pgid, sig):
            killed.append(sig)

        with (
            patch("app.services.yt_dlp_service.os.getpgid", return_value=55555),
            patch("app.services.yt_dlp_service.os.killpg", fake_killpg),
            patch("asyncio.wait_for", fake_wait_for),
        ):
            await _terminate_worker_on_timeout(proc)

        assert signal.SIGTERM in killed
        assert signal.SIGKILL in killed
        assert killed.index(signal.SIGTERM) < killed.index(signal.SIGKILL)

    @pytest.mark.asyncio
    async def test_killpg_process_lookup_error_is_silenced(self) -> None:
        proc = AsyncMock()
        proc.pid = 12345
        proc.wait = AsyncMock(return_value=0)

        def fake_killpg(pgid, sig):
            raise ProcessLookupError(f"Process group {pgid} not found")

        with (
            patch("app.services.yt_dlp_service.os.getpgid", return_value=12345),
            patch("app.services.yt_dlp_service.os.killpg", fake_killpg),
        ):
            # Must not raise despite killpg failing.
            await _terminate_worker_on_timeout(proc)


class TestExtractViaSubprocessTimeout:
    """End-to-end timeout behaviour through _extract_via_subprocess + the pool."""

    @pytest.mark.asyncio
    async def test_extract_via_subprocess_timeout_propagates(self, monkeypatch) -> None:
        monkeypatch.setattr(yt_dlp_service, "_EXTRACTION_CONCURRENCY", 1)
        await reset_yt_dlp_pool()

        killed: list = []

        def mock_subprocess_exec(*args, **kwargs):
            proc = AsyncMock()
            proc.stdout = _HangStream()
            stdin = MagicMock()
            stdin.write = MagicMock()
            stdin.drain = AsyncMock()
            proc.stdin = stdin
            proc.stderr = None
            proc.pid = 55555
            proc.returncode = None
            proc.wait = AsyncMock(return_value=0)
            return proc

        def fake_wait_for(coro, timeout=None):
            _discard_awaitable(coro)
            raise TimeoutError("timed out")

        def fake_killpg(pgid, sig):
            killed.append(sig)

        with (
            patch(
                "app.services.yt_dlp_service.asyncio.create_subprocess_exec",
                mock_subprocess_exec,
            ),
            patch("app.services.yt_dlp_service._check_ssrf", new_callable=AsyncMock),
            patch("app.services.yt_dlp_service.os.getpgid", return_value=55555),
            patch("app.services.yt_dlp_service.os.killpg", fake_killpg),
            patch("asyncio.wait_for", fake_wait_for),
        ):
            with pytest.raises(TimeoutError):
                await _extract_via_subprocess(
                    "https://www.youtube.com/watch?v=test", "/tmp/out"
                )

        assert signal.SIGTERM in killed


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
        assert (
            _get_platform("https://clips.twitch.tv/SmilingPluckySashimiBibleThump") == "twitch"
        )

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

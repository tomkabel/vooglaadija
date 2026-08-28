import asyncio
import json
import os
import re
import signal
import sys
import uuid
from collections.abc import Awaitable, Callable
from typing import Any
from urllib.parse import urlparse

from app.utils.exceptions import StorageError
from app.utils.validators import validate_url_not_ssrf
from core.logging_config import get_logger
from core.utils.security import validate_path

logger = get_logger(__name__)

# Timeout for yt-dlp operations in seconds (5 minutes)
YT_DLP_TIMEOUT = 300

# Timeout for lightweight metadata-only extraction (no download)
YT_DLP_METADATA_TIMEOUT = 15

THROTTLE_PATTERN = re.compile(r"HTTP Error 429", re.IGNORECASE)

FORMAT_FALLBACK_CHAIN = [
    {"format": "bestvideo*+bestaudio/best", "format_sort": ["res:1080", "codec:h264"]},
    {"format": "bestvideo+bestaudio/best", "format_sort": ["res", "codec"]},
    {"format": "worstvideo*+bestaudio/best", "format_sort": ["res:720"]},
    {"format": "best", "format_sort": ["quality"]},
    {"format": "worst", "format_sort": ["quality"]},
]

# Max bytes for a single stdout line from the yt-dlp subprocess.
# YouTube video metadata JSON can exceed the default 64KB StreamReader limit
# when a video has many format variants, causing LimitOverrunError.
_STREAM_READER_LIMIT = 1024 * 1024  # 1MB

# Fields to extract from the yt-dlp sanitized_info dict.
# Sending the full sanitized_info over stdout can exceed pipe buffer limits.
_OUTPUT_FIELDS = frozenset({"title", "ext", "duration", "webpage_url", "thumbnail"})

# Semaphore to limit concurrent yt-dlp subprocesses for metadata resolution.
# Each subprocess uses ~50-100MB RAM — 5 concurrent avoids OOM on constrained hosts.
_metadata_semaphore = asyncio.Semaphore(5)

# Semaphore to limit concurrent yt-dlp extraction subprocesses (download + format negotiation).
# Each extraction subprocess consumes ~50-100MB RAM during format negotiation and download
# — 2 concurrent avoids OOM on constrained hosts.
# Configurable via YT_DLP_EXTRACTION_CONCURRENCY env var (default 2).
_EXTRACTION_CONCURRENCY = max(1, int(os.environ.get("YT_DLP_EXTRACTION_CONCURRENCY", "2")))
_EXTRACTION_SEMAPHORE = asyncio.Semaphore(_EXTRACTION_CONCURRENCY)


class SSRFError(Exception):
    """Raised when a URL resolves to a private/internal IP address."""


async def _check_ssrf(url: str) -> None:
    """Validate URL does not resolve to a private IP (SSRF prevention).

    Raises SSRFError if the URL resolves to an RFC 1918, loopback, link-local,
    or other private address range.
    """
    if not await validate_url_not_ssrf(url):
        parsed = urlparse(url)
        hostname = parsed.hostname or ""
        raise SSRFError(f"URL resolves to a private or internal address: {hostname}")


async def resolve_video_title(url: str) -> str | None:
    """
    Resolve a video title from a URL without downloading the media.

    Parameters:
        url (str): Video URL to inspect.

    Returns:
        str | None: The extracted title, or `None` if the URL is blocked or metadata extraction fails.
    """
    try:
        await _check_ssrf(url)
    except SSRFError:
        logger.warning("ssrf_blocked_metadata", url=url[:80])
        return None

    url_json = json.dumps(url)
    platform = _get_platform(url)
    cookies_opts = _build_cookies_opts()
    cookies_opts_json = json.dumps(cookies_opts)

    if platform in _COOKIE_REQUIRED_PLATFORMS and not cookies_opts:
        logger.info(
            "metadata_without_cookies",
            platform=platform,
            url=url[:80],
            hint="Set YT_DLP_COOKIES_FILE or YT_DLP_COOKIES_BROWSER to enable cookies for this platform",
        )

    script = f"""
import sys
import json
import yt_dlp
url = {url_json}
cookies_opts = {cookies_opts_json}
if "cookiesfrombrowser" in cookies_opts and isinstance(cookies_opts["cookiesfrombrowser"], list):
    cookies_opts["cookiesfrombrowser"] = tuple(cookies_opts["cookiesfrombrowser"])
try:
    ydl_opts = {{
        "quiet": True,
        "no_warnings": True,
        "noprogress": True,
        "socket_timeout": 10,
        "retries": 1,
    }}
    ydl_opts.update(cookies_opts)
    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        info = ydl.extract_info(url, download=False)
        sanitized = ydl.sanitize_info(info)
        title = sanitized.get("title")
        if title:
            print(json.dumps({{"title": title}}))
            sys.exit(0)
        print(json.dumps({{"error": "no_title"}}))
        sys.exit(1)
except Exception as e:
    print(json.dumps({{"error": str(e)}}))
    sys.exit(1)
"""
    process = None
    try:
        async with _metadata_semaphore:
            process = await asyncio.create_subprocess_exec(
                sys.executable,
                "-c",
                script,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                start_new_session=True,
            )

            stdout_bytes, _ = await asyncio.wait_for(
                process.communicate(),
                timeout=YT_DLP_METADATA_TIMEOUT,
            )

        if process.returncode != 0:
            return None

        result: dict = json.loads(stdout_bytes.decode().strip())
        raw = result.get("title")
        if isinstance(raw, str) and raw:
            return raw
        return None

    except TimeoutError:
        logger.warning("metadata_extraction_timeout", url=url[:80])
        return None
    except json.JSONDecodeError:
        logger.debug("metadata_json_parse_failed", url=url[:80], exc_info=True)
        return None
    except OSError:
        logger.warning("metadata_subprocess_failed", url=url[:80], exc_info=True)
        return None
    finally:
        if process and process.returncode is None:
            await _kill_process_group(process, graceful=True)


async def _kill_process_group(process: asyncio.subprocess.Process, graceful: bool = True) -> None:
    """Kill a process group with SIGTERM grace window before SIGKILL.

    Args:
        process: The subprocess to terminate.
        graceful: If True, sends SIGTERM first and waits 5s for clean shutdown.
                  If False, skips straight to SIGKILL (for use when SIGTERM
                  was already sent by the caller, e.g. the timeout handler).

    """
    if process.returncode is not None:
        return

    try:
        if graceful:
            os.killpg(process.pid, signal.SIGTERM)
            try:
                await asyncio.wait_for(process.wait(), timeout=5)
                return
            except TimeoutError:
                pass

        os.killpg(process.pid, signal.SIGKILL)
        try:
            await asyncio.wait_for(process.wait(), timeout=5)
        except TimeoutError:
            pass
    except (ProcessLookupError, OSError):
        pass


async def _walk_and_kill_orphaned_children(process: asyncio.subprocess.Process) -> None:
    """Walk /proc for child processes that detached from the process group.

    yt-dlp may spawn ffmpeg with its own session/process group via
    preexec_fn=os.setpgrp. These children won't be reached by killpg.
    This walks /proc/{pid}/task/{pid}/children to find and kill them.

    Best-effort; silent on failure.
    """
    try:
        children_path = f"/proc/{process.pid}/task/{process.pid}/children"
        children_text: str | None = None
        try:
            children_text = await asyncio.get_running_loop().run_in_executor(
                None,
                lambda: open(children_path).read(),
            )
        except (FileNotFoundError, PermissionError, ProcessLookupError, OSError):
            return

        if not children_text:
            return

        for child_pid_str in children_text.strip().split():
            try:
                child_pid = int(child_pid_str.strip())
                os.kill(child_pid, signal.SIGKILL)
            except (ValueError, ProcessLookupError, OSError):
                pass
    except (FileNotFoundError, PermissionError, ProcessLookupError, OSError):
        pass


async def _terminate_worker_on_timeout(process: asyncio.subprocess.Process) -> None:
    """Kill a timed-out worker's process group, preserving original semantics.

    Mirrors the timeout-kill sequence previously applied to the per-call
    extraction subprocess: broadcast SIGTERM (and SIGCONT) to the process
    group, wait briefly for a clean exit, walk /proc for ffmpeg children that
    detached from the group, then escalate to SIGKILL.
    """
    try:
        pgid = os.getpgid(process.pid)
    except (ProcessLookupError, OSError):
        pgid = process.pid

    for sig in (signal.SIGTERM, signal.SIGCONT):
        try:
            os.killpg(pgid, sig)
        except (ProcessLookupError, OSError):
            pass

    try:
        await asyncio.wait_for(process.wait(), timeout=3)
    except TimeoutError:
        pass

    # Walk /proc for survivors — catches ffmpeg that detached from process group
    await _walk_and_kill_orphaned_children(process)

    # Final escalation: SIGKILL the process group
    try:
        os.killpg(pgid, signal.SIGKILL)
    except (ProcessLookupError, OSError):
        pass

    try:
        await asyncio.wait_for(process.wait(), timeout=3)
    except TimeoutError:
        pass


class _WorkerDead(Exception):
    """Raised when a worker slot's subprocess is no longer usable."""


class _WorkerJobError(RuntimeError):
    """Raised when a worker completes a job but reports an extraction failure.

    The worker process is still alive and reusable; only the job itself failed.
    The captured per-job stderr is retained so callers can still run throttle
    detection on the failure path (where 429s are most likely to appear).
    """

    def __init__(self, message: str, stderr: str = "") -> None:
        super().__init__(message)
        self.stderr = stderr


class _Worker:
    """A single persistent yt-dlp worker subprocess and its IO handles."""

    __slots__ = ("process", "stdin", "stdout")

    def __init__(
        self,
        process: asyncio.subprocess.Process,
        stdin: asyncio.StreamWriter,
        stdout: asyncio.StreamReader,
    ) -> None:
        self.process = process
        self.stdin = stdin
        self.stdout = stdout


class YtDlpProcessPool:
    """A pool of persistent yt-dlp worker subprocesses.

    Each worker imports ``yt_dlp`` once and serves extraction requests over
    stdin/stdout, eliminating the per-job import + extractor-init cold start.
    One worker is allocated per extraction concurrency slot; workers are reused
    across jobs. On a per-job timeout the offending worker is killed (using the
    same process-group SIGTERM -> SIGKILL + orphan-walk semantics as before)
    and immediately respawned so the pool stays at full strength.
    """

    def __init__(self, size: int, timeout: int) -> None:
        self._size = max(1, int(size))
        self._timeout = timeout
        self._workers: list[_Worker] = []
        self._free: asyncio.Queue[_Worker] = asyncio.Queue(maxsize=self._size)
        self._started = False
        self._lock = asyncio.Lock()

    async def _ensure_started(self) -> None:
        async with self._lock:
            if self._started:
                return
            # Idempotent: only spawn the slots that are still missing. A previous
            # attempt that failed midway leaves workers 1..k-1 in ``_workers`` and
            # ``_free``, so we top up only the remainder rather than re-spawning the
            # whole set — re-spawning all of them would overflow the bounded queue
            # (maxsize == size) and permanently deadlock every subsequent submit.
            needed = self._size - len(self._workers)
            for _ in range(needed):
                try:
                    worker = await self._spawn_worker()
                except Exception:
                    logger.warning("yt_dlp_worker_spawn_failed", exc_info=True)
                    # Leave ``_started`` False so the next submit retries the
                    # still-missing slots instead of declaring victory early.
                    break
                self._workers.append(worker)
                try:
                    await self._free.put(worker)
                except asyncio.QueueFull:
                    break
            if len(self._workers) == self._size:
                self._started = True

    async def _spawn_worker(self) -> _Worker:
        worker_path = os.path.join(os.path.dirname(__file__), "yt_dlp_worker.py")
        process = await asyncio.create_subprocess_exec(
            sys.executable,
            worker_path,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
            start_new_session=True,
            limit=_STREAM_READER_LIMIT,
        )
        assert process.stdin is not None
        assert process.stdout is not None
        return _Worker(process, process.stdin, process.stdout)

    async def submit(
        self,
        request: dict,
        progress_callback: Callable[[dict], Awaitable[None]] | None = None,
    ) -> tuple[dict, str]:
        """Run one extraction request on a pooled worker.

        Returns ``(result, stderr_text)`` on success. Raises ``TimeoutError`` if
        the worker does not respond within the pool timeout, ``_WorkerJobError``
        (a ``RuntimeError`` subclass) if the worker reports an extraction
        failure while remaining reusable, or ``_WorkerDead`` if the worker
        subprocess itself is gone.

        A *healthy* worker is always returned to the pool — only a genuinely
        dead/timeout worker is killed and respawned. This prevents leaking a
        full ``yt_dlp`` process on every routine extraction error (e.g. a
        private/age-restricted video), which previously accumulated without
        bound until OOM.
        """
        await self._ensure_started()
        # Fail fast instead of hanging forever: if the pool shrank to zero usable
        # workers there is nothing to wait for.
        if not self._workers:
            raise RuntimeError(
                "yt-dlp worker pool is unavailable: no workers could be started"
            )
        try:
            worker = await asyncio.wait_for(self._free.get(), timeout=self._timeout)
        except TimeoutError as exc:
            raise RuntimeError(
                "yt-dlp worker pool has no available worker; request timed out "
                "waiting for a free slot"
            ) from exc

        worker_dead = False
        try:
            return await self._run_on_worker(worker, request, progress_callback)
        except _WorkerDead:
            # Worker process is genuinely gone — must be replaced.
            worker_dead = True
            raise
        except Exception:
            # Job-level failure (e.g. ``_WorkerJobError``): the worker process is
            # still alive and reusable, so propagate without respawning.
            raise
        finally:
            if worker_dead or worker.process.returncode is not None:
                await self._respawn_into_free(worker)
            elif worker in self._workers:
                # Healthy worker → return it to the pool for reuse. The
                # ``worker in self._workers`` guard avoids re-queueing a stale
                # worker after the pool was shut down and restarted.
                await self._free.put(worker)

    async def _run_on_worker(
        self,
        worker: _Worker,
        request: dict,
        progress_callback: Callable[[dict], Awaitable[None]] | None,
    ) -> tuple[dict, str]:
        try:
            worker.stdin.write((json.dumps(request) + "\n").encode())
            await worker.stdin.drain()
        except (BrokenPipeError, ConnectionError, ValueError) as exc:
            raise _WorkerDead(str(exc)) from exc

        result: dict | None = None
        error_result: dict | None = None
        stderr_text = ""

        async def _read_stdout() -> None:
            nonlocal result, error_result, stderr_text
            if worker.stdout is None:
                return
            async for line_bytes in worker.stdout:
                line = line_bytes.decode().strip()
                if not line:
                    continue
                try:
                    parsed = json.loads(line)
                except json.JSONDecodeError:
                    logger.warning("worker_stdout_non_json", line=line[:200])
                    continue
                if parsed.get("progress") and progress_callback:
                    await progress_callback(parsed)
                elif "error" in parsed:
                    error_result = parsed
                    stderr_text = parsed.get("_stderr", "")
                    return
                else:
                    result = {k: v for k, v in parsed.items() if k != "_stderr"}
                    stderr_text = parsed.get("_stderr", "")
                    return

        try:
            await asyncio.wait_for(_read_stdout(), timeout=self._timeout)
        except TimeoutError as exc:
            await _terminate_worker_on_timeout(worker.process)
            raise TimeoutError(
                f"yt-dlp extraction timed out after {self._timeout}s"
            ) from exc

        if error_result:
            # The worker is alive and reusable; surface the failure to the caller
            # (as a ``RuntimeError`` subclass) carrying the per-job stderr so the
            # caller can still run throttle detection on the failure path.
            raise _WorkerJobError(
                f"yt-dlp extraction failed: {error_result['error']}",
                stderr=stderr_text,
            )
        if result is not None:
            return result, stderr_text
        raise RuntimeError("yt-dlp worker exited without producing a result")

    async def _respawn_into_free(self, dead_worker: _Worker) -> None:
        """Replace a dead worker so the pool stays at full size.

        The discarded worker is terminated if it is somehow still running (it is
        normally already dead or killed on timeout) so no process is leaked. Spawn
        failures are swallowed (logged) so the original job error still propagates
        to the caller; the pool simply shrinks until a future respawn succeeds.
        """
        try:
            if dead_worker in self._workers:
                self._workers.remove(dead_worker)
        except ValueError:
            pass
        try:
            if dead_worker.process.returncode is None:
                await _kill_process_group(dead_worker.process, graceful=True)
        except Exception:
            pass
        # Another concurrent respawn may have already filled the slot.
        if len(self._workers) >= self._size:
            return
        try:
            new_worker = await self._spawn_worker()
        except Exception:
            logger.warning("yt_dlp_worker_respawn_failed", exc_info=True)
            return
        self._workers.append(new_worker)
        try:
            await self._free.put(new_worker)
        except asyncio.QueueFull:
            pass

    async def shutdown(self) -> None:
        """Terminate all workers and reset the pool."""
        async with self._lock:
            self._started = False
            workers = self._workers
            self._workers = []
            while not self._free.empty():
                try:
                    self._free.get_nowait()
                except asyncio.QueueEmpty:
                    break
        for worker in workers:
            try:
                if worker.process.returncode is None:
                    await _kill_process_group(worker.process, graceful=True)
            except Exception:
                pass


_pool: YtDlpProcessPool | None = None


def _get_pool() -> YtDlpProcessPool:
    """Return the module-level singleton worker pool, creating it lazily.

    Creation is synchronous with no ``await`` points, so it is atomic with
    respect to other coroutines on the same event loop (no double-create).
    """
    global _pool
    if _pool is None:
        _pool = YtDlpProcessPool(_EXTRACTION_CONCURRENCY, YT_DLP_TIMEOUT)
    return _pool


async def shutdown_yt_dlp_pool() -> None:
    """Shut down the worker pool (call on application shutdown)."""
    global _pool
    if _pool is not None:
        await _pool.shutdown()
        _pool = None


async def reset_yt_dlp_pool() -> None:
    """Shut down and clear the worker pool (used by tests to re-establish state)."""
    global _pool
    if _pool is not None:
        await _pool.shutdown()
        _pool = None


def _extract_error_message(error_msg: str, fallback: str) -> str:
    """Extract the most relevant error line from error output."""
    for error_line in error_msg.split("\n"):
        stripped = error_line.strip()
        if "ERROR" in stripped or "error" in stripped.lower():
            return stripped
    return fallback or error_msg


def _sanitize_title(title: str) -> str:
    """Sanitize a video title for safe use as a display name (not a path)."""
    # Remove any path separators, null bytes, and dots (prevent path traversal in display name)
    sanitized = title.replace("\x00", "").replace("/", "_").replace("\\", "_").replace(".", "_")
    # Remove non-printable characters
    sanitized = re.sub(r"[^\w\s\-]", "", sanitized)
    # Collapse whitespace
    sanitized = re.sub(r"\s+", " ", sanitized).strip()
    return sanitized or "download"


_YOUTUBE_DOMAINS = frozenset(
    {"youtube.com", "www.youtube.com", "m.youtube.com", "music.youtube.com"},
)
_YOUTUBE_SHORT_DOMAINS = frozenset({"youtu.be"})
_YOUTUBE_NOCOOKIE = frozenset({"youtube-nocookie.com", "www.youtube-nocookie.com"})
_VIMEO_HOSTS = frozenset({"vimeo.com", "www.vimeo.com"})
_DAILYMOTION_HOSTS = frozenset({"dailymotion.com", "www.dailymotion.com"})
_TWITCH_HOSTS = frozenset({"twitch.tv", "www.twitch.tv", "m.twitch.tv", "clips.twitch.tv"})
_TIKTOK_HOSTS = frozenset(
    {"tiktok.com", "www.tiktok.com", "m.tiktok.com", "vm.tiktok.com", "tiktokv.com"},
)
_INSTAGRAM_HOSTS = frozenset({"instagram.com", "www.instagram.com", "instagr.am"})


def _get_platform(url: str) -> str:
    """
    Identify the media platform associated with a URL hostname.

    Returns:
        str: The platform identifier, `"youtube"` for recognized YouTube or
            unrecognized hosts, `"unknown"` for suspicious platform-like hostnames,
            or the matching supported platform identifier.
    """
    hostname = (urlparse(url).hostname or "").lower()
    if not hostname:
        return "youtube"

    youtube_all = _YOUTUBE_DOMAINS | _YOUTUBE_SHORT_DOMAINS | _YOUTUBE_NOCOOKIE

    def _host_matches(domains: frozenset[str]) -> bool:
        """
        Determine whether the hostname matches a supported domain or its subdomain.

        Parameters:
            domains (frozenset[str]): Domains to match against the hostname.

        Returns:
            bool: `true` if the hostname matches a domain or valid subdomain, `false` otherwise.
        """
        return hostname in domains or any(hostname.endswith("." + d) for d in domains)

    if _host_matches(youtube_all):
        return "youtube"
    if _host_matches(_VIMEO_HOSTS):
        return "vimeo"
    if _host_matches(_DAILYMOTION_HOSTS):
        return "dailymotion"
    if _host_matches(_TWITCH_HOSTS):
        return "twitch"
    if _host_matches(_TIKTOK_HOSTS):
        return "tiktok"
    if _host_matches(_INSTAGRAM_HOSTS):
        return "instagram"
    # Subdomain-bypass detection: a hostname like youtube.com.evil.com
    # contains a platform domain but doesn't end with a valid suffix.
    # Truly unknown domains (e.g. example.com) default to "youtube"
    # since yt-dlp handles most URLs.
    all_platform_domains = (
        youtube_all
        | _VIMEO_HOSTS
        | _DAILYMOTION_HOSTS
        | _TWITCH_HOSTS
        | _TIKTOK_HOSTS
        | _INSTAGRAM_HOSTS
    )
    for domain in all_platform_domains:
        if hostname.startswith(domain + ".") or hostname.endswith("." + domain):
            return "unknown"
    return "youtube"


# Alias for backward compatibility — throttle-tracking uses the same platform key.
_service_from_url = _get_platform


def _build_cookies_opts() -> dict:
    """Build cookies-related yt-dlp options from environment configuration.

    Supports two modes (checked in order):
    1. YT_DLP_COOKIES_FILE — path to a Netscape-format cookies file
    2. YT_DLP_COOKIES_BROWSER — browser name for cookiesfrombrowser (e.g. chrome, firefox)

    Returns an empty dict if neither is configured or the cookie file doesn't exist.
    Logs a warning when the configured cookie file path is absent from disk.
    """
    opts: dict = {}
    cookies_file = os.environ.get("YT_DLP_COOKIES_FILE", "").strip()
    cookies_browser = os.environ.get("YT_DLP_COOKIES_BROWSER", "").strip()

    if cookies_file:
        resolved = os.path.abspath(cookies_file)
        if os.path.isfile(resolved):
            opts["cookiefile"] = resolved
        else:
            logger.warning(
                "cookies_file_not_found",
                configured=cookies_file,
                resolved=resolved,
                hint="Set YT_DLP_COOKIES_FILE to an existing Netscape-format cookies file",
            )
    elif cookies_browser:
        opts["cookiesfrombrowser"] = (cookies_browser,)

    return opts


# Non-YouTube platforms use single-stream formats with no merging semantics.
_GENERIC_FORMAT_CHAIN: list[dict] = [
    {"format": "best", "format_sort": ["quality"]},
]

_PLATFORM_FORMAT_CHAINS: dict[str, list[dict]] = {
    "youtube": FORMAT_FALLBACK_CHAIN,
    "tiktok": _GENERIC_FORMAT_CHAIN,
    "instagram": _GENERIC_FORMAT_CHAIN,
    "vimeo": _GENERIC_FORMAT_CHAIN,
    "dailymotion": _GENERIC_FORMAT_CHAIN,
    "twitch": _GENERIC_FORMAT_CHAIN,
}

# Extractor args per platform. Only YouTube benefits from player-client hints.
_PLATFORM_EXTRACTOR_ARGS: dict[str, dict] = {
    "youtube": {
        "youtube": {
            "player_client": ["tv", "web", "default", "mobile"],
        },
    },
}

# Platforms that require cookies for reliable extraction. Cookies are included
# automatically when configured via YT_DLP_COOKIES_FILE or YT_DLP_COOKIES_BROWSER.
_COOKIE_REQUIRED_PLATFORMS = frozenset({"tiktok", "instagram"})


async def _check_throttle(stderr_text: str, service: str = "youtube") -> None:
    """
    Record a throttling response when subprocess output indicates HTTP status 429.

    Parameters:
        stderr_text (str): Subprocess error output to inspect.
        service (str): Service associated with the response.
    """
    if not stderr_text:
        return
    if THROTTLE_PATTERN.search(stderr_text):
        from app.services.throttle_predictor import record_response

        await record_response(service, 429)


def _build_extract_request(url: str, output_template: str) -> dict:
    """Build the per-job request payload dispatched to a pooled worker.

    Encapsulates the platform-specific format chain, extractor args, cookies,
    and output-field selection that previously lived inline in the per-call
    extraction subprocess script.
    """
    platform = _get_platform(url)
    cookies_opts = _build_cookies_opts()
    fallback_chain = _PLATFORM_FORMAT_CHAINS.get(platform, FORMAT_FALLBACK_CHAIN)
    extractor_args = _PLATFORM_EXTRACTOR_ARGS.get(platform, {})

    if platform in _COOKIE_REQUIRED_PLATFORMS and not cookies_opts:
        logger.info(
            "extraction_without_cookies",
            platform=platform,
            url=url[:80],
            hint="Set YT_DLP_COOKIES_FILE or YT_DLP_COOKIES_BROWSER to enable cookies for this platform",
        )

    return {
        "url": url,
        "output_template": output_template,
        "platform": platform,
        "fallback_chain": fallback_chain,
        "extractor_args": extractor_args,
        "cookies_opts": cookies_opts,
        "output_fields": list(_OUTPUT_FIELDS),
        "youtube_opts": platform == "youtube",
    }


async def _extract_via_subprocess(
    url: str,
    output_template: str,
    progress_callback: Callable[[dict], Awaitable[None]] | None = None,
) -> dict:
    """Extract media using a pooled, persistent yt-dlp worker.

    Reuses a long-lived worker subprocess (which imports ``yt_dlp`` once) instead
    of spawning a fresh ``python -c`` subprocess per job. See ``YtDlpProcessPool``
    for the worker protocol, lifecycle, and timeout/kill semantics.

    Raises:
        SSRFError: If the URL resolves to a private or internal address.
        TimeoutError: If extraction exceeds the configured timeout.
        RuntimeError: If extraction fails or produces no usable output.
    """
    await _check_ssrf(url)
    request = _build_extract_request(url, output_template)
    pool = _get_pool()
    try:
        result, stderr_text = await pool.submit(request, progress_callback)
    except _WorkerJobError as exc:
        # Throttle detection must also run on the failed-extraction path: a 429
        # is most likely to surface in the failure's stderr, and the throttle
        # predictor only ever learns about throttling from failure output. The
        # worker stays alive and reusable; only the job failed.
        service = _service_from_url(url)
        await _check_throttle(exc.stderr, service)
        raise
    if stderr_text:
        service = _service_from_url(url)
        await _check_throttle(stderr_text, service)

    return result


async def extract_media_url(
    url: str,
    storage_path: str,
    progress_callback: Callable[[dict], Awaitable[None]] | None = None,
) -> tuple[str, str, str | None]:
    """
    Extract media from a supported video URL and store the resulting file.

    Supports YouTube, Vimeo, Dailymotion, Twitch, TikTok, and Instagram. TikTok and
    Instagram may require cookies configured through YT_DLP_COOKIES_FILE or
    YT_DLP_COOKIES_BROWSER.

    Parameters:
        url (str): Video URL to extract.
        storage_path (str): Base directory for downloaded files.
        progress_callback (Callable[[dict], Awaitable[None]] | None): Optional
            asynchronous callback receiving download progress updates.

    Returns:
        tuple[str, str, str | None]: The stored file path, display filename, and
        extracted title, or None when no title is available.

    Raises:
        StorageError: If the download directory cannot be created, the output path
            is invalid, or the extracted file is missing.
    """
    download_dir = os.path.join(storage_path, "downloads")
    try:
        os.makedirs(download_dir, exist_ok=True)
    except OSError as e:
        logger.error("failed_to_create_download_directory", directory=download_dir, error=str(e))
        raise StorageError(f"Failed to create download directory: {e}") from e

    file_id = str(uuid.uuid4())
    # Use ONLY the UUID for the filesystem path — never the title
    output_template = os.path.join(download_dir, f"{file_id}.%(ext)s")

    # Run via subprocess so it can be killed on timeout.
    # Extraction semaphore prevents OOM from N concurrent ~50-100MB processes.
    async with _EXTRACTION_SEMAPHORE:
        info = await _extract_via_subprocess(
            url,
            output_template,
            progress_callback=progress_callback,
        )

    title: str | None = info.get("title") or None
    ext = info.get("ext") or "mp4"
    # `ext` is derived from the remote media URL and reaches the
    # Content-Disposition filename unsanitized (title goes through
    # _sanitize_title, ext did not). Constrain it to a safe extension shape.
    if not re.fullmatch(r"[a-z0-9]{1,8}", ext, re.IGNORECASE):
        ext = "mp4"
    # Sanitize title for display only — never used in filesystem path
    safe_title = _sanitize_title(str(title or file_id))
    file_name = f"{safe_title}.{ext}"
    file_path = os.path.join(download_dir, f"{file_id}.{ext}")

    # Validate the resolved path is within download_dir
    try:
        file_path = validate_path(download_dir, file_path)
    except (ValueError, PermissionError) as e:
        raise StorageError(str(e)) from e

    # Verify the file was actually created
    if not os.path.isfile(file_path):
        raise StorageError(f"Expected output file not found: {file_path}")

    return file_path, file_name, title

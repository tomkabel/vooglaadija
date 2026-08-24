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
from core.config import settings
from core.logging_config import get_logger
from core.utils.security import validate_path

logger = get_logger(__name__)

# Timeout for yt-dlp operations in seconds (5 minutes)
YT_DLP_TIMEOUT = 300

# Timeout for lightweight metadata-only extraction (no download)
YT_DLP_METADATA_TIMEOUT = 15

THROTTLE_PATTERN = re.compile(r"HTTP Error 429", re.IGNORECASE)

# Native single-pass YouTube format selection.
#
# Historically this was a list of format specs iterated in a `for` loop, but
# `extract_info(download=True)` resolves AND downloads in one shot, so the loop
# was a sequence of independent full-selection attempts — only the first spec
# ever ran, and the "chain" collapsed to a single 1080p/h264 selector (see
# issue #169). yt-dlp already does progressive degradation across a "/"-
# separated format string in a single call, so we encode the whole chain there
# and issue ONE extract_info. The trailing `/best` / `/worst` give yt-dlp
# built-in fallback when the merged combos are unavailable.
YOUTUBE_FORMAT = (
    "bestvideo*+bestaudio/best/res:1080+h264"
    " / bestvideo+bestaudio/best"
    " / worstvideo*+bestaudio/best/res:720"
    " / best"
    " / worst"
)
YOUTUBE_FORMAT_SORT = ["res:1080", "codec:h264"]

# Progressive-first variant (used when YT_DLP_PREFER_PROGRESSIVE is enabled):
# try a single-stream (no ffmpeg merge) progressive file *before* the merged
# combos. Lighter CPU/storage and smaller failure surface, but YouTube
# progressive mp4 caps ~720p, so this trades resolution ceiling for weight.
YOUTUBE_FORMAT_PROGRESSIVE = (
    "best[ext=mp4][protocol!=dash]"
    " / best[protocol!=dash]"
    " / bestvideo*+bestaudio/best/res:1080+h264"
    " / bestvideo+bestaudio/best"
    " / worstvideo*+bestaudio/best/res:720"
    " / best"
    " / worst"
)

# Non-YouTube platforms use single-stream progressive formats (no merging).
GENERIC_FORMAT = "best"
GENERIC_FORMAT_SORT = ["quality"]


def _format_spec_for(platform: str) -> tuple[str, list[str]]:
    """Return (format_string, format_sort) for a platform.

    YouTube uses the merged-combo chain above (or the progressive-first variant
    when ``settings.yt_dlp_prefer_progressive`` is enabled); everything else gets
    a single progressive ``best`` stream.
    """
    if platform == "youtube":
        if getattr(settings, "yt_dlp_prefer_progressive", False):
            return YOUTUBE_FORMAT_PROGRESSIVE, YOUTUBE_FORMAT_SORT
        return YOUTUBE_FORMAT, YOUTUBE_FORMAT_SORT
    return GENERIC_FORMAT, GENERIC_FORMAT_SORT

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

    # Warm-pool fast path: ship a metadata job to a pooled driver (imports yt_dlp
    # once) and fall back to the per-job python -c subprocess on any failure.
    pool = _get_pool()
    if pool is not None:
        job = {
            "job_id": str(uuid.uuid4()),
            "mode": "metadata",
            "url": url,
            "platform": platform,
            "cookies_opts": cookies_opts,
        }
        try:
            await pool.ensure_started()
            result = await pool.run_job(job, job_timeout=YT_DLP_METADATA_TIMEOUT)
            raw = result.get("title") if isinstance(result, dict) else None
            if isinstance(raw, str) and raw:
                return raw
            return None
        except Exception as exc:
            logger.warning(
                "warm_pool_metadata_fallback",
                error=str(exc),
                hint="yt-dlp warm pool metadata failed; using per-job subprocess",
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


# Platform format selection is handled by `_format_spec_for()` (defined above):
# YouTube uses the merged-combo chain; every other platform gets a single
# progressive `best` stream. Both are encoded as a single yt-dlp format string
# (with "/" fallback) so yt-dlp does the degradation in one extract_info call.

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


class YtDlpProcessPool:
    """Pool of warm, long-lived yt-dlp driver subprocesses.

    Each slot is a single driver process (``app.services.yt_dlp_worker_driver``)
    that imported ``yt_dlp`` once and processes one job at a time over stdin/stdout.
    This eliminates the per-job ``python -c "import yt_dlp"`` cold start.

    Slots are checked out exclusively: a process handles exactly one job until it
    emits a terminal response, so the existing process-group kill semantics (used
    on timeout) always target the correct owning pid.

    The pool is crash-tolerant: if a driver process dies, its slot is marked dead
    and the next checkout spawns a replacement. Callers treat pool unavailability
    as a signal to fall back to the per-job subprocess path.
    """

    def __init__(
        self,
        size: int,
        startup_timeout: float = 30.0,
        driver_module: str = "app.services.yt_dlp_worker_driver",
    ) -> None:
        self._size = max(1, size)
        self._startup_timeout = startup_timeout
        self._driver_module = driver_module
        self._lock = asyncio.Lock()
        # Each slot: {"proc": Process|None, "ready": bool, "busy": bool}
        self._slots: list[dict] = [{"proc": None, "ready": False, "busy": False} for _ in range(self._size)]
        self._started = False

    async def _start_slot(self, slot: dict) -> bool:
        """Spawn and readiness-check one driver process. Returns True on success."""
        try:
            proc = await asyncio.create_subprocess_exec(
                sys.executable,
                "-m",
                self._driver_module,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                start_new_session=True,
                limit=_STREAM_READER_LIMIT,
            )
        except OSError:
            return False

        slot["proc"] = proc
        slot["ready"] = False

        # Wait for the driver's {"_worker_ready": true} handshake.
        try:
            line_bytes = await asyncio.wait_for(
                proc.stdout.readline(), timeout=self._startup_timeout
            )
        except (TimeoutError, ValueError):
            # No handshake in time — kill and treat as dead.
            await _kill_process_group(proc, graceful=True)
            slot["proc"] = None
            return False

        if not line_bytes:
            # Process exited before ready.
            await _kill_process_group(proc, graceful=True)
            slot["proc"] = None
            return False

        try:
            handshake = json.loads(line_bytes.decode().strip())
        except json.JSONDecodeError:
            handshake = {}

        if not handshake.get("_worker_ready"):
            await _kill_process_group(proc, graceful=True)
            slot["proc"] = None
            return False

        slot["ready"] = True
        return True

    async def ensure_started(self) -> None:
        """Start all slots (idempotent). Best-effort: dead slots are simply left None."""
        if self._started:
            return
        async with self._lock:
            if self._started:
                return
            for slot in self._slots:
                await self._start_slot(slot)
            self._started = True

    def available_count(self) -> int:
        return sum(1 for s in self._slots if s["proc"] is not None and s["ready"] and not s["busy"])

    async def _checkout(self) -> dict | None:
        """Return a free, ready slot, spawning replacements for dead ones.

        Returns None if no slot is currently usable (caller should fall back).
        """
        async with self._lock:
            for slot in self._slots:
                if slot["busy"]:
                    continue
                proc = slot["proc"]
                if proc is None or proc.returncode is not None or not slot["ready"]:
                    # Try to (re)start this slot.
                    if not await self._start_slot(slot):
                        continue
                slot["busy"] = True
                return slot
        return None

    async def _release(self, slot: dict) -> None:
        async with self._lock:
            slot["busy"] = False
            proc = slot["proc"]
            # If the driver exited during the job, drop it so the next checkout
            # respawns a fresh one.
            if proc is None or proc.returncode is not None or not slot["ready"]:
                slot["proc"] = None
                slot["ready"] = False

    async def run_job(
        self,
        job: dict,
        job_timeout: float,
        progress_callback: Callable[[dict], Awaitable[None]] | None = None,
    ) -> dict:
        """Run one job on a pooled driver. Raises on failure so callers can fall back.

        Reads stdout lines for this job_id until a terminal ``result``/``error``
        line arrives. Progress lines are relayed to ``progress_callback``.
        On timeout, applies the same process-group kill semantics as the
        per-job path (SIGTERM grace -> SIGKILL + orphan walk).
        """
        slot = await self._checkout()
        if slot is None or slot["proc"] is None:
            raise RuntimeError("yt-dlp warm pool has no available slot")
        proc = slot["proc"]
        job_id = job.get("job_id", "")

        try:
            proc.stdin.write((json.dumps(job) + "\n").encode())
            await proc.stdin.drain()

            result: dict | None = None
            error: str | None = None
            while True:
                try:
                    line_bytes = await asyncio.wait_for(proc.stdout.readline(), timeout=job_timeout)
                except TimeoutError:
                    await self._kill_busy_slot(slot, proc)
                    raise TimeoutError(
                        f"yt-dlp warm-pool job timed out after {job_timeout}s"
                    ) from None
                if not line_bytes:
                    # Driver closed stdout unexpectedly.
                    await _kill_process_group(proc, graceful=True)
                    raise RuntimeError("yt-dlp warm-pool driver closed stdout")
                try:
                    parsed = json.loads(line_bytes.decode().strip())
                except json.JSONDecodeError:
                    logger.warning("warm_pool_stdout_non_json", line=line_bytes[:200].decode(errors="replace"))
                    continue
                if parsed.get("job_id") != job_id:
                    continue
                if parsed.get("progress") and progress_callback:
                    await progress_callback(parsed)
                elif "error" in parsed:
                    error = parsed["error"]
                    break
                elif "result" in parsed:
                    result = parsed["result"]
                    break

            if error is not None:
                raise RuntimeError(f"yt-dlp extraction failed: {error}")
            if result is None:
                raise RuntimeError("yt-dlp warm-pool produced no result")
            return result
        finally:
            await self._release(slot)

    async def _kill_busy_slot(self, slot: dict, proc: asyncio.subprocess.Process) -> None:
        """Apply SIGTERM -> SIGKILL + orphan walk against a busy driver's group."""
        try:
            pgid = os.getpgid(proc.pid)
        except (ProcessLookupError, OSError):
            return
        for sig in (signal.SIGTERM, signal.SIGCONT):
            try:
                os.killpg(pgid, sig)
            except (ProcessLookupError, OSError):
                pass
        try:
            await asyncio.wait_for(proc.wait(), timeout=3)
        except TimeoutError:
            pass
        await _walk_and_kill_orphaned_children(proc)
        try:
            os.killpg(pgid, signal.SIGKILL)
        except (ProcessLookupError, OSError):
            pass
        try:
            await asyncio.wait_for(proc.wait(), timeout=3)
        except TimeoutError:
            pass

    async def shutdown(self) -> None:
        async with self._lock:
            for slot in self._slots:
                proc = slot["proc"]
                if proc is not None and proc.returncode is None:
                    await _kill_process_group(proc, graceful=True)
                slot["proc"] = None
                slot["ready"] = False
            self._started = False


# Module-level singleton, created lazily from settings so importing this module
# (e.g. in tests) does not spawn processes.
_pool: YtDlpProcessPool | None = None
_pool_failed = False


def _get_pool() -> YtDlpProcessPool | None:
    """Return the warm pool singleton, or None if disabled/unavailable."""
    global _pool, _pool_failed
    if _pool_failed:
        return None
    if os.environ.get("TESTING", "").lower() in ("1", "true", "yes", "on"):
        # Under TESTING the subprocess layer is mocked, so spawning real driver
        # processes (and waiting on their handshake) is both pointless and would
        # hang. Callers fall back to the inline per-job subprocess path.
        return None
    if not settings.yt_dlp_warm_pool:
        return None
    if _pool is None:
        try:
            _pool = YtDlpProcessPool(size=settings.yt_dlp_pool_size)
        except Exception as exc:
            logger.warning("yt_dlp_warm_pool_init_failed", error=str(exc))
            _pool_failed = True
            return None
    return _pool


async def _extract_via_subprocess(
    url: str,
    output_template: str,
    progress_callback: Callable[[dict], Awaitable[None]] | None = None,
) -> dict:
    """
    Extract media information and download media using yt-dlp.

    Supports platform-specific format fallbacks and optionally reports download progress
    through the callback.

    Parameters:
        url (str): Media URL to validate and extract.
        output_template (str): Template for the downloaded media file path.
        progress_callback (Callable[[dict], Awaitable[None]] | None): Callback that receives
                progress updates when provided.

    Returns:
        dict: Extracted media information.

    Raises:
        SSRFError: If the URL resolves to a private or internal address.
        TimeoutError: If extraction exceeds the configured timeout.
        RuntimeError: If extraction fails or produces no usable output.
    """
    await _check_ssrf(url)

    url_json = json.dumps(url)
    output_template_json = json.dumps(output_template)
    platform = _get_platform(url)
    platform_json = json.dumps(platform)
    cookies_opts = _build_cookies_opts()
    cookies_opts_json = json.dumps(cookies_opts)
    format_spec, format_sort = _format_spec_for(platform)
    format_spec_json = json.dumps(format_spec)
    format_sort_json = json.dumps(format_sort)
    extractor_args = _PLATFORM_EXTRACTOR_ARGS.get(platform, {})
    extractor_args_json = json.dumps(extractor_args)

    if platform in _COOKIE_REQUIRED_PLATFORMS and not cookies_opts:
        logger.info(
            "extraction_without_cookies",
            platform=platform,
            url=url[:80],
            hint="Set YT_DLP_COOKIES_FILE or YT_DLP_COOKIES_BROWSER to enable cookies for this platform",
        )

    output_fields_json = json.dumps(list(_OUTPUT_FIELDS))

    # Only inject youtube-specific options when building the script for YouTube.
    # Tests assert that non-YouTube platform scripts do not contain YouTube-only keys.
    # Embedding them as dict entries keeps the options inside the ydl_opts dict definition.
    if platform == "youtube":
        youtube_opts = (
            '            "prefer_free_formats": True,\n            "check_formats": "missable",\n'
        )
    else:
        youtube_opts = ""

    extract_script = f"""
import sys
import json
import yt_dlp

url = {url_json}
output_template = {output_template_json}
platform = {platform_json}
format_spec = {format_spec_json}
format_sort = {format_sort_json}
extractor_args = {extractor_args_json}
cookies_opts = {cookies_opts_json}
output_fields = {output_fields_json}

if "cookiesfrombrowser" in cookies_opts and isinstance(cookies_opts["cookiesfrombrowser"], list):
    cookies_opts["cookiesfrombrowser"] = tuple(cookies_opts["cookiesfrombrowser"])

last_error = None
_last_progress_pct = -1.0

def _progress_hook(d):
    global _last_progress_pct
    if d.get("status") == "downloading":
        downloaded = d.get("downloaded_bytes", 0)
        total = d.get("total_bytes") or d.get("total_bytes_estimate") or 0
        pct = (downloaded / total * 100) if total > 0 else 0
        if pct - _last_progress_pct < 0.5:
            return
        _last_progress_pct = pct
        print(json.dumps({{
            "progress": True,
            "percent": round(pct, 1),
            "downloaded_bytes": downloaded,
            "total_bytes": total,
            "speed": d.get("speed"),
            "eta": d.get("eta"),
        }}), flush=True)

# Single extract_info call. The "/" in format_spec is yt-dlp's native fallback
# across the whole chain, so degradation (merged-combo -> best -> worst) happens
# inside one call instead of a per-spec loop that only ever ran the first entry.
ydl_opts = {{
    "format": format_spec,
    "format_sort": format_sort,
    "outtmpl": output_template,
    "quiet": True,
    "no_warnings": True,
    "noprogress": True,
    "socket_timeout": 60,
    "retries": 3,
    "progress_hooks": [_progress_hook],
{youtube_opts}}}

ydl_opts.update(cookies_opts)
if extractor_args:
    ydl_opts["extractor_args"] = extractor_args

try:
    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        info = ydl.extract_info(url, download=True)
        if info is None:
            print(json.dumps({{"error": f"[{platform}] No video info returned"}}))
            sys.exit(1)
        sanitized_info = ydl.sanitize_info(info)
        filtered = {{k: sanitized_info.get(k) for k in output_fields if k in sanitized_info}}
        print(json.dumps(filtered))
        sys.exit(0)
except Exception as e:
    print(json.dumps({{"error": f"[{platform}] {{str(e)}}"}}))
    sys.exit(1)
"""
    process = None
    stderr_lines: list[str] = []
    result: dict[str, Any] | None = None
    error_result: dict[str, Any] | None = None

    try:
        process = await asyncio.create_subprocess_exec(
            sys.executable,
            "-c",
            extract_script,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            start_new_session=True,
            limit=_STREAM_READER_LIMIT,
        )

        async def _read_stdout() -> None:
            nonlocal result, error_result
            if process.stdout is None:
                return
            async for line_bytes in process.stdout:
                line = line_bytes.decode().strip()
                if not line:
                    continue
                try:
                    parsed = json.loads(line)
                    if parsed.get("progress") and progress_callback:
                        await progress_callback(parsed)
                    elif "error" in parsed:
                        error_result = parsed
                    else:
                        result = parsed
                except json.JSONDecodeError:
                    logger.warning("stdout_non_json_line", line=line[:200])

        async def _read_stderr() -> None:
            """
            Collects the subprocess's standard error output as decoded lines.
            """
            if process.stderr is None:
                return
            async for line_bytes in process.stderr:
                stderr_lines.append(line_bytes.decode().strip())

        try:
            await asyncio.wait_for(
                asyncio.gather(_read_stdout(), _read_stderr()),
                timeout=YT_DLP_TIMEOUT,
            )
        except TimeoutError as e:
            pgid = os.getpgid(process.pid)
            # Broadcast SIGTERM to the entire process group
            for sig in (signal.SIGTERM, signal.SIGCONT):
                try:
                    os.killpg(pgid, sig)
                except (ProcessLookupError, OSError):
                    pass

            # Give brief grace for clean shutdown
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

            raise TimeoutError(f"yt-dlp extraction timed out after {YT_DLP_TIMEOUT}s") from e

        stderr_text = "\n".join(stderr_lines)
        service = _service_from_url(url)
        await _check_throttle(stderr_text, service)

        if error_result:
            raise RuntimeError(f"yt-dlp extraction failed: {error_result['error']}")

        if result is not None:
            return result

        if process.returncode != 0:
            error_msg = stderr_text or "Unknown error"
            error_msg = _extract_error_message(error_msg, "")
            if not error_msg:
                error_msg = "Unknown error"
            raise RuntimeError(f"yt-dlp failed: {error_msg}")

        raise RuntimeError("yt-dlp extraction completed but produced no usable output")

    finally:
        if process and process.returncode is None:
            await _kill_process_group(process)


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

    # Run via subprocess so it can be killed on timeout. The extraction semaphore
    # bounds total concurrent extraction (pool slots + inline) to avoid OOM from
    # N concurrent ~50-100MB yt-dlp/ffmpeg processes.
    async with _EXTRACTION_SEMAPHORE:
        info: dict | None = None
        pool = _get_pool()
        if pool is not None:
            platform = _get_platform(url)
            format_spec, format_sort = _format_spec_for(platform)
            extractor_args = _PLATFORM_EXTRACTOR_ARGS.get(platform, {})
            cookies_opts = _build_cookies_opts()
            job = {
                "job_id": str(uuid.uuid4()),
                "mode": "extract",
                "url": url,
                "output_template": output_template,
                "platform": platform,
                "format": format_spec,
                "format_sort": format_sort,
                "extractor_args": extractor_args,
                "cookies_opts": cookies_opts,
                "youtube_opts": platform == "youtube",
            }
            try:
                await pool.ensure_started()
                info = await pool.run_job(
                    job, job_timeout=YT_DLP_TIMEOUT, progress_callback=progress_callback
                )
            except Exception as exc:
                logger.warning(
                    "warm_pool_extract_fallback",
                    error=str(exc),
                    hint="yt-dlp warm pool failed; using per-job subprocess",
                )

        if info is None:
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

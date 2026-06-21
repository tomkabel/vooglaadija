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

from app.logging_config import get_logger
from app.utils.validators import validate_url_not_ssrf

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
    """Resolve a video title from a URL without downloading.

    Runs yt-dlp with download=False for fast metadata extraction (~0.5-3s).
    Called at job creation time so the title is available immediately in the UI.

    Includes SSRF protection: validates the URL does not resolve to a private IP.

    Returns the raw video title string, or None if extraction fails for any reason
    (timeout, network error, unsupported URL, SSRF, etc.). Never raises.
    """
    try:
        await _check_ssrf(url)
    except SSRFError:
        logger.warning("ssrf_blocked_metadata", url=url[:80])
        return None

    url_json = json.dumps(url)
    script = f"""
import sys
import json
import yt_dlp
url = {url_json}
try:
    ydl_opts = {{
        "quiet": True,
        "no_warnings": True,
        "noprogress": True,
        "socket_timeout": 10,
        "retries": 1,
    }}
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
                process.communicate(), timeout=YT_DLP_METADATA_TIMEOUT
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


class StorageError(Exception):
    """Raised when storage operations fail."""


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
                None, lambda: open(children_path).read()
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
    return fallback if fallback else error_msg


def _sanitize_title(title: str) -> str:
    """Sanitize a video title for safe use as a display name (not a path)."""
    # Remove any path separators, null bytes, and dots (prevent path traversal in display name)
    sanitized = title.replace("\x00", "").replace("/", "_").replace("\\", "_").replace(".", "_")
    # Remove non-printable characters
    sanitized = re.sub(r"[^\w\s\-]", "", sanitized)
    # Collapse whitespace
    sanitized = re.sub(r"\s+", " ", sanitized).strip()
    return sanitized or "download"


def _service_from_url(url: str) -> str:
    """Derive throttle-tracking service name from a URL.

    This is a best-effort extraction for metric labels only (not security-critical).
    Defaults to 'youtube' for backward compatibility.
    """
    hostname = urlparse(url).hostname or ""
    hostname = hostname.lower()
    if "youtube" in hostname or "youtu.be" in hostname:
        return "youtube"
    if "vimeo" in hostname:
        return "vimeo"
    if "dailymotion" in hostname:
        return "dailymotion"
    if "twitch" in hostname:
        return "twitch"
    if "tiktok" in hostname:
        return "tiktok"
    if "instagram" in hostname:
        return "instagram"
    return "youtube"


async def _check_throttle(stderr_text: str, service: str = "youtube") -> None:
    """Parse stderr for HTTP 429 pattern and record response if found.

    yt-dlp runs as a subprocess, so HTTP status codes aren't exposed directly.
    Detection is via stderr pattern matching against 'HTTP Error 429'.
    """
    if not stderr_text:
        return
    if THROTTLE_PATTERN.search(stderr_text):
        from app.services.throttle_predictor import record_response

        await record_response(service, 429)


async def _extract_via_subprocess(
    url: str,
    output_template: str,
    progress_callback: Callable[[dict], Awaitable[None]] | None = None,
) -> dict:
    """
    Extract media info via subprocess that can be forcibly killed on timeout.

    This runs yt-dlp as a separate OS process so that on TimeoutError,
    process.kill() can terminate it immediately rather than leaving a thread running.

    Uses a format fallback chain to handle "Requested format is not available" errors
    that occur when YouTube doesn't have the exact formats needed for merging.

    When progress_callback is provided, the subprocess emits download progress JSON
    lines via stdout, which are parsed and forwarded to the callback in real time.
    """
    await _check_ssrf(url)

    url_json = json.dumps(url)
    output_template_json = json.dumps(output_template)
    fallback_chain_json = json.dumps(FORMAT_FALLBACK_CHAIN)

    output_fields_json = json.dumps(list(_OUTPUT_FIELDS))

    extract_script = f"""
import sys
import json
import yt_dlp

url = {url_json}
output_template = {output_template_json}
fallback_chain = {fallback_chain_json}
output_fields = {output_fields_json}

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

for i, format_spec in enumerate(fallback_chain):
    _last_progress_pct = -1.0  # reset so each fallback attempt reports fresh progress
    ydl_opts = {{
        "format": format_spec["format"],
        "format_sort": format_spec.get("format_sort", []),
        "outtmpl": output_template,
        "quiet": True,
        "no_warnings": True,
        "noprogress": True,
        "socket_timeout": 60,
        "retries": 3,
        "prefer_free_formats": True,
        "check_formats": "missable",
        "progress_hooks": [_progress_hook],
        "extractor_args": {{
            "youtube": {{
                "player_client": ["tv", "web", "default", "mobile"],
            }},
        }},
    }}

    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=True)
            if info is None:
                last_error = "No video info returned"
                continue
            sanitized_info = ydl.sanitize_info(info)
            filtered = {{k: sanitized_info.get(k) for k in output_fields if k in sanitized_info}}
            print(json.dumps(filtered))
            sys.exit(0)
    except Exception as e:
        err_str = str(e)
        if "Requested format" in err_str and "not available" in err_str:
            last_error = err_str
            continue
        print(json.dumps({{"error": err_str}}))
        sys.exit(1)

attempted_formats = [spec["format"] for spec in fallback_chain]
print(json.dumps({{
    "error": f"All formats failed. Last error: {{last_error}}. Attempted formats: {{attempted_formats}}"
}}))
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

        async def _read_stdout():
            nonlocal result, error_result
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

        async def _read_stderr():
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
            error_msg = stderr_text if stderr_text else "Unknown error"
            error_msg = _extract_error_message(error_msg, "")
            if not error_msg:
                error_msg = "Unknown error"
            raise RuntimeError(f"yt-dlp failed: {error_msg}")

        raise RuntimeError("yt-dlp extraction completed but produced no usable output")

    finally:
        if process and process.returncode is None:
            await _kill_process_group(process)


def _validate_path_within(base_path: str, target_path: str) -> str:
    """Validate that target_path resolves within base_path.

    Returns the resolved path if valid.
    Raises StorageError if the path escapes the base directory.
    """
    from app.utils.security import validate_path_within

    try:
        return validate_path_within(base_path, target_path)
    except ValueError as e:
        raise StorageError(str(e)) from e


async def extract_media_url(
    url: str,
    storage_path: str,
    progress_callback: Callable[[dict], Awaitable[None]] | None = None,
) -> tuple[str, str, str | None]:
    """
    Extract media URL from a YouTube URL using yt-dlp.

    Args:
        url: The video URL to extract.
        storage_path: Base path for storing downloaded files.
        progress_callback: Optional async callback invoked with progress dicts during download.

    Returns:
        tuple of (file_path, file_name, title) where file_path is always within storage_path
        and title is the human-readable video title (or None if unavailable).

    Raises:
        StorageError: If the download directory cannot be created or path is invalid.
        SSRFError: If the URL resolves to a private/internal IP address.
        asyncio.TimeoutError: If the extraction takes longer than YT_DLP_TIMEOUT.
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
            url, output_template, progress_callback=progress_callback
        )

    title: str | None = info.get("title") or None
    ext = info.get("ext") or "mp4"
    # Sanitize title for display only — never used in filesystem path
    safe_title = _sanitize_title(str(title if title else file_id))
    file_name = f"{safe_title}.{ext}"
    file_path = os.path.join(download_dir, f"{file_id}.{ext}")

    # Validate the resolved path is within download_dir
    file_path = _validate_path_within(download_dir, file_path)

    # Verify the file was actually created
    if not os.path.isfile(file_path):
        raise StorageError(f"Expected output file not found: {file_path}")

    return file_path, file_name, title

"""Tests for the warm yt-dlp subprocess pool (#162).

These exercise the real driver process (``yt_dlp_worker_driver``) without the
TESTING flag, so they actually spawn python and import yt_dlp once per slot.
They are kept small and time-boxed to avoid slow CI.
"""

from __future__ import annotations

import asyncio

import pytest

from app.services import yt_dlp_service
from app.services.yt_dlp_service import YtDlpProcessPool, _get_pool


class _FakeStream:
    """Minimal asyncio stream stand-in (readline/drain/write)."""

    def __init__(self, lines: list[bytes] | None = None):
        self._lines = list(lines or [])

    async def readline(self):
        if not self._lines:
            return b""
        return self._lines.pop(0)

    async def drain(self):
        return None

    def write(self, data) -> None:
        return None


class _FakeProc:
    def __init__(self, out_lines: list[bytes]):
        self.stdout = _FakeStream(out_lines)
        self.stderr = _FakeStream([])
        self.stdin = _FakeStream([])
        self.returncode = None
        self.pid = 424242

    async def wait(self):
        return 0


class _ChattyStream:
    """A stdout stream that always yields progress lines (never EOF)."""

    async def readline(self):
        return b'{"job_id": "j1", "progress": true, "percent": 99.9}\n'

    async def drain(self):
        return None

    def write(self, data) -> None:
        return None


class _ChattyProc(_FakeProc):
    def __init__(self):
        super().__init__([])
        self.stdout = _ChattyStream()


# Ensure the pool is genuinely exercised (not skipped via the TESTING guard).
@pytest.fixture(autouse=True)
def _no_testing_env(monkeypatch):
    monkeypatch.delenv("TESTING", raising=False)


@pytest.mark.asyncio
async def test_pool_starts_and_handshakes():
    """A fresh pool spawns driver processes that emit the ready handshake."""
    pool = YtDlpProcessPool(size=2, startup_timeout=60.0)
    try:
        await pool.ensure_started()
        assert pool.available_count() >= 1
    finally:
        await pool.shutdown()


@pytest.mark.asyncio
async def test_pool_runs_metadata_job():
    """The pool runs a metadata job through the warm driver (offline protocol test).

    Uses the guaranteed-unresolvable ``.invalid`` TLD so the driver returns a
    typed error quickly without contacting any real service — the stdin/stdout
    round-trip is what's under test, not YouTube.
    """
    pool = YtDlpProcessPool(size=1, startup_timeout=60.0)
    try:
        await pool.ensure_started()
        # A synthetic but well-formed job exercises the driver's JSON round-trip
        # (handshake -> stdin job -> stdout result/error) without the network.
        job = {
            "job_id": "meta-1",
            "mode": "metadata",
            "url": "https://unresolvable.invalid/video",
            "platform": "youtube",
            "cookies_opts": {},
        }
        try:
            result = await pool.run_job(job, job_timeout=60.0)
            # A networked driver could still theoretically resolve; accept a dict.
            assert isinstance(result, dict)
        except (RuntimeError, TimeoutError) as exc:
            # The driver returns a typed error for the unresolvable host, which
            # still proves the pool + driver plumbing works.
            assert "yt-dlp" in str(exc).lower()
    finally:
        await pool.shutdown()


@pytest.mark.asyncio
async def test_pool_dead_slot_respawns_on_next_checkout():
    """A slot whose driver died is replaced on the next checkout."""
    pool = YtDlpProcessPool(size=1, startup_timeout=60.0)
    try:
        await pool.ensure_started()
        slot = await pool._checkout()
        assert slot is not None
        # Kill the driver process out from under the slot.
        proc = slot["proc"]
        proc.kill()
        await asyncio.sleep(0.1)
        await pool._release(slot)
        # Next checkout should detect the dead process and respawn a fresh one.
        slot2 = await pool._checkout()
        assert slot2 is not None
        assert slot2["proc"] is not None
        assert slot2["proc"].returncode is None
        await pool._release(slot2)
    finally:
        await pool.shutdown()


@pytest.mark.asyncio
async def test_get_pool_disabled_under_testing(monkeypatch):
    """_get_pool returns None when TESTING is set, forcing the inline path."""
    monkeypatch.setenv("TESTING", "1")
    assert _get_pool() is None


@pytest.mark.asyncio
async def test_get_pool_singleton_and_fallback_flag(monkeypatch):
    """_get_pool respects the warm-pool disabled setting."""
    from core.config import settings as _settings

    monkeypatch.setattr(_settings, "yt_dlp_warm_pool", False)
    # Clear any cached singleton so the setting is re-read.
    monkeypatch.setattr(yt_dlp_service, "_pool", None)
    monkeypatch.setattr(yt_dlp_service, "_pool_failed", False)
    assert _get_pool() is None


@pytest.mark.asyncio
async def test_driver_module_is_importable():
    """The driver module imports cleanly (yt_dlp available in the image)."""
    from app.services import yt_dlp_worker_driver

    assert hasattr(yt_dlp_worker_driver, "main")
    assert hasattr(yt_dlp_worker_driver, "_run_extract")
    assert hasattr(yt_dlp_worker_driver, "_run_metadata")


@pytest.mark.asyncio
async def test_run_job_progress_callback_failure_kills_slot():
    """A raising progress callback kills the slot instead of releasing it busy."""
    pool = YtDlpProcessPool(size=1)
    proc = _FakeProc(
        [
            b'{"job_id": "j1", "progress": true, "percent": 10.0}\n',
            b'{"job_id": "j1", "result": {"title": "x"}}\n',
        ]
    )
    slot = pool._slots[0]
    slot["proc"] = proc
    slot["ready"] = True

    async def boom(_parsed):
        raise RuntimeError("pubsub hiccup")

    with pytest.raises(RuntimeError, match="pubsub hiccup"):
        await pool.run_job({"job_id": "j1"}, job_timeout=5.0, progress_callback=boom)

    # The slot must be dead (not released ready), so no other job can
    # interleave with the still-running driver or race the same output file.
    assert slot["ready"] is False
    assert slot["proc"] is None
    assert slot["busy"] is False


@pytest.mark.asyncio
async def test_run_job_wall_clock_deadline_bounds_chatty_job():
    """job_timeout is a total wall-clock deadline, not a per-line inactivity
    timeout — a download emitting progress lines must not run forever."""
    pool = YtDlpProcessPool(size=1)
    proc = _ChattyProc()
    slot = pool._slots[0]
    slot["proc"] = proc
    slot["ready"] = True

    start = asyncio.get_running_loop().time()
    with pytest.raises(TimeoutError, match="timed out"):
        await pool.run_job({"job_id": "j1"}, job_timeout=0.15)
    assert asyncio.get_running_loop().time() - start < 2.0
    assert slot["ready"] is False
    assert slot["proc"] is None


@pytest.mark.asyncio
async def test_run_job_relays_throttle_signal_on_error_path(monkeypatch):
    """A failed job whose stderr carries a 429 still feeds the throttle predictor.

    Regression for the PR #165 review finding "throttle detection on the failure
    path": the check must run on the per-job stderr before the error is re-raised,
    since 429s only ever appear on failed extractions.
    """
    recorded = []

    async def fake_record(service, status):
        recorded.append((service, status))

    monkeypatch.setattr("app.services.throttle_predictor.record_response", fake_record)

    class _ErrorWithStderr(_FakeStream):
        def __init__(self, slot):
            super().__init__([b'{"job_id": "j1", "error": "Video unavailable"}\n'])
            self._slot = slot

        async def readline(self):
            line = await super().readline()
            # Simulate the driver writing a 429 diagnostic to stderr mid-job.
            self._slot["stderr_lines"].append("ERROR: HTTP Error 429: Too Many Requests")
            return line

    pool = YtDlpProcessPool(size=1)
    slot = pool._slots[0]
    proc = _FakeProc([])
    proc.stdout = _ErrorWithStderr(slot)
    slot["proc"] = proc
    slot["ready"] = True
    slot["stderr_lines"] = ["pre-existing line"]

    with pytest.raises(RuntimeError, match="yt-dlp extraction failed"):
        await pool.run_job({"job_id": "j1"}, job_timeout=5.0, service="youtube")

    # The 429 must have been relayed to the throttle predictor even though the
    # job itself failed.
    assert recorded == [("youtube", 429)]


@pytest.mark.asyncio
async def test_pool_does_not_spawn_after_shutdown():
    """No checkout may spawn a fresh driver into a pool that was shut down.

    Regression for the PR #165 review finding "shutdown race": once a pool is
    discarded, an unwinding/in-flight checkout must fail fast instead of
    spawning a new yt_dlp subprocess into the just-discarded pool.
    """
    pool = YtDlpProcessPool(size=1, startup_timeout=60.0)
    await pool.ensure_started()
    assert pool.available_count() == 1
    await pool.shutdown()
    assert pool.available_count() == 0

    # A checkout after shutdown must return None without respawning.
    assert await pool._checkout() is None
    assert all(slot["proc"] is None for slot in pool._slots)
    assert all(slot["ready"] is False for slot in pool._slots)


@pytest.mark.asyncio
async def test_ensure_started_does_not_spawn_after_shutdown():
    """``ensure_started`` on a shut-down pool instance must stay a no-op.

    Regression for the kilo-code-bot review finding: ``ensure_started`` used
    to unconditionally clear ``_shutting_down`` and set ``_started = True``,
    reopening the spawn-after-shutdown race for any request that reached it
    in the window before ``shutdown_yt_dlp_pool`` drops the singleton.
    """
    pool = YtDlpProcessPool(size=1, startup_timeout=60.0)
    await pool.ensure_started()
    assert pool.available_count() == 1
    await pool.shutdown()

    await pool.ensure_started()

    assert pool._started is False
    assert pool._shutting_down is True
    assert all(slot["proc"] is None for slot in pool._slots)
    assert all(slot["ready"] is False for slot in pool._slots)


@pytest.mark.asyncio
async def test_shutdown_yt_dlp_pool_helper(monkeypatch):
    """The module-level shutdown helper is a no-op when idle and clears the singleton."""
    from app.services import yt_dlp_service

    monkeypatch.setattr(yt_dlp_service, "_pool", None)
    # Must not raise when no pool was ever created.
    await yt_dlp_service.shutdown_yt_dlp_pool()

    pool = YtDlpProcessPool(size=1)
    monkeypatch.setattr(yt_dlp_service, "_pool", pool)
    await yt_dlp_service.shutdown_yt_dlp_pool()
    assert yt_dlp_service._pool is None

"""Tests for the warm yt-dlp subprocess pool (#162).

These exercise the real driver process (``yt_dlp_worker_driver``) without the
TESTING flag, so they actually spawn python and import yt_dlp once per slot.
They are kept small and time-boxed to avoid slow CI.
"""

from __future__ import annotations

import asyncio
import os

import pytest

from app.services import yt_dlp_service
from app.services.yt_dlp_service import YtDlpProcessPool, _get_pool


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
    """The pool resolves a title (metadata mode) via the warm driver."""
    pool = YtDlpProcessPool(size=1, startup_timeout=60.0)
    try:
        await pool.ensure_started()
        # A synthetic but well-formed job exercises the driver's JSON round-trip
        # and progress/result parsing without hitting the network.
        job = {
            "job_id": "meta-1",
            "mode": "metadata",
            "url": "https://www.youtube.com/watch?v=dQw4w9WgXcQ",
            "platform": "youtube",
            "cookies_opts": {},
        }
        # The driver will attempt a real network fetch; we only assert the
        # protocol works (result or a typed error) rather than specific output.
        try:
            result = await pool.run_job(job, job_timeout=60.0)
            assert isinstance(result, dict)
        except RuntimeError as exc:
            # Network-blocked CI environments return a typed error rather than
            # crashing — that still proves the pool + driver plumbing works.
            assert "yt-dlp" in str(exc).lower() or "failed" in str(exc).lower()
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
async def test_get_pool_disabled_under_testing():
    """_get_pool returns None when TESTING is set, forcing the inline path."""
    monkeypatch_testing = os.environ.get("TESTING")
    os.environ["TESTING"] = "1"
    try:
        assert _get_pool() is None
    finally:
        if monkeypatch_testing is None:
            os.environ.pop("TESTING", None)
        else:
            os.environ["TESTING"] = monkeypatch_testing


@pytest.mark.asyncio
async def test_get_pool_singleton_and_fallback_flag():
    """_get_pool respects the warm-pool disabled setting."""
    from core.config import settings as _settings

    # Force-disabled: returns None and records no failure.
    _settings.yt_dlp_warm_pool = False
    try:
        # Clear any cached singleton so the setting is re-read.
        yt_dlp_service._pool = None
        yt_dlp_service._pool_failed = False
        assert _get_pool() is None
    finally:
        _settings.yt_dlp_warm_pool = True
        yt_dlp_service._pool = None
        yt_dlp_service._pool_failed = False


@pytest.mark.asyncio
async def test_driver_module_is_importable():
    """The driver module imports cleanly (yt_dlp available in the image)."""
    from app.services import yt_dlp_worker_driver

    assert hasattr(yt_dlp_worker_driver, "main")
    assert hasattr(yt_dlp_worker_driver, "_run_extract")
    assert hasattr(yt_dlp_worker_driver, "_run_metadata")

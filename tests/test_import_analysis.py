"""Tests for the zone-aware import-boundary / yt-dlp ACL verifier.

These are the negative tests the audit relies on: the enforcement is applied
mechanically by ``scripts/import_analysis.py`` (ruff's TID251 cannot express
per-directory bans), so a regression that disables a rule would otherwise go
uncaught. A real leak (e.g. ``yt_dlp`` imported outside its facade) must be
flagged; the facade and legitimate layering must stay clean.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from scripts.import_analysis import analyze_project, yt_dlp_acl_reason


def _write(root: Path, rel: str, content: str) -> Path:
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    return path


def _violations(root: Path) -> list[str]:
    return sorted(
        f"{v.path.relative_to(root)}:{v.line_number}: {v.statement} ({v.reason})"
        for v in analyze_project(root)
    )


# --------------------------------------------------------------------------
# Layering (core / app / worker)
# --------------------------------------------------------------------------
def test_core_must_not_import_app(tmp_path: Path) -> None:
    _write(tmp_path, "core/x.py", "from app.foo import bar\n")
    assert any("core must not import app or worker modules" in v for v in _violations(tmp_path))


def test_core_must_not_import_worker(tmp_path: Path) -> None:
    _write(tmp_path, "core/x.py", "from worker.foo import bar\n")
    assert any("core must not import app or worker modules" in v for v in _violations(tmp_path))


def test_app_must_not_import_worker(tmp_path: Path) -> None:
    _write(tmp_path, "app/x.py", "from worker.foo import bar\n")
    assert any("app must not import worker modules" in v for v in _violations(tmp_path))


def test_worker_must_not_import_forbidden_app_prefix(tmp_path: Path) -> None:
    _write(tmp_path, "worker/x.py", "from app.api.routes import foo\n")
    assert any("worker must not import API" in v for v in _violations(tmp_path))


def test_worker_may_import_core(tmp_path: Path) -> None:
    _write(tmp_path, "worker/x.py", "from core.config import settings\n")
    assert _violations(tmp_path) == []


def test_worker_may_import_app_services(tmp_path: Path) -> None:
    _write(tmp_path, "worker/x.py", "from app.services.queue_helpers import enqueue\n")
    assert _violations(tmp_path) == []


# --------------------------------------------------------------------------
# yt-dlp anti-corruption layer (ACL)
# --------------------------------------------------------------------------
def test_yt_dlp_allowed_only_in_facade(tmp_path: Path) -> None:
    facade = _write(tmp_path, "app/services/yt_dlp_service.py", "import yt_dlp\n")
    # The facade itself must not be flagged.
    assert yt_dlp_acl_reason(facade, tmp_path) is None
    assert [v for v in _violations(tmp_path) if "yt_dlp" in v] == []


def test_yt_dlp_banned_in_app_outside_facade(tmp_path: Path) -> None:
    _write(tmp_path, "app/x.py", "from yt_dlp import YoutubeDL\n")
    assert any("yt_dlp may be imported only from" in v for v in _violations(tmp_path))


def test_yt_dlp_banned_in_core(tmp_path: Path) -> None:
    _write(tmp_path, "core/x.py", "import yt_dlp\n")
    assert any("yt_dlp may be imported only from" in v for v in _violations(tmp_path))


def test_yt_dlp_banned_in_worker_zone(tmp_path: Path) -> None:
    # ruff lifts TID251 in worker/**, so import_analysis must enforce the ACL
    # there independently.
    _write(tmp_path, "worker/x.py", "from yt_dlp import YoutubeDL\n")
    assert any("yt_dlp may be imported only from" in v for v in _violations(tmp_path))

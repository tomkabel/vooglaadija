"""Regression tests for Story 1.6 core boundary finalization."""

import subprocess
import sys
from pathlib import Path

import pytest

from scripts import import_analysis

pytestmark = pytest.mark.slow


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def _write_source(project_root: Path, relative_path: str, source: str) -> Path:
    path = project_root / relative_path
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(source, encoding="utf-8")
    return path


def test_import_boundary_script_passes_for_current_source():
    """The committed import-boundary checker passes for current source files."""
    result = subprocess.run(
        [sys.executable, "scripts/import_analysis.py"],
        cwd=PROJECT_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stdout + result.stderr
    assert result.stdout.strip() == "Import boundary check passed."


def test_worker_deleted_shim_import_reports_actionable_failure(tmp_path):
    """The boundary checker rejects worker imports of deleted app shim modules."""
    _write_source(tmp_path, "worker/processor.py", "from app.services import redis_client\n")

    violations = import_analysis.analyze_project(tmp_path)

    assert [violation.format(tmp_path) for violation in violations] == [
        (
            "worker/processor.py:1: from app.services import redis_client "
            "(worker must not import API, schema, model, or removed shim app modules)"
        )
    ]


@pytest.mark.parametrize(
    ("relative_path", "source", "expected"),
    [
        (
            "core/config.py",
            "import app.main\n",
            "core/config.py:1: import app.main (core must not import app or worker modules)",
        ),
        (
            "core/queue.py",
            "from worker.main import run\n",
            (
                "core/queue.py:1: from worker.main import run "
                "(core must not import app or worker modules)"
            ),
        ),
        (
            "app/main.py",
            "from worker.processor import process_job\n",
            (
                "app/main.py:1: from worker.processor import process_job "
                "(app must not import worker modules)"
            ),
        ),
    ],
)
def test_boundary_checker_rejects_forbidden_layer_imports(
    tmp_path,
    relative_path,
    source,
    expected,
):
    """The boundary checker rejects forbidden direct imports between source layers."""
    _write_source(tmp_path, relative_path, source)

    violations = import_analysis.analyze_project(tmp_path)

    assert [violation.format(tmp_path) for violation in violations] == [expected]


def test_worker_allows_core_worker_and_api_independent_app_services(tmp_path):
    """The worker may import core, worker, and API-independent app service modules."""
    _write_source(
        tmp_path,
        "worker/processor.py",
        "\n".join(
            [
                "from core.queue import pop_ready_retry_jobs",
                "from worker.state import shutdown_event",
                "from app.services.pubsub_service import publish_progress_update",
                "import app.services.throttle_predictor",
                "",
            ]
        ),
    )

    assert import_analysis.analyze_project(tmp_path) == []


@pytest.mark.parametrize(
    ("source", "expected"),
    [
        (
            "from app.api.routes import downloads\n",
            (
                "worker/processor.py:1: from app.api.routes import downloads "
                "(worker must not import API, schema, model, or removed shim app modules)"
            ),
        ),
        (
            "from app.schemas.downloads import DownloadResponse\n",
            (
                "worker/processor.py:1: from app.schemas.downloads import DownloadResponse "
                "(worker must not import API, schema, model, or removed shim app modules)"
            ),
        ),
        (
            f"from {'.'.join(('app', 'models'))} import User\n",
            (
                f"worker/processor.py:1: from {'.'.join(('app', 'models'))} import User "
                "(worker must not import API, schema, model, or removed shim app modules)"
            ),
        ),
        (
            "from app import config\n",
            (
                "worker/processor.py:1: from app import config "
                "(worker must not import API, schema, model, or removed shim app modules)"
            ),
        ),
    ],
)
def test_worker_rejects_api_web_model_and_shim_imports(tmp_path, source, expected):
    """The worker rejects API, schema, model, and removed-shim app imports."""
    _write_source(tmp_path, "worker/processor.py", source)

    violations = import_analysis.analyze_project(tmp_path)

    assert [violation.format(tmp_path) for violation in violations] == [expected]


def test_import_boundary_cli_reports_deterministic_failure_output(tmp_path):
    """The import-boundary CLI returns non-zero output for discovered violations."""
    _write_source(tmp_path, "app/main.py", "import worker.processor\n")
    _write_source(tmp_path, "worker/processor.py", "from app.api.routes import downloads\n")

    result = subprocess.run(
        [sys.executable, str(PROJECT_ROOT / "scripts/import_analysis.py")],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 1
    assert result.stderr == ""
    assert result.stdout.splitlines() == [
        "app/main.py:1: import worker.processor (app must not import worker modules)",
        (
            "worker/processor.py:1: from app.api.routes import downloads "
            "(worker must not import API, schema, model, or removed shim app modules)"
        ),
    ]


def test_import_boundary_checker_ignores_comments_and_strings(tmp_path):
    """The boundary checker ignores import-like text outside AST import nodes."""
    legacy_config_text = ".".join(("app", "config"))
    _write_source(
        tmp_path,
        "core/config.py",
        f"# import {legacy_config_text}\nREFERENCE = 'from worker.main import run'\n",
    )

    assert import_analysis.analyze_project(tmp_path) == []


def test_code_boundaries_document_final_ownership_rules():
    """The root boundary document records final ownership and removed shims."""
    document = (PROJECT_ROOT / "CODEBOUNDARIES.md").read_text(encoding="utf-8")

    assert "`core/` must not import from `app/` or `worker/`" in document
    assert "`app/` must not import from `worker/`" in document
    assert "`worker/` may import from `core/`" in document
    assert "`app/services/redis_client.py`" in document

import pytest

"""Story 4.1 ownership guardrails for DownloadService."""

import ast
from pathlib import Path

import pytest

pytestmark = pytest.mark.slow



PROJECT_ROOT = Path(__file__).resolve().parents[1]
SERVICE_PATH = PROJECT_ROOT / "app/services/download_service.py"
REST_ROUTE_PATH = PROJECT_ROOT / "app/api/routes/downloads.py"
WEB_ROUTE_PATH = PROJECT_ROOT / "app/api/routes/web/web_downloads.py"


def _imported_modules(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    modules: set[str] = set()

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            modules.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            modules.add(node.module or "")

    return modules


@pytest.mark.unit
def test_download_service_exists_and_exposes_required_api() -> None:
    """DownloadService exists and exposes the epic-required public methods."""
    from app.services.download_service import DownloadService

    for method_name in (
        "create",
        "list",
        "get",
        "delete",
        "get_file_path",
        "replay_failed",
        "resolve_errors",
    ):
        assert callable(getattr(DownloadService, method_name))


@pytest.mark.unit
def test_download_service_stays_api_independent() -> None:
    """DownloadService does not import FastAPI, route modules, schemas, or templates."""
    tree = ast.parse(SERVICE_PATH.read_text(encoding="utf-8"))
    forbidden_prefixes = ("fastapi", "app.api", "app.schemas")
    forbidden_names = {"Request", "Response", "FileResponse", "HTMLResponse", "RedirectResponse"}

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                assert not alias.name.startswith(forbidden_prefixes)
                assert alias.name not in forbidden_names
        elif isinstance(node, ast.ImportFrom):
            module = node.module or ""
            assert not module.startswith(forbidden_prefixes)
            assert module != "fastapi"
            for alias in node.names:
                assert alias.name not in forbidden_names


@pytest.mark.unit
def test_routes_delegate_download_domain_work_to_service() -> None:
    """REST and Web download routes instantiate DownloadService and avoid old owner helpers."""
    rest_source = REST_ROUTE_PATH.read_text(encoding="utf-8")
    web_source = WEB_ROUTE_PATH.read_text(encoding="utf-8")

    assert "DownloadService" in rest_source
    assert "DownloadService" in web_source
    for symbol in (
        "async def _get_user_job",
        "async def _create_pending_download_job",
        "async def _best_effort_enqueue",
        "write_job_to_outbox",
        "resolve_video_title",
    ):
        assert symbol not in rest_source
        assert symbol not in web_source


@pytest.mark.unit
def test_routes_do_not_import_domain_infrastructure_owners() -> None:
    """Routes do not import outbox, queue, DLQ models, or path-validation owners directly."""
    forbidden_modules = {
        "app.services.outbox_service",
        "app.services.yt_dlp_service",
        "core.models.failed_job",
        "core.models.outbox",
        "core.queue",
        "core.utils.security",
    }

    assert _imported_modules(REST_ROUTE_PATH).isdisjoint(forbidden_modules)
    assert _imported_modules(WEB_ROUTE_PATH).isdisjoint(
        forbidden_modules | {"core.models.download_job", "sqlalchemy"}
    )


@pytest.mark.unit
def test_static_dlq_routes_are_prioritized_before_job_id_routes() -> None:
    """Static DLQ routes are registered before generic job-id routes."""
    from app.api.routes.downloads import router


    route_paths = [getattr(route, "path", "") for route in router.routes]
    first_job_route = min(
        index for index, path in enumerate(route_paths) if path.startswith("/downloads/{job_id}")
    )

    for static_path in (
        "/downloads/failed",
        "/downloads/failed/{failed_job_id}/replay",
        "/downloads/failed/replay-all",
    ):
        assert route_paths.index(static_path) < first_job_route


@pytest.mark.unit
def test_replay_all_service_retains_batch_original_job_lookup() -> None:
    """Replay-all remains batched and does not call single replay in a loop."""
    source = SERVICE_PATH.read_text(encoding="utf-8")

    assert "DownloadJob.id.in_(original_ids)" in source
    assert "originals_by_id" in source
    replay_all_source = source[source.index("async def replay_all_failed") :]
    assert "await self.replay_failed(" not in replay_all_source

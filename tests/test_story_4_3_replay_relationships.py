"""Story 4.3 relationship and replay-all guardrails."""

import inspect as python_inspect
from pathlib import Path

import pytest
from sqlalchemy import inspect

from core.models.download_job import DownloadJob
from core.models.failed_job import FailedJob
from core.models.user import User

pytestmark = pytest.mark.slow


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SERVICE_PATH = PROJECT_ROOT / "app/services/download_service.py"
ROUTE_PATH = PROJECT_ROOT / "app/api/routes/downloads.py"

EXPECTED_DOWNLOAD_JOB_COLUMNS = {
    "id",
    "user_id",
    "url",
    "status",
    "file_path",
    "title",
    "file_name",
    "error",
    "last_error",
    "error_category",
    "retry_count",
    "max_retries",
    "next_retry_at",
    "created_at",
    "updated_at",
    "completed_at",
    "expires_at",
}
EXPECTED_FAILED_JOB_COLUMNS = {
    "id",
    "original_job_id",
    "user_id",
    "url",
    "error_category",
    "retry_history",
    "final_error",
    "final_error_category",
    "retry_count",
    "max_retries_at_failure",
    "title",
    "created_at",
    "failed_at",
    "expires_at",
}


@pytest.mark.unit
def test_failed_job_original_job_relationship_targets_download_job() -> None:
    """FailedJob.original_job maps original_job_id to DownloadJob."""
    relationship = inspect(FailedJob).relationships["original_job"]

    assert relationship.mapper.class_ is DownloadJob
    assert {column.name for column in relationship.local_columns} == {"original_job_id"}
    assert {column.name for column in relationship.remote_side} == {"id"}
    assert relationship.uselist is False


@pytest.mark.unit
def test_download_job_user_relationship_targets_user() -> None:
    """DownloadJob.user maps user_id to User."""
    relationship = inspect(DownloadJob).relationships["user"]

    assert relationship.mapper.class_ is User
    assert {column.name for column in relationship.local_columns} == {"user_id"}
    assert {column.name for column in relationship.remote_side} == {"id"}
    assert relationship.uselist is False


@pytest.mark.unit
def test_relationship_only_change_preserves_model_columns() -> None:
    """Story 4.3 relationships do not introduce database columns."""
    assert set(DownloadJob.__table__.columns.keys()) == EXPECTED_DOWNLOAD_JOB_COLUMNS
    assert set(FailedJob.__table__.columns.keys()) == EXPECTED_FAILED_JOB_COLUMNS


@pytest.mark.unit
def test_replay_all_stays_service_owned_and_batched() -> None:
    """Replay-all remains service-owned, batched, and free of single replay loop calls."""
    source = SERVICE_PATH.read_text(encoding="utf-8")
    replay_all_source = source[source.index("async def replay_all_failed") :]

    assert "DownloadJob.id.in_(original_ids)" in replay_all_source
    assert "DownloadJob.user_id == self.user_id" in replay_all_source
    assert "originals_by_id" in replay_all_source
    assert "await self.replay_failed(" not in replay_all_source


@pytest.mark.unit
def test_replay_all_route_remains_thin_delegate() -> None:
    """The REST replay-all endpoint delegates to DownloadService without owning DLQ logic."""
    from app.api.routes.downloads import replay_all_failed_jobs
    from app.services.download_service import DownloadService

    route_source = python_inspect.getsource(replay_all_failed_jobs)

    assert (
        "DownloadService(db, current_user.id).replay_all_failed(category=category)" in route_source
    )
    assert 'return {"replayed": result.replayed, "total": result.total}' in route_source
    assert DownloadService.replay_all_failed.__name__ == "replay_all_failed"

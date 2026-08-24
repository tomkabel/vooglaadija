from datetime import datetime
from typing import Annotated
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.utils.validators import is_supported_url


class DownloadCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    url: Annotated[str, Field(min_length=1, max_length=2000)]

    @field_validator("url")
    @classmethod
    def validate_youtube_url(cls, v: str) -> str:
        if not is_supported_url(v):
            raise ValueError("Must be a valid supported URL")
        return v


class DownloadResponse(BaseModel):
    model_config = {"from_attributes": True}

    id: UUID
    url: str
    status: str
    title: str | None = None
    file_name: str | None = None
    error: str | None = None
    error_category: str | None = None
    retry_count: int = 0
    max_retries: int = 3
    next_retry_at: datetime | None = None
    created_at: datetime
    completed_at: datetime | None = None
    expires_at: datetime | None = None


class PaginationInfo(BaseModel):
    page: int
    per_page: int
    total: int


class DownloadListResponse(BaseModel):
    downloads: list[DownloadResponse]
    pagination: PaginationInfo


class BulkDeleteRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    job_ids: Annotated[list[UUID], Field(min_length=1, max_length=100)]


class BulkDeleteResponse(BaseModel):
    deleted: list[UUID]
    skipped: list[UUID]
    requested: int


class FailedJobResponse(BaseModel):
    model_config = {"from_attributes": True}

    id: UUID
    original_job_id: UUID | None = None
    url: str
    error_category: str
    retry_history: str | None = None
    final_error: str
    final_error_category: str
    retry_count: int
    max_retries_at_failure: int
    title: str | None = None
    created_at: datetime
    failed_at: datetime
    expires_at: datetime | None = None


class FailedJobListResponse(BaseModel):
    failed_jobs: list[FailedJobResponse]
    pagination: PaginationInfo

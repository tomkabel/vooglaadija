"""Personal Access Token schemas."""

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from app.utils.validators import validate_token_name


class PersonalAccessTokenScope:
    READ_DOWNLOADS = "read:downloads"
    WRITE_DOWNLOADS = "write:downloads"
    READ_FAILED_JOBS = "read:failed_jobs"
    WRITE_FAILED_JOBS = "write:failed_jobs"
    READ_KEYS = "read:keys"
    WRITE_KEYS = "write:keys"

    ALL_SCOPES = [
        READ_DOWNLOADS,
        WRITE_DOWNLOADS,
        READ_FAILED_JOBS,
        WRITE_FAILED_JOBS,
        READ_KEYS,
        WRITE_KEYS,
    ]


class PersonalAccessTokenCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(..., min_length=1, max_length=255, description="Human-readable name for the token")
    scopes: list[str] = Field(
        default=[PersonalAccessTokenScope.READ_DOWNLOADS],
        description="List of scopes granted to this token",
    )
    expires_in_days: int | None = Field(
        default=None,
        ge=1,
        le=365,
        description="Optional expiration in days (1-365). None means no expiration.",
    )

    @property
    def scopes_str(self) -> str:
        return ",".join(self.scopes)


class PersonalAccessTokenResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    name: str
    scopes: list[str]
    is_active: bool
    last_used_at: datetime | None
    expires_at: datetime | None
    created_at: datetime
    revoked_at: datetime | None


class PersonalAccessTokenCreateResponse(BaseModel):
    token: PersonalAccessTokenResponse
    plain_token: str = Field(
        ...,
        description="The full token value. This is only shown once at creation time.",
    )


class PersonalAccessTokenListResponse(BaseModel):
    tokens: list[PersonalAccessTokenResponse]

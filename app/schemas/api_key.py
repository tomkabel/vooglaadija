"""Schemas for personal access token (API key) management."""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Annotated
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator

from core.models.api_key import WILDCARD_SCOPE

# Concrete, documented scopes an API key can be granted. A key holding the
# wildcard scope ("*") is treated as having every scope below.
class ApiKeyScope(str, Enum):
    """Machine-facing capability scopes granted to a personal access token."""

    DOWNLOADS_READ = "downloads:read"
    DOWNLOADS_WRITE = "downloads:write"
    KEYS_ADMIN = "keys:admin"


ALL_API_KEY_SCOPES: list[str] = [scope.value for scope in ApiKeyScope]
KNOWN_SCOPES: set[str] = {WILDCARD_SCOPE, *ALL_API_KEY_SCOPES}


class ApiKeyCreate(BaseModel):
    """Request body for creating a personal access token."""

    model_config = ConfigDict(extra="forbid")

    name: Annotated[str, Field(min_length=1, max_length=64)]
    scopes: Annotated[list[str], Field(min_length=1, max_length=32)] = [WILDCARD_SCOPE]
    expires_in_days: Annotated[int | None, Field(ge=1, le=3650)] = None

    @field_validator("scopes")
    @classmethod
    def validate_scopes(cls, values: list[str]) -> list[str]:
        normalized: list[str] = []
        for raw in values:
            scope = raw.strip()
            if not scope:
                continue
            if scope not in KNOWN_SCOPES:
                raise ValueError(
                    f"Unknown scope '{scope}'. Valid scopes: {sorted(KNOWN_SCOPES)}"
                )
            normalized.append(scope)
        if not normalized:
            normalized = [WILDCARD_SCOPE]
        # De-duplicate while preserving order.
        seen: set[str] = set()
        deduped: list[str] = []
        for scope in normalized:
            if scope not in seen:
                seen.add(scope)
                deduped.append(scope)
        # Wildcard subsumes any concrete scope.
        if WILDCARD_SCOPE in deduped:
            return [WILDCARD_SCOPE]
        return deduped


class ApiKeyResponse(BaseModel):
    """Public representation of an API key (never includes the raw secret)."""

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    name: str
    key_prefix: str
    scopes: list[str]
    created_at: datetime
    expires_at: datetime | None = None
    last_used_at: datetime | None = None
    revoked_at: datetime | None = None
    is_active: bool = True

    @field_validator("scopes", mode="before")
    @classmethod
    def _coerce_scopes(cls, value: object) -> object:
        # The ORM column stores scopes as a comma-separated string.
        if isinstance(value, str):
            return [scope.strip() for scope in value.split(",") if scope.strip()]
        return value


class ApiKeyCreatedResponse(ApiKeyResponse):
    """API key response that includes the raw token exactly once."""

    token: str


class ApiKeyListResponse(BaseModel):
    """List of the caller's API keys."""

    keys: list[ApiKeyResponse]
    total: int

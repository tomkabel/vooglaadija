"""Personal access token (API key) management endpoints."""

from fastapi import APIRouter, HTTPException, Request, status

from app.api.dependencies import CurrentUser, DbSession, KeysAdminScope
from app.api.rate_limit_config import limiter
from app.schemas.api_key import (
    ALL_API_KEY_SCOPES,
    ApiKeyCreate,
    ApiKeyCreatedResponse,
    ApiKeyListResponse,
    ApiKeyResponse,
)
from app.schemas.error import (
    ErrorCode,
    error_response_doc,
    success_response_doc,
)
from app.services.api_key_service import ApiKeyService

router = APIRouter(prefix="/keys", tags=["keys"])


@router.post(
    "",
    response_model=ApiKeyCreatedResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Create a personal access token",
    description=(
        "Create a long-lived, scoped API key (PAT). The raw token is returned "
        "exactly once and cannot be recovered. Requires the `keys:admin` scope "
        "when authenticated with a PAT; JWT sessions are always allowed."
    ),
    responses={
        201: success_response_doc(
            "API key created",
            {
                "id": "7e9c1a2b-3f4d-4a6b-9c0e-1f2a3b4c5d6e",
                "name": "ci-pipeline",
                "key_prefix": "vlj_pat_9f3a",
                "token": "vlj_pat_9f3a...full-token...",
                "scopes": ["downloads:read", "downloads:write"],
                "created_at": "2026-08-24T12:00:00Z",
                "expires_at": None,
                "last_used_at": None,
                "revoked_at": None,
                "is_active": True,
            },
        ),
        401: error_response_doc(
            "Unauthorized",
            ErrorCode.UNAUTHORIZED,
            "Could not validate credentials",
        ),
        403: error_response_doc(
            "Forbidden",
            ErrorCode.FORBIDDEN,
            "Insufficient scope: requires 'keys:admin'",
        ),
        422: error_response_doc(
            "Validation error",
            ErrorCode.VALIDATION_ERROR,
            "Request validation failed",
        ),
    },
)
@limiter.limit("20/minute")
async def create_api_key(
    request: Request,
    data: ApiKeyCreate,
    current_user: CurrentUser,
    db: DbSession,
    _: None = KeysAdminScope,
) -> ApiKeyCreatedResponse:
    """Create a new personal access token for the authenticated user."""
    api_key, raw_token = await ApiKeyService(db).create(
        user_id=current_user.id,
        name=data.name,
        scopes=data.scopes,
        expires_in_days=data.expires_in_days,
    )
    return ApiKeyCreatedResponse(
        id=api_key.id,
        name=api_key.name,
        key_prefix=api_key.key_prefix,
        token=raw_token,
        scopes=api_key.scopes_list,
        created_at=api_key.created_at,
        expires_at=api_key.expires_at,
        last_used_at=api_key.last_used_at,
        revoked_at=api_key.revoked_at,
        is_active=api_key.is_active,
    )


@router.get(
    "",
    response_model=ApiKeyListResponse,
    summary="List personal access tokens",
    description="Return all API keys owned by the authenticated user, including revoked ones.",
    responses={
        200: success_response_doc(
            "List of API keys",
            {
                "keys": [
                    {
                        "id": "7e9c1a2b-3f4d-4a6b-9c0e-1f2a3b4c5d6e",
                        "name": "ci-pipeline",
                        "key_prefix": "vlj_pat_9f3a",
                        "scopes": ["downloads:read", "downloads:write"],
                        "created_at": "2026-08-24T12:00:00Z",
                        "expires_at": None,
                        "last_used_at": None,
                        "revoked_at": None,
                        "is_active": True,
                    },
                ],
                "total": 1,
            },
        ),
        401: error_response_doc(
            "Unauthorized",
            ErrorCode.UNAUTHORIZED,
            "Could not validate credentials",
        ),
    },
)
async def list_api_keys(
    current_user: CurrentUser,
    db: DbSession,
) -> ApiKeyListResponse:
    """List the caller's API keys."""
    keys = await ApiKeyService(db).list_for_user(current_user.id)
    return ApiKeyListResponse(
        keys=[ApiKeyResponse.model_validate(key) for key in keys],
        total=len(keys),
    )


@router.delete(
    "/{key_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Revoke a personal access token",
    description=(
        "Revoke an API key owned by the authenticated user. Revocation takes "
        "effect immediately. Requires the `keys:admin` scope when authenticated "
        "with a PAT."
    ),
    responses={
        204: {"description": "API key revoked"},
        401: error_response_doc(
            "Unauthorized",
            ErrorCode.UNAUTHORIZED,
            "Could not validate credentials",
        ),
        403: error_response_doc(
            "Forbidden",
            ErrorCode.FORBIDDEN,
            "Insufficient scope: requires 'keys:admin'",
        ),
        404: error_response_doc(
            "Not found",
            ErrorCode.NOT_FOUND,
            "API key not found",
            details={"key_id": "unknown-id"},
        ),
    },
)
@limiter.limit("20/minute")
async def revoke_api_key(
    key_id: str,
    request: Request,
    current_user: CurrentUser,
    db: DbSession,
    _: None = KeysAdminScope,
) -> None:
    """Revoke (immediately disable) an API key."""
    from uuid import UUID

    try:
        key_uuid = UUID(key_id)
    except (ValueError, TypeError) as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="API key not found",
        ) from exc

    revoked = await ApiKeyService(db).revoke(current_user.id, key_uuid)
    if not revoked:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="API key not found",
        )

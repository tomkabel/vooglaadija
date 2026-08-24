"""Personal Access Token management endpoints."""

from uuid import UUID

from fastapi import APIRouter, HTTPException, Request, status

from app.api.dependencies import CurrentUser, DbSession
from app.api.rate_limit_config import limiter
from app.schemas.error import ErrorCode, error_response_doc, success_response_doc
from app.schemas.personal_access_token import (
    PersonalAccessTokenCreate,
    PersonalAccessTokenCreateResponse,
    PersonalAccessTokenListResponse,
    PersonalAccessTokenResponse,
    PersonalAccessTokenScope,
)
from app.services.pat_service import PATService

router = APIRouter(prefix="/tokens", tags=["tokens"])


def _pat_to_response(pat) -> PersonalAccessTokenResponse:
    return PersonalAccessTokenResponse(
        id=pat.id,
        name=pat.name,
        scopes=pat.scopes.split(",") if pat.scopes else [],
        is_active=pat.is_active and pat.revoked_at is None,
        last_used_at=pat.last_used_at,
        expires_at=pat.expires_at,
        created_at=pat.created_at,
        revoked_at=pat.revoked_at,
    )


@router.post(
    "",
    response_model=PersonalAccessTokenCreateResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Create a Personal Access Token",
    description="Generate a new long-lived Personal Access Token for machine/agents. "
    "The token is only shown once at creation time.",
    responses={
        201: success_response_doc(
            "Token created successfully",
            {
                "token": {
                    "id": "f47ac10b-58cc-4372-a567-0e02b2c3d479",
                    "name": "MCP Server Token",
                    "scopes": ["read:downloads", "write:downloads"],
                    "is_active": True,
                    "last_used_at": None,
                    "expires_at": None,
                    "created_at": "2026-08-24T10:00:00Z",
                    "revoked_at": None,
                },
                "plain_token": "vpat_live_xxxxxxxxxxxxxxxxxxxx",
            },
        ),
        401: error_response_doc(
            "Unauthorized",
            ErrorCode.UNAUTHORIZED,
            "Could not validate credentials",
        ),
        422: error_response_doc(
            "Validation error",
            ErrorCode.VALIDATION_ERROR,
            "Request validation failed",
        ),
        429: error_response_doc(
            "Rate limit exceeded",
            ErrorCode.RATE_LIMIT_EXCEEDED,
            "Rate limit exceeded. Try again in 42 seconds.",
        ),
    },
)
@limiter.limit("10/minute")
async def create_token(
    request: Request,
    token_data: PersonalAccessTokenCreate,
    current_user: CurrentUser,
    db: DbSession,
) -> PersonalAccessTokenCreateResponse:
    service = PATService(db)
    pat, plain_token = await service.create_token(
        user_id=current_user.id,
        name=token_data.name,
        scopes=token_data.scopes,
        expires_in_days=token_data.expires_in_days,
    )
    await db.commit()

    return PersonalAccessTokenCreateResponse(
        token=_pat_to_response(pat),
        plain_token=plain_token,
    )


@router.get(
    "",
    response_model=PersonalAccessTokenListResponse,
    summary="List Personal Access Tokens",
    description="List all Personal Access Tokens for the authenticated user.",
    responses={
        200: success_response_doc(
            "List of tokens",
            {
                "tokens": [
                    {
                        "id": "f47ac10b-58cc-4372-a567-0e02b2c3d479",
                        "name": "MCP Server Token",
                        "scopes": ["read:downloads"],
                        "is_active": True,
                        "last_used_at": "2026-08-24T10:30:00Z",
                        "expires_at": None,
                        "created_at": "2026-08-24T10:00:00Z",
                        "revoked_at": None,
                    }
                ],
            },
        ),
        401: error_response_doc(
            "Unauthorized",
            ErrorCode.UNAUTHORIZED,
            "Could not validate credentials",
        ),
    },
)
async def list_tokens(
    current_user: CurrentUser,
    db: DbSession,
) -> PersonalAccessTokenListResponse:
    service = PATService(db)
    tokens = await service.list_tokens(current_user.id)
    return PersonalAccessTokenListResponse(tokens=[_pat_to_response(t) for t in tokens])


@router.delete(
    "/{token_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Revoke a Personal Access Token",
    description="Revoke a Personal Access Token. This action is irreversible.",
    responses={
        204: {"description": "Token revoked successfully"},
        401: error_response_doc(
            "Unauthorized",
            ErrorCode.UNAUTHORIZED,
            "Could not validate credentials",
        ),
        404: error_response_doc(
            "Token not found",
            ErrorCode.NOT_FOUND,
            "Token not found",
        ),
    },
)
async def revoke_token(
    token_id: UUID,
    current_user: CurrentUser,
    db: DbSession,
) -> None:
    service = PATService(db)
    revoked = await service.revoke_token(current_user.id, token_id)
    if not revoked:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Token not found",
        )
    await db.commit()


@router.get(
    "/scopes",
    summary="List available token scopes",
    description="Returns all available scopes that can be assigned to a Personal Access Token.",
    responses={
        200: success_response_doc(
            "Available scopes",
            {"scopes": PersonalAccessTokenScope.ALL_SCOPES},
        ),
    },
)
async def list_scopes() -> dict[str, list[str]]:
    return {"scopes": PersonalAccessTokenScope.ALL_SCOPES}

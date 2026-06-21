from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any
from uuid import UUID, uuid4

from jose import JWTError, jwt

from app.config import settings

if TYPE_CHECKING:
    from starlette.responses import Response

ALGORITHM = "HS256"
ACCESS_TOKEN_TYPE = "access"
REFRESH_TOKEN_TYPE = "refresh"


def _make_token(
    subject: UUID | str,
    token_type: str,
    lifetime: timedelta,
    extra_claims: dict[str, Any] | None = None,
) -> str:
    expire = datetime.now(UTC) + lifetime
    payload: dict[str, Any] = {
        "sub": str(subject),
        "exp": expire,
        "type": token_type,
        "iat": datetime.now(UTC),
        "jti": uuid4().hex,
    }
    if extra_claims:
        payload.update(extra_claims)
    return jwt.encode(payload, settings.secret_key, algorithm=ALGORITHM)


def create_access_token(
    subject: UUID | str,
    email: str | None = None,
    token_version: int = 1,
) -> str:
    extra: dict[str, Any] = {"user_id": str(subject)}
    if email:
        extra["email"] = email
    if token_version > 1:
        extra["ver"] = token_version
    return _make_token(
        subject,
        ACCESS_TOKEN_TYPE,
        timedelta(minutes=settings.access_token_expire_minutes),
        extra_claims=extra,
    )


def create_refresh_token(
    subject: UUID | str,
    token_version: int = 1,
) -> str:
    extra: dict[str, Any] = {}
    if token_version > 1:
        extra["ver"] = token_version
    extra["user_id"] = str(subject)
    return _make_token(
        subject,
        REFRESH_TOKEN_TYPE,
        timedelta(days=settings.refresh_token_expire_days),
        extra_claims=extra,
    )


def verify_token(token: str, expected_type: str | None = None) -> dict[str, Any] | None:
    try:
        payload = jwt.decode(
            token,
            settings.secret_key,
            algorithms=[ALGORITHM],
            options={
                "verify_exp": True,
                "verify_signature": True,
                "require": ["sub", "exp"],
            },
        )
        if expected_type is not None and payload.get("type") != expected_type:
            return None
        return payload
    except JWTError:
        return None


def set_token_cookies(
    response: "Response", access_token: str, refresh_token: str, secure: bool = True
) -> None:
    response.set_cookie(
        key="access_token",
        value=access_token,
        httponly=True,
        secure=secure,
        samesite="lax",
        path="/",
        max_age=settings.access_token_expire_minutes * 60,
    )
    response.set_cookie(
        key="refresh_token",
        value=refresh_token,
        httponly=True,
        secure=secure,
        samesite="lax",
        path="/",
        max_age=settings.refresh_token_expire_days * 24 * 60 * 60,
    )


def clear_token_cookies(response: "Response") -> None:
    response.delete_cookie(key="access_token", path="/")
    response.delete_cookie(key="refresh_token", path="/")

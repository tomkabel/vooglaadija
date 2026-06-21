"""Global API exception handlers."""

from collections.abc import Awaitable, Callable
from typing import cast

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from slowapi.errors import RateLimitExceeded
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.responses import Response

from app.api.rate_limit_config import rate_limit_exceeded_handler
from app.schemas.error import ErrorCode, error_response_dict
from core.logging_config import get_logger

logger = get_logger(__name__)
ExceptionHandler = Callable[[Request, Exception], Response | Awaitable[Response]]


async def http_exception_handler(request: Request, exc: StarletteHTTPException) -> JSONResponse:
    """Handle HTTP exceptions with standardized error response."""
    request_id = getattr(request.state, "request_id", "unknown")
    error_code_map = {
        400: ErrorCode.VALIDATION_ERROR,
        401: ErrorCode.UNAUTHORIZED,
        403: ErrorCode.FORBIDDEN,
        404: ErrorCode.NOT_FOUND,
        405: ErrorCode.VALIDATION_ERROR,
        406: ErrorCode.VALIDATION_ERROR,
        409: ErrorCode.RESOURCE_CONFLICT,
        415: ErrorCode.VALIDATION_ERROR,
        422: ErrorCode.VALIDATION_ERROR,
        429: ErrorCode.RATE_LIMIT_EXCEEDED,
        500: ErrorCode.INTERNAL_ERROR,
        503: ErrorCode.SERVICE_UNAVAILABLE,
    }
    code = error_code_map.get(
        exc.status_code,
        ErrorCode.VALIDATION_ERROR if 400 <= exc.status_code < 500 else ErrorCode.INTERNAL_ERROR,
    )

    logger.warning(
        "http_exception",
        status_code=exc.status_code,
        error_code=code.value,
        detail=str(exc.detail),
        request_id=request_id,
    )

    return JSONResponse(
        status_code=exc.status_code,
        content=error_response_dict(code, str(exc.detail)),
        headers=exc.headers or None,
    )


async def validation_exception_handler(
    request: Request, exc: RequestValidationError
) -> JSONResponse:
    """Handle validation errors with standardized error response."""
    request_id = getattr(request.state, "request_id", "unknown")
    errors = [
        {
            "field": ".".join(str(loc) for loc in error["loc"] if loc != "body"),
            "message": error["msg"],
            "type": error["type"],
        }
        for error in exc.errors()
    ]

    logger.warning(
        "validation_error",
        error_count=len(errors),
        request_id=request_id,
    )

    return JSONResponse(
        status_code=422,
        content=error_response_dict(
            ErrorCode.VALIDATION_ERROR,
            "Request validation failed",
            details={"validation_errors": errors},
        ),
    )


async def general_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    """Handle unexpected exceptions with standardized error response."""
    request_id = getattr(request.state, "request_id", "unknown")
    logger.error(
        "unhandled_exception",
        exception_type=type(exc).__name__,
        exception_message=str(exc),
        request_id=request_id,
        exc_info=True,
    )

    return JSONResponse(
        status_code=500,
        content=error_response_dict(ErrorCode.INTERNAL_ERROR, "An internal error occurred"),
        headers={"X-Request-ID": request_id},
    )


def register_exception_handlers(app: FastAPI) -> None:
    """Register rate-limit and global exception handlers."""
    app.add_exception_handler(RateLimitExceeded, rate_limit_exceeded_handler)
    app.add_exception_handler(
        StarletteHTTPException, cast(ExceptionHandler, http_exception_handler)
    )
    app.add_exception_handler(
        RequestValidationError,
        cast(ExceptionHandler, validation_exception_handler),
    )
    app.add_exception_handler(Exception, cast(ExceptionHandler, general_exception_handler))

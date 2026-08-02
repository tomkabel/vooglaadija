"""Request body size limiting middleware."""

from collections.abc import Awaitable, Callable

from fastapi import Request
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import Response

from app.schemas.error import ErrorCode, error_response_dict


class RequestBodySizeMiddleware(BaseHTTPMiddleware):
    """Limit incoming request body size to prevent resource exhaustion."""

    MAX_BODY_SIZE = 1024 * 1024

    async def dispatch(
        self, request: Request, call_next: Callable[[Request], Awaitable[Response]],
    ) -> Response:
        """
        Limit request bodies to the configured maximum size.
        
        Parameters:
            request (Request): The incoming HTTP request.
        
        Returns:
            Response: The downstream response, or a 413 response when the declared body size exceeds the limit.
        """
        if request.method in ("GET", "HEAD", "OPTIONS"):
            return await call_next(request)

        content_length_str = request.headers.get("content-length")
        if content_length_str:
            try:
                content_length = int(content_length_str)
                if content_length > self.MAX_BODY_SIZE:
                    return JSONResponse(
                        status_code=413,
                        content=error_response_dict(
                            ErrorCode.VALIDATION_ERROR,
                            f"Request body too large. Maximum size is {self.MAX_BODY_SIZE // 1024}KB.",
                        ),
                    )
            except (ValueError, TypeError):
                pass

        return await call_next(request)

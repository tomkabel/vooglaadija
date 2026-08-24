"""MCP (Model Context Protocol) SSE endpoint.

Exposes an MCP-compatible SSE transport for HTTP-based agent integration.
This allows AI agents (Claude Desktop, Cursor, etc.) to connect to Vooglaadija
using the Model Context Protocol over Server-Sent Events.

Reference: https://modelcontextprotocol.io/docs/concepts/transports
"""

import json
from collections import defaultdict
from typing import Any
from uuid import UUID

from fastapi import Depends
from pydantic import BaseModel
from sse_starlette import EventSourceResponse, ServerSentEvent
from starlette.responses import JSONResponse

from app.api.dependencies import CurrentUserWithPAT, DbSession
from app.schemas.download import DownloadCreate
from app.services.download_service import (
    DownloadNotFoundError,
    DownloadService,
)
from app.utils.validators import is_supported_url
from core.logging_config import get_logger

logger = get_logger(__name__)

router = APIRouter(prefix="/mcp", tags=["mcp"])


class MCPToolRequest(BaseModel):
    """MCP tool call request."""

    jsonrpc: str = "2.0"
    id: str | int | None = None
    method: str = "tools/call"
    params: dict[str, Any] | None = None


class MCPToolResponse(BaseModel):
    """MCP tool call response."""

    jsonrpc: str = "2.0"
    id: str | int | None = None
    result: dict[str, Any] | None = None
    error: dict[str, Any] | None = None


# Session state per connected MCP client
_mcp_sessions: dict[str, dict[str, Any]] = defaultdict(dict)


@router.get("/sse")
async def mcp_sse_endpoint(request: Request, current_user: CurrentUserWithPAT):
    """MCP SSE transport endpoint.

    Provides a Server-Sent Events stream for MCP protocol communication.
    AI agents connect here to send tool calls and receive responses.

    The endpoint requires authentication via either JWT or Personal Access Token.

    Returns:
        EventSourceResponse: SSE stream for MCP protocol messages.
    """
    session_id = request.query_params.get("session_id", "default")

    async def event_generator():
        # Send initial endpoint event with session info
        endpoint_data = {
            "type": "endpoint",
            "session_id": session_id,
            "user_id": str(current_user.id),
            "tools": [
                "create_download",
                "list_downloads",
                "get_download",
                "retry_download",
                "list_failed_jobs",
                "delete_download",
            ],
        }
        yield ServerSentEvent(event="endpoint", data=json.dumps(endpoint_data))

        # Keep connection alive with periodic pings
        import asyncio

        try:
            while True:
                if await request.is_disconnected():
                    break
                await asyncio.sleep(30)
                yield ServerSentEvent(event="ping", data="{}")
        except Exception:
            pass

    return EventSourceResponse(
        event_generator(),
        media_type="text/event-stream",
        ping=15,
    )


@router.post("/messages")
async def mcp_messages_endpoint(
    request: Request,
    current_user: CurrentUserWithPAT,
    db: DbSession,
):
    """MCP tool execution endpoint.

    Receives MCP tool call requests, executes them against the Vooglaadija API,
    and returns structured responses.

    Returns:
        JSONResponse: MCP tool call response with result or error.
    """
    body = await request.json()

    req = MCPToolRequest(**body)
    tool_name = (req.params or {}).get("name", "")
    arguments = (req.params or {}).get("arguments", {})

    result = await _execute_tool(tool_name, arguments, current_user, db)

    if "error" in result:
        return JSONResponse(
            content=MCPToolResponse(
                id=req.id,
                error={"code": -32603, "message": result["error"]},
            ).model_dump()
        )

    return JSONResponse(
        content=MCPToolResponse(
            id=req.id,
            result={"content": [{"type": "text", "text": result["text"]}]},
        ).model_dump()
    )


@router.get("")
async def mcp_info():
    """MCP server information endpoint.

    Returns server metadata for MCP client discovery.
    """
    return {
        "name": "vooglaadija",
        "version": "1.0.0",
        "description": "Vooglaadija Media Link Processor - MCP Server",
        "capabilities": {
            "tools": {
                "listChanged": False,
            },
            "resources": {
                "subscribe": False,
                "listChanged": False,
            },
            "prompts": {
                "listChanged": False,
            },
        },
        "transports": {
            "sse": "/mcp/sse",
            "http": "/mcp/messages",
        },
        "tools": [
            {
                "name": "create_download",
                "description": "Create a new download job from a video URL",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "url": {
                            "type": "string",
                            "description": "The video URL to download",
                        }
                    },
                    "required": ["url"],
                },
            },
            {
                "name": "list_downloads",
                "description": "List all download jobs for the authenticated user",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "limit": {
                            "type": "integer",
                            "description": "Maximum number of jobs to return (1-100)",
                            "minimum": 1,
                            "maximum": 100,
                        }
                    },
                },
            },
            {
                "name": "get_download",
                "description": "Get details of a specific download job",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "job_id": {
                            "type": "string",
                            "description": "The UUID of the download job",
                        }
                    },
                    "required": ["job_id"],
                },
            },
            {
                "name": "retry_download",
                "description": "Retry a failed download job",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "job_id": {
                            "type": "string",
                            "description": "The UUID of the failed job to retry",
                        }
                    },
                    "required": ["job_id"],
                },
            },
            {
                "name": "list_failed_jobs",
                "description": "List failed jobs from the Dead Letter Queue",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "limit": {
                            "type": "integer",
                            "description": "Maximum number of failed jobs (1-100)",
                            "minimum": 1,
                            "maximum": 100,
                        }
                    },
                },
            },
            {
                "name": "delete_download",
                "description": "Delete a download job",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "job_id": {
                            "type": "string",
                            "description": "The UUID of the job to delete",
                        }
                    },
                    "required": ["job_id"],
                },
            },
        ],
        "resources": [
            {
                "uri": "vooglaadija://user/profile",
                "name": "User Profile",
                "description": "Current user profile information",
                "mimeType": "application/json",
            },
            {
                "uri": "vooglaadija://downloads/summary",
                "name": "Downloads Summary",
                "description": "Summary statistics of download jobs",
                "mimeType": "application/json",
            },
        ],
    }


async def _execute_tool(
    name: str,
    arguments: dict[str, Any],
    user: Any,
    db: Any,
) -> dict[str, Any]:
    """Execute an MCP tool call."""
    try:
        service = DownloadService(db, user.id)

        if name == "create_download":
            url = arguments["url"]
            if not is_supported_url(url):
                return {"error": "Invalid URL: Must be a valid supported URL"}
            try:
                job = await service.create(url)
                return {
                    "text": f"Download job created successfully.\n"
                    f"Job ID: {job.id}\n"
                    f"URL: {job.url}\n"
                    f"Status: {job.status}"
                }
            except Exception as e:
                return {"error": f"Failed to create download: {e}"}

        if name == "list_downloads":
            limit = arguments.get("limit", 20)
            page = await service.list(page=1, per_page=limit)
            jobs = page.jobs
            if not jobs:
                return {"text": "No download jobs found."}
            lines = [f"Found {len(jobs)} download job(s):"]
            for job in jobs:
                lines.append(f"  [{job.status}] {job.id} - {job.url}")
            return {"text": "\n".join(lines)}

        if name == "get_download":
            try:
                job = await service.get(UUID(arguments["job_id"]))
                return {
                    "text": f"Job ID: {job.id}\n"
                    f"URL: {job.url}\n"
                    f"Status: {job.status}\n"
                    f"File: {job.file_name or 'N/A'}\n"
                    f"Created: {job.created_at}\n"
                    f"Completed: {job.completed_at or 'N/A'}"
                }
            except DownloadNotFoundError:
                return {"error": f"Job not found: {arguments['job_id']}"}

        if name == "retry_download":
            try:
                job = await service.retry(UUID(arguments["job_id"]))
                return {
                    "text": f"Job {job.id} queued for retry.\n"
                    f"Status: {job.status}"
                }
            except DownloadNotFoundError:
                return {"error": f"Job not found: {arguments['job_id']}"}

        if name == "list_failed_jobs":
            limit = arguments.get("limit", 20)
            page = await service.list_failed(page=1, per_page=limit)
            jobs = page.failed_jobs
            if not jobs:
                return {"text": "No failed jobs found."}
            lines = [f"Found {len(jobs)} failed job(s):"]
            for job in jobs:
                lines.append(f"  {job.id} - {job.url}")
                if job.final_error:
                    lines.append(f"    Error: {job.final_error}")
            return {"text": "\n".join(lines)}

        if name == "delete_download":
            try:
                await service.delete(UUID(arguments["job_id"]))
                return {"text": f"Job {arguments['job_id']} deleted successfully."}
            except DownloadNotFoundError:
                return {"error": f"Job not found: {arguments['job_id']}"}

        return {"error": f"Unknown tool: {name}"}

    except Exception as e:
        logger.error("mcp_tool_execution_error", tool=name, error=str(e))
        return {"error": f"Tool execution error: {str(e)}"}

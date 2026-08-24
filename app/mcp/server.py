"""Model Context Protocol server for Vooglaadija.

Exposes download job management capabilities as MCP tools, enabling
AI agents to interact with the service using the Model Context Protocol.

Reference: https://modelcontextprotocol.io/
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp.types import Resource, TextContent, Tool

from core.config import settings
from core.logging_config import get_logger

if TYPE_CHECKING:
    pass

logger = get_logger(__name__)

server: Server = Server("vooglaadija-mcp")


@server.list_tools()
async def list_tools() -> list[Tool]:
    """List available MCP tools for agentic consumption."""
    return [
        Tool(
            name="create_download",
            description="Create a new download job from a video URL. "
            "Supports YouTube and other platforms supported by yt-dlp.",
            inputSchema={
                "type": "object",
                "properties": {
                    "url": {
                        "type": "string",
                        "description": "The video URL to download (e.g., YouTube URL)",
                    },
                },
                "required": ["url"],
            },
        ),
        Tool(
            name="list_downloads",
            description="List all download jobs for the authenticated user. "
            "Returns job ID, URL, status, and file information.",
            inputSchema={
                "type": "object",
                "properties": {
                    "limit": {
                        "type": "integer",
                        "description": "Maximum number of jobs to return (1-100). Default: 20.",
                        "minimum": 1,
                        "maximum": 100,
                    },
                },
            },
        ),
        Tool(
            name="get_download",
            description="Get details of a specific download job by its ID.",
            inputSchema={
                "type": "object",
                "properties": {
                    "job_id": {
                        "type": "string",
                        "description": "The UUID of the download job",
                    },
                },
                "required": ["job_id"],
            },
        ),
        Tool(
            name="retry_download",
            description="Retry a failed download job.",
            inputSchema={
                "type": "object",
                "properties": {
                    "job_id": {
                        "type": "string",
                        "description": "The UUID of the failed download job to retry",
                    },
                },
                "required": ["job_id"],
            },
        ),
        Tool(
            name="list_failed_jobs",
            description="List failed jobs from the Dead Letter Queue (DLQ).",
            inputSchema={
                "type": "object",
                "properties": {
                    "limit": {
                        "type": "integer",
                        "description": "Maximum number of failed jobs to return (1-100). Default: 20.",
                        "minimum": 1,
                        "maximum": 100,
                    },
                },
            },
        ),
        Tool(
            name="delete_download",
            description="Delete a download job and its associated file.",
            inputSchema={
                "type": "object",
                "properties": {
                    "job_id": {
                        "type": "string",
                        "description": "The UUID of the download job to delete",
                    },
                },
                "required": ["job_id"],
            },
        ),
    ]


@server.call_tool()
async def call_tool(name: str, arguments: dict[str, Any]) -> list[TextContent]:
    """Handle MCP tool calls by proxying to the Vooglaadija REST API."""
    import httpx

    api_key = settings.mcp_api_key
    base_url = settings.mcp_base_url

    headers = {}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"

    error_payload = {
        "error_code": "INTERNAL_ERROR",
        "retryable": False,
        "suggestion": None,
    }

    try:
        async with httpx.AsyncClient(base_url=base_url, headers=headers, timeout=30.0) as client:
            if name == "create_download":
                response = await client.post("/api/v1/downloads", json={"url": arguments["url"]})
                data = _handle_response(response, error_payload)
                return [TextContent(type="text", text=_format_download_result(data))]

            if name == "list_downloads":
                limit = arguments.get("limit", 20)
                response = await client.get("/api/v1/downloads", params={"limit": limit})
                data = _handle_response(response, error_payload)
                return [TextContent(type="text", text=_format_download_list(data))]

            if name == "get_download":
                job_id = arguments["job_id"]
                response = await client.get(f"/api/v1/downloads/{job_id}")
                data = _handle_response(response, error_payload)
                return [TextContent(type="text", text=_format_download_result(data))]

            if name == "retry_download":
                job_id = arguments["job_id"]
                response = await client.post(f"/api/v1/downloads/{job_id}/retry")
                data = _handle_response(response, error_payload)
                return [TextContent(type="text", text=_format_download_result(data))]

            if name == "list_failed_jobs":
                limit = arguments.get("limit", 20)
                response = await client.get("/api/v1/downloads/failed", params={"limit": limit})
                data = _handle_response(response, error_payload)
                return [TextContent(type="text", text=_format_failed_jobs(data))]

            if name == "delete_download":
                job_id = arguments["job_id"]
                response = await client.delete(f"/api/v1/downloads/{job_id}")
                if response.status_code == 204:
                    return [TextContent(type="text", text=f"Job {job_id} deleted successfully.")]
                data = _handle_response(response, error_payload)
                return [TextContent(type="text", text=str(data))]

            error_payload["error_code"] = "TOOL_NOT_FOUND"
            error_payload["suggestion"] = f"Unknown tool: {name}"
            return [TextContent(type="text", text=_format_error(error_payload))]

    except httpx.RequestError as exc:
        error_payload["error_code"] = "CONNECTION_ERROR"
        error_payload["retryable"] = True
        error_payload["suggestion"] = str(exc)
        return [TextContent(type="text", text=_format_error(error_payload))]


def _handle_response(response: Any, error_payload: dict[str, Any]) -> dict[str, Any]:
    """Handle HTTP response and return parsed data or error."""
    if response.status_code >= 400:
        error_payload["error_code"] = _status_to_error_code(response.status_code)
        error_payload["retryable"] = response.status_code >= 500
        try:
            error_payload["suggestion"] = response.json().get("error", {}).get("message", response.text)
        except Exception:
            error_payload["suggestion"] = response.text
        raise MCPToolError(_format_error(error_payload))
    return response.json()


class MCPToolError(Exception):
    """Exception raised when an MCP tool call fails."""


def _status_to_error_code(status_code: int) -> str:
    """Map HTTP status to error code."""
    mapping = {
        400: "INVALID_REQUEST",
        401: "UNAUTHORIZED",
        403: "FORBIDDEN",
        404: "RESOURCE_NOT_FOUND",
        429: "RATE_LIMIT_EXCEEDED",
        500: "INTERNAL_ERROR",
        503: "SERVICE_UNAVAILABLE",
    }
    return mapping.get(status_code, "UNKNOWN_ERROR")


def _format_error(error_payload: dict[str, Any]) -> str:
    """Format error payload as a structured string for agents."""
    parts = [f"Error: {error_payload['error_code']}"]
    if error_payload.get("suggestion"):
        parts.append(f"Suggestion: {error_payload['suggestion']}")
    parts.append(f"Retryable: {error_payload.get('retryable', False)}")
    return " | ".join(parts)


def _format_download_result(data: dict[str, Any]) -> str:
    """Format a download job result for MCP response."""
    status = data.get("status", "unknown")
    job_id = data.get("id", "unknown")
    url = data.get("url", "unknown")

    parts = [f"Job ID: {job_id}", f"URL: {url}", f"Status: status"]

    if data.get("file_name"):
        parts.append(f"File: {data['file_name']}")
    if data.get("error"):
        parts.append(f"Error: {data['error']}")
    if data.get("created_at"):
        parts.append(f"Created: {data['created_at']}")
    if data.get("completed_at"):
        parts.append(f"Completed: {data['completed_at']}")

    return "\n".join(parts)


def _format_download_list(data: Any) -> str:
    """Format a list of download jobs for MCP response."""
    if isinstance(data, dict) and "items" in data:
        items = data["items"]
    elif isinstance(data, list):
        items = data
    else:
        items = []

    if not items:
        return "No download jobs found."

    lines = [f"Found {len(items)} download job(s):", ""]
    for item in items:
        status_icon = {
            "pending": "[pending]",
            "processing": "[processing]",
            "completed": "[completed]",
            "failed": "[failed]",
        }.get(item.get("status"), "[unknown]")
        lines.append(f"  {status_icon} {item.get('id', 'unknown')} - {item.get('url', 'unknown')}")

    return "\n".join(lines)


def _format_failed_jobs(data: Any) -> str:
    """Format a list of failed jobs for MCP response."""
    if isinstance(data, dict) and "items" in data:
        items = data["items"]
    elif isinstance(data, list):
        items = data
    else:
        items = []

    if not items:
        return "No failed jobs found."

    lines = [f"Found {len(items)} failed job(s):", ""]
    for item in items:
        lines.append(f"  {item.get('id', 'unknown')} - {item.get('url', 'unknown')}")
        if item.get("final_error"):
            lines.append(f"    Error: {item['final_error']}")

    return "\n".join(lines)


@server.list_resources()
async def list_resources() -> list[Resource]:
    """List available MCP resources."""
    return [
        Resource(
            uri="vooglaadija://user/profile",
            name="User Profile",
            description="Current user profile information",
            mimeType="application/json",
        ),
        Resource(
            uri="vooglaadija://downloads/summary",
            name="Downloads Summary",
            description="Summary statistics of download jobs",
            mimeType="application/json",
        ),
    ]


@server.read_resource()
async def read_resource(uri: str) -> str:
    """Read an MCP resource by URI."""
    import httpx

    api_key = settings.mcp_api_key
    base_url = settings.mcp_base_url

    headers = {}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"

    error_payload = {
        "error_code": "INTERNAL_ERROR",
        "retryable": False,
        "suggestion": None,
    }

    try:
        async with httpx.AsyncClient(base_url=base_url, headers=headers, timeout=30.0) as client:
            if uri == "vooglaadija://user/profile":
                response = await client.get("/api/v1/auth/me")
                data = _handle_response(response, error_payload)
                return str(data)

            if uri == "vooglaadija://downloads/summary":
                response = await client.get("/api/v1/downloads", params={"limit": 100})
                data = _handle_response(response, error_payload)
                return str(data)

            error_payload["error_code"] = "RESOURCE_NOT_FOUND"
            error_payload["suggestion"] = f"Unknown resource: {uri}"
            return _format_error(error_payload)

    except httpx.RequestError as exc:
        error_payload["error_code"] = "CONNECTION_ERROR"
        error_payload["retryable"] = True
        error_payload["suggestion"] = str(exc)
        return _format_error(error_payload)


async def run_stdio() -> None:
    """Run the MCP server over stdio transport."""
    async with stdio_server() as (read_stream, write_stream):
        await server.run(
            read_stream,
            write_stream,
            server.create_initialization_options(),
        )

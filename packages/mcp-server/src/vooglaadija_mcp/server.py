"""Model Context Protocol (MCP) server for Vooglaadija.

Implements the MCP JSON-RPC 2.0 surface (initialize / ping / tools / resources /
prompts) over a pluggable transport (stdio or SSE). The protocol handler,
:class:`VooglaadijaMCPServer.handle_message`, is pure and synchronous so it can
be unit-tested without a network or a real client.
"""

from __future__ import annotations

import json
import re
from typing import Any

from .client import VooglaadijaClient
from .errors import VooglaadijaApiError, error_text
from .tools import PROMPTS, RESOURCE_TEMPLATES, RESOURCES, TOOLS

PROTOCOL_VERSION = "2024-11-05"
SERVER_NAME = "vooglaadija"
SERVER_VERSION = "1.0.0"

# JSON-RPC error codes (MCP/JSON-RPC spec).
CODE_PARSE_ERROR = -32700
CODE_INVALID_REQUEST = -32600
CODE_METHOD_NOT_FOUND = -32601
CODE_INVALID_PARAMS = -32602
CODE_INTERNAL_ERROR = -32603


class McpError(Exception):
    """JSON-RPC level error with a numeric code."""

    def __init__(self, code: int, message: str, data: Any | None = None) -> None:
        self.code = code
        self.message = message
        self.data = data
        super().__init__(message)


class VooglaadijaMCPServer:
    """MCP protocol handler backed by a :class:`VooglaadijaClient`."""

    def __init__(self, client: VooglaadijaClient) -> None:
        self.client = client

    # ── JSON-RPC entry point ───────────────────────────────────────────────

    def handle_message(self, message: Any) -> dict[str, Any] | None:
        """Process a single JSON-RPC message and return the response (or None)."""
        if not isinstance(message, dict):
            return self._error(None, CODE_INVALID_REQUEST, "Message must be a JSON object.")

        msg_id = message.get("id")
        method = message.get("method")

        # Notifications (no method or no id) never produce a response.
        if method is None:
            return None
        if msg_id is None:
            try:
                self._dispatch(method, self._coerce_params(message))
            except Exception:
                pass
            return None

        params = self._coerce_params(message)
        try:
            result = self._dispatch(method, params)
        except McpError as exc:
            return self._error(msg_id, exc.code, exc.message, exc.data)
        except Exception as exc:  # defensive: never leak a stack trace to the agent
            return self._error(msg_id, CODE_INTERNAL_ERROR, f"Internal error: {exc}")

        return {"jsonrpc": "2.0", "id": msg_id, "result": result}

    @staticmethod
    def _coerce_params(message: dict[str, Any]) -> dict[str, Any]:
        params = message.get("params", {})
        return params if isinstance(params, dict) else {}

    def _dispatch(self, method: str, params: dict[str, Any]) -> Any:
        if method == "initialize":
            return self._initialize(params)
        if method == "ping":
            return {}
        if method == "tools/list":
            return {"tools": TOOLS}
        if method == "tools/call":
            return self._tools_call(params)
        if method == "resources/list":
            return {"resources": RESOURCES, "resourceTemplates": RESOURCE_TEMPLATES}
        if method == "resources/read":
            return self._resources_read(params)
        if method == "prompts/list":
            return {"prompts": PROMPTS}
        if method == "prompts/get":
            return self._prompts_get(params)
        raise McpError(CODE_METHOD_NOT_FOUND, f"Method not found: {method}")

    @staticmethod
    def _error(msg_id: Any, code: int, message: str, data: Any | None = None) -> dict[str, Any]:
        error: dict[str, Any] = {"code": code, "message": message}
        if data is not None:
            error["data"] = data
        return {"jsonrpc": "2.0", "id": msg_id, "error": error}

    # ── Handlers ──────────────────────────────────────────────────────────

    @staticmethod
    def _initialize(params: dict[str, Any]) -> dict[str, Any]:
        requested = params.get("protocolVersion")
        # Echo a mutually supported protocol version.
        protocol_version = requested if requested == PROTOCOL_VERSION else PROTOCOL_VERSION
        return {
            "protocolVersion": protocol_version,
            "capabilities": {
                "tools": {"listChanged": False},
                "resources": {"listChanged": False},
                "prompts": {"listChanged": False},
            },
            "serverInfo": {"name": SERVER_NAME, "version": SERVER_VERSION},
            "instructions": (
                "Use the Vooglaadija tools to create, inspect, and retry media "
                "extraction jobs. Always pass a job id returned by create_download "
                "or list_downloads."
            ),
        }

    def _tools_call(self, params: dict[str, Any]) -> dict[str, Any]:
        name = params.get("name")
        arguments = params.get("arguments") or {}
        if not isinstance(name, str):
            raise McpError(CODE_INVALID_PARAMS, "Missing tool 'name'.")

        try:
            content, is_error = self._invoke_tool(name, arguments)
        except VooglaadijaApiError as exc:
            return {
                "content": [{"type": "text", "text": error_text(exc.envelope)}],
                "isError": True,
            }
        except McpError:
            raise
        except Exception as exc:  # pragma: no cover - defensive
            return {
                "content": [{"type": "text", "text": json.dumps({"error": str(exc)})}],
                "isError": True,
            }
        return {"content": content, "isError": is_error}

    def _invoke_tool(self, name: str, arguments: dict[str, Any]) -> tuple[list[dict], bool]:
        if name == "create_download":
            result = self.client.create_download(_require_str(arguments, "url"))
        elif name == "get_download":
            result = self.client.get_download(_require_str(arguments, "job_id"))
        elif name == "list_downloads":
            page = _opt_int(arguments, "page", 1)
            per_page = _opt_int(arguments, "per_page", 20)
            result = self.client.list_downloads(page=page, per_page=per_page)
        elif name == "retry_download":
            result = self.client.retry_download(_require_str(arguments, "job_id"))
        elif name == "health":
            result = self.client.health()
        else:
            raise McpError(CODE_METHOD_NOT_FOUND, f"Unknown tool: {name}")

        text = result if isinstance(result, str) else json.dumps(result, separators=(",", ":"))
        return [{"type": "text", "text": text}], False

    def _resources_read(self, params: dict[str, Any]) -> dict[str, Any]:
        uri = params.get("uri")
        if not isinstance(uri, str):
            raise McpError(CODE_INVALID_PARAMS, "Missing resource 'uri'.")

        match = re.match(r"^vooglaadija://downloads/(?P<job_id>[^/]+)$", uri)
        if not match:
            raise McpError(CODE_INVALID_PARAMS, f"Unsupported resource URI: {uri}")

        job = self.client.get_download(match.group("job_id"))
        text = job if isinstance(job, str) else json.dumps(job, separators=(",", ":"))
        return {"contents": [{"uri": uri, "mimeType": "application/json", "text": text}]}

    def _prompts_get(self, params: dict[str, Any]) -> dict[str, Any]:
        name = params.get("name")
        arguments = params.get("arguments") or {}
        if name != "summarize_download":
            raise McpError(CODE_METHOD_NOT_FOUND, f"Unknown prompt: {name}")
        job_id = _require_str(arguments, "job_id")
        text = (
            f"Summarize download job {job_id} for the user. "
            "State its current status, any error and category if failed, and the "
            "next recommended action (e.g. retry_download if failed)."
        )
        return {
            "messages": [
                {"role": "user", "content": {"type": "text", "text": text}}
            ]
        }


def _require_str(arguments: dict[str, Any], key: str) -> str:
    value = arguments.get(key)
    if not isinstance(value, str) or not value:
        raise McpError(CODE_INVALID_PARAMS, f"Missing or empty string argument: '{key}'.")
    return value


def _opt_int(arguments: dict[str, Any], key: str, default: int) -> int:
    value = arguments.get(key, default)
    if not isinstance(value, int):
        raise McpError(CODE_INVALID_PARAMS, f"Argument '{key}' must be an integer.")
    return value

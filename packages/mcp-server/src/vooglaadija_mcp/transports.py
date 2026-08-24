"""Transports for the Vooglaadija MCP server: stdio and SSE."""

from __future__ import annotations

import json
import queue
import sys
import threading
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

from .server import VooglaadijaMCPServer

_JSON_CONTENT = ("Content-Type", "application/json")


def run_stdio(server: VooglaadijaMCPServer) -> None:
    """Run the server over stdin/stdout using newline-delimited JSON-RPC."""
    for raw_line in sys.stdin:
        line = raw_line.strip()
        if not line:
            continue
        try:
            message = json.loads(line)
        except json.JSONDecodeError:
            response = {
                "jsonrpc": "2.0",
                "id": None,
                "error": {"code": -32700, "message": "Parse error"},
            }
        else:
            response = server.handle_message(message)
        if response is not None:
            sys.stdout.write(json.dumps(response) + "\n")
            sys.stdout.flush()


class _SSEState:
    """Shared registry of SSE sessions and their outbound message queues."""

    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.sessions: dict[str, queue.Queue[dict[str, Any]]] = {}

    def create_session(self) -> str:
        session_id = uuid.uuid4().hex
        with self.lock:
            self.sessions[session_id] = queue.Queue()
        return session_id

    def push(self, session_id: str, message: dict[str, Any]) -> bool:
        with self.lock:
            q = self.sessions.get(session_id)
        if q is None:
            return False
        q.put(message)
        return True

    def drop(self, session_id: str) -> None:
        with self.lock:
            self.sessions.pop(session_id, None)


class _SSEHandler(BaseHTTPRequestHandler):
    server_state: _SSEState
    mcp_server: VooglaadijaMCPServer

    def log_message(self, *args: Any) -> None:  # silence default logging
        return

    def do_GET(self) -> None:  # noqa: N802
        if self.path.split("?")[0] != "/sse":
            self.send_error(404)
            return
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Connection", "keep-alive")
        self.end_headers()

        session_id = self.server_state.create_session()
        endpoint = f"/messages?sessionId={session_id}"
        self._send_event("endpoint", endpoint)

        try:
            while not self.rfile.closed:
                try:
                    message = self.server_state.sessions[session_id].get(timeout=15)
                except (queue.Empty, KeyError):
                    self.wfile.write(b": keep-alive\n\n")
                    self.wfile.flush()
                    continue
                self._send_event("message", json.dumps(message))
        finally:
            self.server_state.drop(session_id)

    def do_POST(self) -> None:  # noqa: N802
        if not self.path.split("?")[0].startswith("/messages"):
            self.send_error(404)
            return
        session_id = _query_value(self.path, "sessionId")
        length = int(self.headers.get("Content-Length", 0) or 0)
        body = self.rfile.read(length) if length else b"{}"
        try:
            message = json.loads(body or b"{}")
        except json.JSONDecodeError:
            message = {}

        response = self.mcp_server.handle_message(message)
        if response is not None and session_id:
            if not self.server_state.push(session_id, response):
                self.send_error(400, "Unknown or expired SSE session")
                return

        self.send_response(202)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(b'{"ok":true}')

    def _send_event(self, event: str, data: str) -> None:
        payload = f"event: {event}\ndata: {data}\n\n"
        self.wfile.write(payload.encode("utf-8"))
        self.wfile.flush()


def _query_value(path: str, key: str) -> str | None:
    from urllib.parse import parse_qs, urlparse

    parsed = urlparse(path)
    values = parse_qs(parsed.query).get(key)
    return values[0] if values else None


def run_sse(server: VooglaadijaMCPServer, host: str = "127.0.0.1", port: int = 8001) -> None:
    """Run the server over Server-Sent Events (HTTP)."""
    state = _SSEState()

    httpd = ThreadingHTTPServer((host, port), _SSEHandler)
    httpd.server_state = state  # type: ignore[attr-defined]
    httpd.mcp_server = server  # type: ignore[attr-defined]

    base = f"http://{host}:{port}"
    print(f"Vooglaadija MCP server (SSE) listening on {base}/sse", file=sys.stderr)
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:  # pragma: no cover - manual shutdown
        pass
    finally:
        httpd.server_close()

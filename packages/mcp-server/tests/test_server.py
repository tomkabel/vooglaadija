"""Unit tests for the Vooglaadija MCP server and deterministic error mapping."""

from vooglaadija_mcp.client import VooglaadijaApiError
from vooglaadija_mcp.errors import (
    ApiErrorEnvelope,
    error_text,
    map_http_error,
    map_network_error,
)
from vooglaadija_mcp.server import (
    CODE_METHOD_NOT_FOUND,
    VooglaadijaMCPServer,
)


class FakeClient:
    def create_download(self, url):
        return {"id": "job1", "url": url, "status": "pending"}

    def get_download(self, job_id):
        return {"id": job_id, "status": "completed"}

    def list_downloads(self, page=1, per_page=20):
        return {"downloads": [], "pagination": {"page": page, "per_page": per_page, "total": 0}}

    def retry_download(self, job_id):
        return {"id": job_id, "status": "pending"}

    def health(self):
        return {"status": "ok"}


class ErrorClient:
    def create_download(self, url):
        raise VooglaadijaApiError(
            ApiErrorEnvelope(
                error_code="VALIDATION_ERROR",
                retryable=False,
                suggestion="Fix the URL",
                http_status=422,
            )
        )

    def get_download(self, job_id):
        raise VooglaadijaApiError(
            ApiErrorEnvelope(
                error_code="RESOURCE_NOT_FOUND",
                retryable=False,
                suggestion="Verify the id",
                http_status=404,
            )
        )


def _server(client=None):
    return VooglaadijaMCPServer(client or FakeClient())


def _call(server, method, params=None, msg_id=1):
    return server.handle_message(
        {"jsonrpc": "2.0", "id": msg_id, "method": method, "params": params or {}}
    )


def test_initialize_reports_capabilities():
    resp = _call(_server(), "initialize", {"protocolVersion": "2024-11-05"})
    assert resp["result"]["serverInfo"]["name"] == "vooglaadija"
    assert resp["result"]["capabilities"]["tools"]
    assert resp["result"]["capabilities"]["resources"]
    assert resp["result"]["capabilities"]["prompts"]


def test_tools_list_returns_five_tools_with_schemas():
    tools = _call(_server(), "tools/list")["result"]["tools"]
    names = {t["name"] for t in tools}
    assert {
        "create_download",
        "get_download",
        "list_downloads",
        "retry_download",
        "health",
    } <= names
    for tool in tools:
        assert "inputSchema" in tool


def test_resources_and_prompts_listed():
    assert _call(_server(), "resources/list")["result"]["resourceTemplates"]
    prompts = _call(_server(), "prompts/list")["result"]["prompts"]
    assert any(p["name"] == "summarize_download" for p in prompts)


def test_tool_call_returns_content():
    resp = _call(_server(), "tools/call", {"name": "create_download", "arguments": {"url": "u"}})
    assert resp["result"]["isError"] is False
    assert "job1" in resp["result"]["content"][0]["text"]


def test_tool_call_api_error_is_deterministic():
    resp = _call(_server(ErrorClient()), "tools/call", {"name": "create_download", "arguments": {"url": "u"}})
    assert resp["result"]["isError"] is True
    envelope = resp["result"]["content"][0]["text"]
    assert "VALIDATION_ERROR" in envelope
    assert "retryable" in envelope


def test_tool_call_invalid_params_is_jsonrpc_error():
    resp = _call(_server(), "tools/call", {"name": "create_download", "arguments": {}})
    assert resp["error"]["code"] == -32602


def test_resources_read_resolves_template():
    resp = _call(_server(), "resources/read", {"uri": "vooglaadija://downloads/job1"})
    assert resp["result"]["contents"][0]["text"] == '{"id":"job1","status":"completed"}'


def test_prompts_get_builds_message():
    resp = _call(_server(), "prompts/get", {"name": "summarize_download", "arguments": {"job_id": "job1"}})
    assert "job1" in resp["result"]["messages"][0]["content"]["text"]


def test_notification_produces_no_response():
    assert _server().handle_message({"jsonrpc": "2.0", "method": "notifications/initialized"}) is None


def test_unknown_method_is_method_not_found():
    assert _call(_server(), "bogus")["error"]["code"] == CODE_METHOD_NOT_FOUND


def test_error_mapping_is_deterministic():
    assert map_http_error(404).error_code == "RESOURCE_NOT_FOUND"
    assert map_http_error(404).retryable is False
    assert map_http_error(429).retryable is True
    assert map_http_error(401).error_code == "AUTHENTICATION_FAILED"
    assert "VOOGLAADIJA_API_KEY" in map_http_error(401).suggestion
    assert map_http_error(403).error_code == "INSUFFICIENT_SCOPE"
    # Unknown status code falls back to UNKNOWN_ERROR.
    assert map_http_error(418).error_code == "UNKNOWN_ERROR"


def test_network_error_is_retryable():
    env = map_network_error("Connection refused")
    assert env.error_code == "NETWORK_ERROR"
    assert env.retryable is True


def test_error_text_round_trips():
    env = ApiErrorEnvelope(error_code="X", retryable=True, suggestion="retry")
    assert error_text(env) == '{"error_code":"X","retryable":true,"suggestion":"retry"}'

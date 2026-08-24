# Vooglaadija MCP Server

The official [Model Context Protocol (MCP)](https://modelcontextprotocol.io)
server for **Vooglaadija**. It exposes Vooglaadija's core capabilities — creating
and inspecting media-extraction jobs — as standardized MCP **tools**,
**resources**, and **prompts**, so LLM agents and IDEs (Claude Desktop, Cursor,
etc.) can drive Vooglaadija natively.

This package is **dependency-free** (Python standard library only) and supports
both the **stdio** and **SSE** transports.

> Part of RFC [#154 — Native Agentic Support: First-Class MCP Integration &
> Long-Lived Machine Authentication](https://github.com/tomkabel/vooglaadija/issues/154).

## Why

- **Long-lived auth.** Agents authenticate with a scoped Personal Access Token
  (PAT) instead of short-lived JWTs, so background workers and MCP bridges stop
  dropping connections mid-task.
- **Zero-shot tool calls.** Every tool ships a strict JSON Schema.
- **Self-healing errors.** Failures are returned as deterministic envelopes:
  `{ "error_code": "...", "retryable": false, "suggestion": "..." }`, so an agent
  can decide whether to retry.

## Install

```bash
cd packages/mcp-server
pip install -e .
```

This installs the `vooglaadija-mcp` console command.

## Configure (Claude Desktop / Cursor)

Create a key in Vooglaadija first (any authenticated user can mint one):

```bash
curl -X POST http://localhost:8000/api/v1/keys \
  -H "Authorization: Bearer $ACCESS_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"name":"claude-desktop","scopes":["downloads:read","downloads:write"]}'
```

Then add to your `claude_desktop_config.json` (or Cursor's MCP settings):

```json
{
  "mcpServers": {
    "vooglaadija": {
      "command": "vooglaadija-mcp",
      "args": ["--transport", "stdio"],
      "env": {
        "VOOGLAADIJA_API_KEY": "vlj_pat_xxxxxxxxxxxxxxxxxxxx",
        "VOOGLAADIJA_API_BASE_URL": "http://localhost:8000"
      }
    }
  }
}
```

For a remote / HTTP setup, run the server with `--transport sse` and point your
client at `http://host:8001/sse`.

## Usage

```bash
# stdio (default)
vooglaadija-mcp --api-key "$VOOGLAADIJA_API_KEY"

# SSE over HTTP
vooglaadija-mcp --transport sse --host 0.0.0.0 --port 8001
```

Environment variables (overridden by CLI flags):

| Variable                   | Default                 | Description                          |
| -------------------------- | ----------------------- | ------------------------------------ |
| `VOOGLAADIJA_API_KEY`      | _(none)_                | PAT used as the bearer credential.   |
| `VOOGLAADIJA_API_BASE_URL` | `http://localhost:8000` | Base URL of the Vooglaadija API.     |

## Capabilities

**Tools:** `create_download`, `get_download`, `list_downloads`, `retry_download`,
`health`.

**Resources:** `vooglaadija://downloads/{jobId}` (JSON job representation).

**Prompts:** `summarize_download` (summarize a job's status for an end user).

## Development

```bash
python -m vooglaadija_mcp --api-key "$VOOGLAADIJA_API_KEY"
pytest packages/mcp-server/tests
```

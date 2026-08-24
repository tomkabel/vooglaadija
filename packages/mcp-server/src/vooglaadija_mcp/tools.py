"""MCP tool, resource, and prompt definitions for Vooglaadija.

These are pure metadata (no I/O) so they can be unit-tested and reused by both
the stdio and SSE transports. Each tool carries a strict JSON Schema so an LLM
can call it zero-shot without schema guessing.
"""

from __future__ import annotations

from typing import Any

TOOLS: list[dict[str, Any]] = [
    {
        "name": "create_download",
        "description": (
            "Queue a new media-extraction job for a supported URL (e.g. YouTube). "
            "Returns the created job with its id and initial status."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "url": {
                    "type": "string",
                    "description": "A supported media URL to extract (e.g. https://www.youtube.com/watch?v=...).",
                }
            },
            "required": ["url"],
        },
    },
    {
        "name": "get_download",
        "description": "Get the current status and metadata of a download job by its id.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "job_id": {"type": "string", "description": "The download job UUID."}
            },
            "required": ["job_id"],
        },
    },
    {
        "name": "list_downloads",
        "description": "List the caller's download jobs with pagination.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "page": {"type": "integer", "minimum": 1, "default": 1},
                "per_page": {"type": "integer", "minimum": 1, "maximum": 100, "default": 20},
            },
        },
    },
    {
        "name": "retry_download",
        "description": "Retry a failed download job, requeueing it for processing.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "job_id": {"type": "string", "description": "The failed download job UUID."}
            },
            "required": ["job_id"],
        },
    },
    {
        "name": "health",
        "description": "Check service health and readiness. Does not require authentication.",
        "inputSchema": {"type": "object", "properties": {}},
    },
]

RESOURCES: list[dict[str, Any]] = []

RESOURCE_TEMPLATES: list[dict[str, Any]] = [
    {
        "uriTemplate": "vooglaadija://downloads/{jobId}",
        "name": "Download job",
        "mimeType": "application/json",
        "description": "The JSON representation of a single download job.",
    }
]

PROMPTS: list[dict[str, Any]] = [
    {
        "name": "summarize_download",
        "description": "Summarize a download job's status for an end user.",
        "arguments": [
            {
                "name": "job_id",
                "description": "The download job UUID to summarize.",
                "required": True,
            }
        ],
    }
]

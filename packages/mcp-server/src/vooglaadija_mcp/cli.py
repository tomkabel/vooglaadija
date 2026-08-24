"""Command-line entrypoint for the Vooglaadija MCP server."""

from __future__ import annotations

import argparse
import os
import sys

from .client import VooglaadijaClient
from .server import VooglaadijaMCPServer
from .transports import run_sse, run_stdio

DEFAULT_BASE_URL = "http://localhost:8000"


def build_client(api_base_url: str, api_key: str) -> VooglaadijaClient:
    return VooglaadijaClient(api_base_url=api_base_url, api_key=api_key)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="vooglaadija-mcp",
        description="Official Model Context Protocol (MCP) server for Vooglaadija.",
    )
    parser.add_argument(
        "--transport",
        choices=["stdio", "sse"],
        default="stdio",
        help="Transport to use. 'stdio' (default) for Claude Desktop / IDEs; 'sse' for HTTP.",
    )
    parser.add_argument(
        "--api-base-url",
        default=os.environ.get("VOOGLAADIJA_API_BASE_URL", DEFAULT_BASE_URL),
        help="Base URL of the Vooglaadija API (env: VOOGLAADIJA_API_BASE_URL).",
    )
    parser.add_argument(
        "--api-key",
        default=os.environ.get("VOOGLAADIJA_API_KEY", ""),
        help="Personal access token used as the bearer credential (env: VOOGLAADIJA_API_KEY).",
    )
    parser.add_argument("--host", default="127.0.0.1", help="SSE bind host (SSE transport only).")
    parser.add_argument("--port", type=int, default=8001, help="SSE bind port (SSE transport only).")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)

    if not args.api_key:
        print(
            "Warning: VOOGLAADIJA_API_KEY is not set. Authenticated tools will fail "
            "until a personal access token is provided.",
            file=sys.stderr,
        )

    client = build_client(args.api_base_url, args.api_key)
    server = VooglaadijaMCPServer(client)

    if args.transport == "sse":
        run_sse(server, host=args.host, port=args.port)
    else:
        run_stdio(server)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

"""MCP server entry point for stdio transport.

Run with: python -m app.mcp
"""

import asyncio
import sys

from app.mcp.server import run_stdio


def main() -> None:
    """Run the MCP server over stdio."""
    try:
        asyncio.run(run_stdio())
    except KeyboardInterrupt:
        sys.exit(0)


if __name__ == "__main__":
    main()

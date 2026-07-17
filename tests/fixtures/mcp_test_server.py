from __future__ import annotations

import os

from mcp.server.fastmcp import FastMCP

mcp = FastMCP("test-server")


@mcp.tool()
def echo(value: str) -> str:
    """Echo a value with the configured server label."""
    return f"{os.environ.get('TEST_SERVER_LABEL', 'unknown')}:{value}"


@mcp.tool()
def current_directory() -> str:
    """Return the server process working directory."""
    return os.getcwd()


if __name__ == "__main__":
    mcp.run(transport="stdio")

"""Official SDK client for the installed local AUTONOMOUS-EDA server."""

import sys
from pathlib import Path

from mcp import Client
from mcp.client.stdio import StdioServerParameters

from .api import Model
from .tools import TOOLS, Failure


def local_client(config: Path, *, timeout: float = 25.0) -> Client:
    # The operator starts this installed interpreter. Neither config nor an
    # MCP response can change its executable, module, or command-line shape.
    return Client(
        StdioServerParameters(
            command=str(Path(sys.executable).absolute()),
            args=["-I", "-m", "autonomous_eda", "mcp", "serve", "--config", str(config.resolve())],
        ),
        read_timeout_seconds=timeout,
        cache=None,
    )


async def invoke(client: Client, tool: str, args: dict[str, object]) -> Model:
    if tool not in TOOLS:
        raise ValueError("Unsupported local MCP tool")
    result = await client.call_tool(tool, args)
    if result.is_error:
        return Failure.model_validate(result.structured_content)
    return TOOLS[tool].result.model_validate(result.structured_content)

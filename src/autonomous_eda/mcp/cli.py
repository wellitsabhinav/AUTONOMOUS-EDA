"""MCP CLI arguments and optional dependency loading."""

import argparse
import asyncio
import sys
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path

from . import SDK_VERSION


def configure(parser: argparse.ArgumentParser) -> None:
    actions = parser.add_subparsers(dest="mcp_action", required=True)
    for name, help_text in (
        ("serve", "serve approved evidence over stdio"),
        ("inspect", "discover server capabilities and tool schemas"),
        ("smoke", "retrieve an experiment summary and timing over stdio"),
    ):
        action = actions.add_parser(name, help=help_text)
        action.add_argument(
            "--config", type=Path, required=True, help="operator-owned TOML allowlist"
        )
        if name == "smoke":
            action.add_argument("experiment_id")


async def execute(args: argparse.Namespace) -> int:
    from mcp.shared.exceptions import MCPError
    from pydantic import ValidationError

    from .client import invoke, local_client
    from .registry import Registry
    from .server import serve
    from .tools import Failure

    try:
        if args.mcp_action == "serve":
            await serve(Registry.load(args.config))
            return 0
        async with local_client(args.config) as client:
            tools = await client.list_tools()
            if args.mcp_action == "inspect":
                import json

                print(
                    json.dumps(
                        {
                            "protocol_version": client.protocol_version,
                            "server": client.server_info.model_dump(mode="json")
                            if client.server_info
                            else None,
                            "capabilities": client.server_capabilities.model_dump(
                                mode="json", exclude_none=True
                            ),
                            "tools": [
                                tool.model_dump(mode="json", exclude_none=True)
                                for tool in tools.tools
                            ],
                        },
                        indent=2,
                    )
                )
                return 0
            arguments: dict[str, object] = {"experiment_id": args.experiment_id}
            for name in ("get_experiment_summary", "get_timing_summary"):
                result = await invoke(client, name, arguments)
                print(result.model_dump_json())
                if isinstance(result, Failure):
                    return 2
    except (MCPError, ValidationError, ExceptionGroup, OSError, ValueError, TimeoutError):
        print("MCP operation failed. Check the local config and server stderr.", file=sys.stderr)
        return 2
    return 0


def main(args: argparse.Namespace) -> int:
    try:
        if version("mcp") != SDK_VERSION:
            raise PackageNotFoundError
        return asyncio.run(execute(args))
    except (PackageNotFoundError, ImportError):
        print(
            'Install the supported MCP extra with: python -m pip install "autonomous-eda[mcp]"',
            file=sys.stderr,
        )
        return 2
    except KeyboardInterrupt:
        return 130

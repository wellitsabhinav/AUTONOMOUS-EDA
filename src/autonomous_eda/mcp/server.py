"""Local stdio MCP server with a cancellable evidence worker."""

import asyncio
import json
import logging
import os
import subprocess
import sys
from contextlib import suppress
from pathlib import Path
from tempfile import TemporaryDirectory, gettempdir
from typing import TextIO, cast

import anyio
from mcp import types
from mcp.server.context import CallNext, HandlerResult, ServerRequestContext
from mcp.server.lowlevel import Server
from mcp.server.stdio import stdio_server
from mcp.shared.exceptions import MCPError
from pydantic import ValidationError

from .. import __version__
from ..evidence import VERSION
from . import SDK_VERSION, api
from .api import Model
from .registry import Registry
from .snapshot import Source
from .tools import MAX_RESULT_BYTES, TOOLS, Failure, ToolSpec, bounded, failure
from .worker import Request

MAX_INPUT_CHARS = 64 * 1024
DEFAULT_TIMEOUT = 15.0


class FrameTooLarge(ValueError):
    """The stdio peer exceeded the input limit."""


class BoundedInput:
    def readline(self) -> str:
        line = sys.stdin.buffer.readline(MAX_INPUT_CHARS + 1)
        if len(line) > MAX_INPUT_CHARS:
            raise FrameTooLarge
        return line.decode("utf-8", errors="replace")


def audit(tool: str, outcome: str) -> None:
    print(json.dumps({"event": "mcp_read", "tool": tool, "outcome": outcome}), file=sys.stderr)


async def read_in_worker(request: Request, spec: ToolSpec, target: Path) -> Model:
    # Finish acquiring the process handle before cancellation can interrupt us.
    with anyio.CancelScope(shield=True):
        process = await asyncio.create_subprocess_exec(
            sys.executable,
            "-I",
            "-m",
            "autonomous_eda.mcp.worker",
            str(target),
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            env={name: os.environ[name] for name in ("SYSTEMROOT", "WINDIR") if name in os.environ},
        )
    try:
        stdout, _ = await process.communicate(request.model_dump_json().encode())
    finally:
        with anyio.CancelScope(shield=True):
            if process.returncode is None:
                with suppress(ProcessLookupError):
                    process.kill()
                await process.wait()
    if process.returncode != 0 or len(stdout) > MAX_RESULT_BYTES:
        return failure("worker_error")
    data = json.loads(stdout)
    if isinstance(data, dict) and "error" in data:
        return Failure.model_validate(data)
    return spec.result.model_validate(data)


class Service:
    def __init__(self, registry: Registry, timeout: float = DEFAULT_TIMEOUT) -> None:
        self.registry = registry
        self.timeout = timeout
        self.worker = anyio.CapacityLimiter(1)
        self.temp = Path(gettempdir()).resolve()
        if any(self.temp.is_relative_to(root.root) for root in registry.roots.values()):
            raise ValueError("Temporary files must be outside the configured roots")

    async def list_tools(
        self, _ctx: ServerRequestContext[object], params: types.PaginatedRequestParams | None
    ) -> types.ListToolsResult:
        if params is not None and params.cursor is not None:
            audit("tools/list", "invalid_arguments")
            raise MCPError(types.INVALID_PARAMS, "Unknown tools cursor")
        audit("tools/list", "ok")
        return types.ListToolsResult(
            tools=[
                types.Tool(
                    name=name,
                    description=spec.description,
                    input_schema=spec.arguments.model_json_schema(),
                    output_schema=spec.result.model_json_schema(),
                    annotations=types.ToolAnnotations(read_only_hint=True, open_world_hint=False),
                )
                for name, spec in TOOLS.items()
            ]
        )

    async def call_tool(
        self, ctx: ServerRequestContext[object], params: types.CallToolRequestParams
    ) -> types.CallToolResult:
        if params.name not in TOOLS:
            audit("unknown", "unknown_tool")
            raise MCPError(types.INVALID_PARAMS, "Unknown tool", {"code": "unknown_tool"})
        name = params.name
        spec = TOOLS[name]
        try:
            args = spec.arguments.model_validate(params.arguments or {})
            result = await self.dispatch(name, spec, args, ctx.protocol_version)
        except ValidationError:
            result = failure("invalid_arguments")
        except anyio.get_cancelled_exc_class():
            audit(name, "cancelled")
            raise
        except Exception:
            result = failure("worker_error")
        result = bounded(result)
        audit(name, result.error.code if isinstance(result, Failure) else "ok")
        return types.CallToolResult(
            content=[types.TextContent(type="text", text=result.model_dump_json())],
            structured_content=result.model_dump(mode="json"),
            is_error=isinstance(result, Failure),
        )

    async def audit_request(
        self, ctx: ServerRequestContext[object], call_next: CallNext
    ) -> HandlerResult:
        if ctx.method not in {"tools/call", "tools/list"}:
            audit(
                ctx.method if ctx.method in {"initialize", "server/discover", "ping"} else "other",
                "request",
            )
        try:
            return await call_next(ctx)
        except ValidationError:
            audit("stdio", "invalid_arguments")
            raise MCPError(types.INVALID_PARAMS, "Invalid request parameters") from None

    async def dispatch(
        self, name: str, spec: ToolSpec, args: Model, protocol_version: str
    ) -> Model:
        if isinstance(args, api.NoArgs):
            return api.ServerInfo(
                application_version=__version__,
                sdk_version=SDK_VERSION,
                protocol_version=protocol_version,
                evidence_schema_version=VERSION,
                capabilities=list(TOOLS),
                configured_roots=sorted(self.registry.roots),
            )
        if not isinstance(args, api.ExperimentArgs):
            assert isinstance(args, api.PageArgs)
            return api.list_experiments(self.registry, args)
        source = self.registry.experiments.get(args.experiment_id)
        if source is None:
            return failure("access_denied")
        return await self.evidence(name, spec, args, source)

    async def evidence(self, name: str, spec: ToolSpec, args: Model, source: Source) -> Model:
        try:
            self.worker.acquire_nowait()
        except anyio.WouldBlock:
            return failure("busy")
        try:
            with TemporaryDirectory(prefix="aeda-mcp-", dir=self.temp) as directory:
                request = Request(
                    tool=name,
                    args=args.model_dump(),
                    source_path=str(source.root),
                    source_device=source.identity[0],
                    source_inode=source.identity[1],
                )
                with anyio.fail_after(self.timeout):
                    return await read_in_worker(request, spec, Path(directory))
        except TimeoutError:
            return failure("timeout")
        finally:
            self.worker.release()


async def serve(registry: Registry) -> None:
    # SDK diagnostics can contain peer-controlled data. The application emits
    # its own fixed-field audit events.
    logging.getLogger("mcp").setLevel(logging.CRITICAL + 1)
    service = Service(registry)
    server: Server[object] = Server(
        "autonomous-eda",
        version=__version__,
        on_list_tools=service.list_tools,
        on_call_tool=service.call_tool,
    )
    server.middleware.append(service.audit_request)
    # The pinned SDK consumes only readline() from an explicitly supplied input.
    stdin = anyio.wrap_file(cast(TextIO, BoundedInput()))
    try:
        async with stdio_server(stdin=stdin) as (reader, writer):
            await server.run(reader, writer, server.create_initialization_options())
    except* FrameTooLarge:
        audit("stdio", "input_too_large")

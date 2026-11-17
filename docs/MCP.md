# Read-only MCP

`get_acceptance_summary` returns the deterministic full-profile verdict and gate states from the allowlisted schema 1 snapshot.

`get_aes_qualification` reads an allowlisted run's bound AES qualification
receipt. It checks the receipt digest and the run's pinned RTL and SDC identity
before returning the recorded gate states, blocked obligations, and evidence
references. Milestone 12B also returns the five Verilator scenario states and
independent CDC, sideload, error and bounded proof gates. Archived runs without
an attached receipt return `UNKNOWN`.
The worker reads a bounded snapshot and launches no EDA tools.

AUTONOMOUS-EDA serves recorded experiment evidence to a local MCP client over stdio. The official Python SDK handles discovery, tool calls, and cancellation. An operator-owned registry maps experiment IDs to directories. A short-lived worker captures the approved evidence and calls the same schema 1 read API as the deterministic CLI. It returns typed summaries of the recorded design, attempts, metrics, verification gates, and artifact integrity. The [evidence contract](EVIDENCE_CONTRACT.md) defines the underlying measurements.

## Local setup

```sh
python -m pip install -e '.[mcp]'
```

Create a local `mcp.toml`. Use an absolute, canonical directory and grant access to each experiment explicitly. On Windows, use forward slashes in the TOML paths, for example `F:/eda/recorded-runs`.

```toml
[roots]
archive = "/home/eda/recorded-runs"

[experiments.gcd]
root = "archive"
directory = "gcd"

[experiments.ibex]
root = "archive"
directory = "ibex"
```

Each directory must contain a controller `run.sqlite3` and its recorded inputs and evidence, or a `reference.json` index and its referenced files. To serve a historical reference, copy the matching [GCD or Ibex index](evidence/) to `reference.json` inside a disposable copy of the retained archive. Preserve the paths and bytes recorded by the index. The repository tracks hashes and reference metadata. Supply the physical artifacts from the retained local run.

## CLI and client

| Command | Behavior |
| --- | --- |
| `autonomous-eda mcp serve --config mcp.toml` | Start the stdio server for a client to launch. |
| `autonomous-eda mcp inspect --config mcp.toml` | Launch a local server and print negotiated capabilities and tool schemas as JSON. |
| `autonomous-eda mcp smoke --config mcp.toml gcd` | Launch a local server and print two JSON lines with the GCD run summary and timing. |

The bundled client starts the installed Python interpreter with a fixed module and argument list. Its SDK session applies read deadlines and closes the child when the context exits. Programmatic clients can use the same entry point.

```python
import asyncio
from pathlib import Path

from autonomous_eda.mcp.client import invoke, local_client


async def main():
    async with local_client(Path("mcp.toml")) as client:
        print(client.protocol_version)
        print([tool.name for tool in (await client.list_tools()).tools])
        timing = await invoke(client, "get_timing_summary", {"experiment_id": "gcd"})
        print(timing.model_dump_json())


asyncio.run(main())
```

For a host with Codex-style MCP configuration, use the absolute executable path of the Python environment where the extra is installed. The official Python SDK client was exercised over subprocess stdio. Host-specific integration has yet to be exercised.

```toml
[mcp_servers.autonomous_eda]
command = "/home/eda/venv/bin/python"
args = ["-I", "-m", "autonomous_eda", "mcp", "serve", "--config", "/home/eda/mcp.toml"]
tool_timeout_sec = 25
```

## Tool reference

Every experiment tool takes `experiment_id`. Pagination takes optional `offset` and `limit`, with a default of 20 entries and a maximum of 50. Continue with `next_offset` until it is null. Discover the exact input and output schemas with `tools/list`.

| Tool | Result |
| --- | --- |
| `get_server_info` | App, SDK, protocol, and evidence versions, tool capabilities, read-only status, and configured root aliases. |
| `list_experiments` | A page of approved IDs. Retrieve individual summaries for design and platform. |
| `get_stage_resources` | Bounded pages of up to eight controller attempts plus the current layout's recorded verification dispatches. |
| `get_experiment_summary` | Recorded run and design IDs, top, platform, source revision, toolchain, stage states, acceptance, and evidence counts. |
| `get_stage_status` | Stage states and paginated attempts with elapsed seconds, exit codes, and normalized failure categories. Reference archives without attempt records return an empty list. |
| `get_timing_summary` | Clock target, setup and hold worst slack and TNS at `finish`, with units, corner, and evidence references. Optional `corner` accepts `tt_025C_1v80` when recorded. |
| `get_physical_metrics` | Recorded area, instance count, wirelength, routing, power, and DRC count with units and evidence. |
| `get_verification_gates` | Independent schema 1 gates, acceptance, and artifact references. |
| `list_artifacts` | A page of opaque IDs, type, producing stage, recorded SHA-256, observed size, and integrity state. |
| `get_artifact_metadata` | The same fields for an artifact ID within the named experiment. |

Successful calls return structured objects with a JSON text representation for older hosts. Tool failures use `isError` with an `error.code` and `error.message`. For example, an unregistered ID returns `access_denied`. An unsupported tool name returns JSON-RPC `-32602` with `data.code` set to `unknown_tool`. A lost or modified report yields null measurements, with `UNKNOWN` measurement status and `MISSING` or `MODIFIED` artifact availability. Gates retain their independent schema 1 states, including `NOT_CHECKED`.

## Security and operating limits

- Stdio access comes from the trusted local host process and its OS permissions. The operator controls the executable, configuration, evidence roots and temporary storage. Keep the config and evidence directories writable only by trusted accounts. A local writer who can alter both a report and its recorded hash can forge its provenance. The server verifies consistency with the recorded index or database.
- The registry grants specific IDs under named roots and performs zero directory discovery. Directory symlinks, junctions, hard-linked files, traversal, reserved paths, and replaced registered directories fail closed. On Linux, the worker opens each path segment relative to a directory descriptor with no symlink following. On Windows it opens each parent with reparse-point handling and holds it against replacement during the file open. It checks the opened file identity and metadata, then parses a private snapshot and rechecks the source inventory for observed changes. Use quiescent controller runs on local filesystems. SQLite journal and WAL sidecars trigger an integrity error.
- Each request can stage up to 512 files and 512 MiB, with a 128 MiB binary-file limit, a 16 MiB SQLite limit, and a 4 MiB text-file limit. One evidence worker runs at a time. The 15-second evidence deadline or protocol cancellation terminates and reaps it. The server accepts up to 64 KiB per input line and caps the structured result at 24 KiB before adding the text representation. A host that shares one server instance across callers also shares its allowlist and capacity.
- Stderr carries audit events with fixed tool names and outcomes. The tool responses omit raw diagnostics, command lines, host paths, root paths, report text, and RTL contents. For private projects, register only the specific completed runs that the host may see. The host controls any subsequent handling of returned metadata.
- The transport is local stdio and the only advertised MCP capability is tools. The fixed allowlist covers recorded design and evidence inspection. Future execution tools require a separate authorization and execution policy. This milestone includes no network listener or EDA invocation.

See the [SDK compatibility decision](adr/0002-read-only-mcp.md) for supported protocol revisions.

## Validation

Run `python -m pytest -q tests/test_mcp.py` after installing both the `dev` and `mcp` extras. The default suite uses compact reference and SQLite fixtures. To exercise the retained 1.1 ns GCD run, point `AUTONOMOUS_EDA_MCP_GCD_CONFIG` at an allowlist with a `gcd` entry and run the same command.

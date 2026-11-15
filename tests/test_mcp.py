import asyncio
import json
import os
import sqlite3
import subprocess
import sys
from contextlib import closing
from importlib.util import find_spec
from pathlib import Path

import pytest

from autonomous_eda.experiment import initialize
from autonomous_eda.manifest import ORFSManifest, load_manifest, sha256

if find_spec("mcp") is None:
    pytest.skip("The optional MCP extra is not installed", allow_module_level=True)

import anyio  # noqa: E402
from mcp import Client, types  # noqa: E402
from mcp.client.stdio import StdioServerParameters, stdio_client  # noqa: E402
from mcp.shared.exceptions import MCPError  # noqa: E402

from autonomous_eda.mcp import api  # noqa: E402
from autonomous_eda.mcp.client import invoke, local_client  # noqa: E402
from autonomous_eda.mcp.registry import Registry  # noqa: E402
from autonomous_eda.mcp.server import MAX_INPUT_CHARS, Service, read_in_worker  # noqa: E402
from autonomous_eda.mcp.snapshot import Snapshot, UnsafeEvidence, load_evidence  # noqa: E402
from autonomous_eda.mcp.tools import MAX_RESULT_BYTES, TOOLS, Failure, bounded  # noqa: E402
from autonomous_eda.mcp.worker import Request  # noqa: E402


def registry_config(root: Path, **entries: str) -> Path:
    config = root.parent / "mcp.toml"
    body = [f"[roots]\nlocal = {json.dumps(root.as_posix())}"]
    body.extend(
        f'[experiments.{name}]\nroot = "local"\ndirectory = {json.dumps(directory)}'
        for name, directory in entries.items()
    )
    config.write_text("\n".join(body), encoding="utf-8")
    return config


def write_reference(root: Path, design: str) -> Path:
    source = Path(f"docs/evidence/{design}-sky130hd-index.json")
    index = json.loads(source.read_bytes())
    variant = f"sky130hd/{design}/base"
    final = f"logs/{variant}/6_report.json"
    setup, hold = (-1.20488, 0.429847) if design == "gcd" else (-0.377013, 0.426791)
    sdc = f"create_clock -name core -period {index['manifest']['clock_period_ns']} clk"
    files = {
        final: json.dumps(
            {
                "finish__timing__setup__ws": setup,
                "finish__timing__hold__ws": hold,
                "finish__timing__drv__hold_violation_count": 0,
                "finish__design__instance__area__stdcell": 42,
                "finish__design__instance__count": 9,
            }
        ),
        f"results/{variant}/1_synth.sdc": sdc,
        f"results/{variant}/6_final.sdc": sdc,
        f"results/{variant}/6_final.gds": "fixture GDS",
        f"results/{variant}/6_lvs.lvsdb": "fixture LVS",
        f"reports/{variant}/6_drc.lyrdb": "fixture DRC",
        f"reports/{variant}/6_drc_count.rpt": "0",
        f"logs/{variant}/6_lvs.log": "INFO : Congratulations! Netlists match.",
        "gds-validation.log": f"Validated GDS top {index['manifest']['top']}, 5 cells",
    }
    index["sha256"] = {}
    for relative, content in files.items():
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
        index["sha256"][relative] = sha256(path)
    (root / "reference.json").write_text(json.dumps(index), encoding="utf-8")
    return root / final


@pytest.fixture
def recorded(tmp_path: Path) -> tuple[Path, Path, Path]:
    roots = tmp_path / "records"
    gcd = write_reference(roots / "gcd", "gcd")
    ibex = write_reference(roots / "ibex", "ibex")
    write_reference(roots / "private", "gcd")
    return registry_config(roots, gcd="gcd", ibex="ibex"), gcd, ibex


def snapshot_files(root: Path) -> dict[Path, tuple[str, int]]:
    return {
        path.relative_to(root): (sha256(path), path.stat().st_mtime_ns)
        for path in root.rglob("*")
        if path.is_file()
    }


def test_official_client_reference_conversation(recorded: tuple[Path, Path, Path]) -> None:
    config, gcd, ibex = recorded
    before = snapshot_files(config.parent / "records")

    async def conversation() -> None:
        async with local_client(config) as client:
            assert client.protocol_version == "2026-07-28"
            assert client.server_info is not None and client.server_info.name == "autonomous-eda"
            assert client.server_capabilities.tools is not None
            assert client.server_capabilities.resources is None
            tools = (await client.list_tools()).tools
            assert [tool.name for tool in tools] == list(TOOLS)
            assert all(tool.annotations and tool.annotations.read_only_hint for tool in tools)
            info = await invoke(client, "get_server_info", {})
            assert isinstance(info, api.ServerInfo) and info.configured_roots == ["local"]
            listing = await invoke(client, "list_experiments", {"limit": 1})
            assert listing == api.ExperimentList(experiment_ids=["gcd"], next_offset=1)
            listing = await invoke(client, "list_experiments", {"limit": 1, "offset": 1})
            assert listing == api.ExperimentList(experiment_ids=["ibex"], next_offset=None)
            for name, target, setup, hold, report in (
                ("gcd", 1.1, -1.20488, 0.429847, gcd),
                ("ibex", 8.0, -0.377013, 0.426791, ibex),
            ):
                args: dict[str, object] = {"experiment_id": name}
                summary = await invoke(client, "get_experiment_summary", args)
                assert isinstance(summary, api.Summary)
                assert summary.design == name and summary.platform == "sky130hd"
                assert summary.acceptance == "FAILED"
                timing = await invoke(
                    client,
                    "get_timing_summary",
                    {**args, "stage": "finish", "corner": "tt_025C_1v80"},
                )
                assert isinstance(timing, api.TimingSummary) and timing.corner == "tt_025C_1v80"
                assert timing.clock_target.value == target and timing.clock_target.unit == "ns"
                metric = timing.metrics["setup_worst_slack_ns"]
                assert (metric.value, metric.stage, metric.unit) == (setup, "finish", "ns")
                assert metric.evidence is not None and metric.evidence.sha256 == sha256(report)
                assert timing.metrics["hold_worst_slack_ns"].value == hold
                assert timing.metrics["setup_tns_ns"].status == "UNKNOWN"
                gates = await invoke(client, "get_verification_gates", args)
                assert isinstance(gates, api.Gates) and gates.gates["setup"].state == "FAILED"
                assert all(
                    gates.gates[k].state == "PASSED" for k in ("hold", "platform_drc", "lvs")
                )
                assert gates.gates["equivalence"].state == "NOT_CHECKED"
                physical = await invoke(client, "get_physical_metrics", args)
                assert isinstance(physical, api.PhysicalMetrics)
                assert physical.metrics["klayout_drc_report_value_count"].value == 0
                assert physical.metrics["standard_cell_area_um2"].unit == "um2"
                artifacts = await invoke(client, "list_artifacts", {**args, "limit": 1})
                assert isinstance(artifacts, api.ArtifactList) and artifacts.next_offset == 1
                result = await invoke(
                    client,
                    "get_artifact_metadata",
                    {**args, "artifact_id": metric.evidence.artifact_id},
                )
                assert (
                    isinstance(result, api.ArtifactMetadata)
                    and result.size == report.stat().st_size
                )
                assert "6_report.json" not in result.model_dump_json()
            for tool, invalid_args, code in (
                ("get_timing_summary", {"experiment_id": "private"}, "access_denied"),
                ("get_timing_summary", {"experiment_id": "../private"}, "invalid_arguments"),
                (
                    "get_timing_summary",
                    {"experiment_id": "gcd", "path": "secret"},
                    "invalid_arguments",
                ),
                (
                    "get_artifact_metadata",
                    {"experiment_id": "gcd", "artifact_id": "0" * 64},
                    "access_denied",
                ),
                ("list_experiments", {"limit": "1"}, "invalid_arguments"),
                ("list_experiments", {"limit": 50000}, "invalid_arguments"),
            ):
                denial = await client.call_tool(tool, dict(invalid_args))
                assert denial.is_error and denial.structured_content["error"]["code"] == code
                assert "secret" not in str(denial) and "../private" not in str(denial)
            with pytest.raises(MCPError) as unknown:
                await client.call_tool("execute_shell", {"command": "touch forbidden"})
            assert unknown.value.code == types.INVALID_PARAMS
            assert unknown.value.data == {"code": "unknown_tool"}

    asyncio.run(conversation())
    assert snapshot_files(config.parent / "records") == before


def test_retained_gcd_run_over_stdio() -> None:
    config = os.environ.get("AUTONOMOUS_EDA_MCP_GCD_CONFIG")
    if config is None:
        pytest.skip("Set AUTONOMOUS_EDA_MCP_GCD_CONFIG to an allowlist for retained GCD evidence")

    async def conversation() -> None:
        async with local_client(Path(config)) as client:
            await client.list_tools()
            args: dict[str, object] = {"experiment_id": "gcd"}
            summary = await invoke(client, "get_experiment_summary", args)
            assert isinstance(summary, api.Summary)
            assert (summary.design, summary.platform, summary.acceptance) == (
                "gcd",
                "sky130hd",
                "FAILED",
            )
            timing = await invoke(client, "get_timing_summary", args)
            assert isinstance(timing, api.TimingSummary)
            assert timing.clock_target.value == 1.1
            assert timing.metrics["setup_worst_slack_ns"].value == -1.20488

    asyncio.run(conversation())


def test_legacy_stdio_rechecks_changed_and_missing_evidence(
    recorded: tuple[Path, Path, Path],
) -> None:
    config, gcd, _ = recorded

    async def conversation() -> None:
        target = local_client(config).server
        assert isinstance(target, StdioServerParameters)
        async with Client(target, mode="legacy", read_timeout_seconds=20) as client:
            assert client.protocol_version == "2025-11-25"
            args: dict[str, object] = {"experiment_id": "gcd"}
            assert [tool.name for tool in (await client.list_tools()).tools] == list(TOOLS)
            original = await invoke(client, "get_timing_summary", args)
            assert isinstance(original, api.TimingSummary)
            acceptance = await invoke(client, "get_acceptance_summary", args)
            assert isinstance(acceptance, api.AcceptanceSummary)
            assert acceptance.gates["setup"] == "FAILED"
            gcd.write_text("{}", encoding="utf-8")
            changed = await invoke(client, "get_timing_summary", args)
            assert isinstance(changed, api.TimingSummary)
            metric = changed.metrics["setup_worst_slack_ns"]
            assert metric.status == "UNKNOWN" and metric.value is None
            assert metric.evidence is not None and metric.evidence.availability == "MODIFIED"
            gcd.unlink()
            gates = await invoke(client, "get_verification_gates", args)
            assert isinstance(gates, api.Gates)
            assert gates.gates["setup"].state == "BLOCKED"
            assert gates.gates["setup"].evidence[0].availability == "MISSING"
            assert gates.gates["platform_drc"].state == "PASSED"
            changed_acceptance = await invoke(client, "get_acceptance_summary", args)
            assert isinstance(changed_acceptance, api.AcceptanceSummary)
            assert changed_acceptance.gates["setup"] == "BLOCKED"

    asyncio.run(conversation())


def test_controller_status_over_stdio(tmp_path: Path) -> None:
    roots = tmp_path / "records"
    manifest_path = Path("configs/gcd-sky130hd.toml")
    manifest = load_manifest(manifest_path)
    assert isinstance(manifest, ORFSManifest)
    run = roots / "run"
    initialize(run, manifest_path, manifest, 512)
    with closing(sqlite3.connect(run / "run.sqlite3")) as db, db:
        db.execute(
            "INSERT INTO attempts(stage,state,detail,runtime_seconds,log,exit_code) "
            "VALUES ('synth','failed','token=private-input',1.25,'attempts/synth.log',17)"
        )
    config = registry_config(roots, run="run")
    before = snapshot_files(run)

    async def conversation() -> None:
        async with local_client(config) as client:
            result = await invoke(client, "get_stage_status", {"experiment_id": "run"})
            assert isinstance(result, api.StageStatus) and result.stages == {"synth": "failed"}
            assert (
                result.attempts[0].exit_code == 17 and result.attempts[0].duration_seconds == 1.25
            )
            assert "private-input" not in result.model_dump_json()
            resources = await invoke(client, "get_stage_resources", {"experiment_id": "run"})
            assert isinstance(resources, api.ResourceHistory)
            assert resources.schema_version == 1 and resources.allocated_cpus == 2
            assert resources.attempts[0]["dispatches"]["primary"]["status"] == "UNKNOWN"
            assert "private-input" not in resources.model_dump_json()

    asyncio.run(conversation())
    assert snapshot_files(run) == before


def test_filesystem_policy_rejects_links_traversal_and_changes(
    recorded: tuple[Path, Path, Path], tmp_path: Path
) -> None:
    config, gcd, _ = recorded
    registry = Registry.load(config)
    source = registry.experiments["gcd"]
    target = tmp_path / "snapshot"
    snapshot = Snapshot(source, target)
    assert snapshot.copy("reference.json")
    (source.root / "reference.json").write_text("{}", encoding="utf-8")
    with pytest.raises(UnsafeEvidence, match="changed"):
        snapshot.verify_source()
    for value in ("../private", "/private", "logs\\file", "logs/CON", "logs/file:secret"):
        with pytest.raises(UnsafeEvidence):
            snapshot.copy(value)
    write_reference(source.root, "gcd")
    outside = tmp_path / "outside.json"
    gcd.rename(outside)
    os.link(outside, gcd)
    with pytest.raises(UnsafeEvidence):
        load_evidence(source, tmp_path / "hardlink")
    gcd.unlink()
    try:
        gcd.symlink_to(outside)
    except OSError:
        if sys.platform == "win32":
            # Junctions are Windows reparse points and need no symlink privilege.
            logs = source.root / "logs"
            relocated = tmp_path / "outside-logs"
            logs.rename(relocated)
            subprocess.run(
                ["cmd.exe", "/d", "/c", "mklink", "/J", str(logs), str(relocated)],
                check=True,
                capture_output=True,
            )
        else:
            raise
    with pytest.raises(UnsafeEvidence):
        load_evidence(source, tmp_path / "symlink")


def test_registered_directory_replacement(recorded: tuple[Path, Path, Path]) -> None:
    config, _, _ = recorded
    registry = Registry.load(config)
    source = registry.experiments["gcd"]
    source.root.rename(source.root.with_name("original"))
    write_reference(source.root, "gcd")
    with pytest.raises(UnsafeEvidence, match="replaced"):
        load_evidence(source, config.parent / "snapshot")


def test_oversized_response_and_stdio_frame(recorded: tuple[Path, Path, Path]) -> None:
    config, _, _ = recorded
    data = api.ExperimentList(experiment_ids=["x"] * MAX_RESULT_BYTES, next_offset=None)
    oversized = bounded(data)
    assert isinstance(oversized, Failure) and oversized.error.code == "response_too_large"
    server = local_client(config).server
    assert isinstance(server, StdioServerParameters)
    process = subprocess.run(
        [server.command, *server.args],
        input=b"x" * (MAX_INPUT_CHARS + 1) + b"\n",
        capture_output=True,
        timeout=15,
        check=True,
    )
    assert not process.stdout
    assert json.loads(process.stderr)["outcome"] == "input_too_large"


def test_timeout_and_cancellation_kill_and_reap_worker(
    recorded: tuple[Path, Path, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    config, _, _ = recorded
    original = asyncio.create_subprocess_exec
    processes: list[asyncio.subprocess.Process] = []

    async def slow_worker(*_args: object, **_kwargs: object) -> asyncio.subprocess.Process:
        process = await original(
            sys.executable,
            "-I",
            "-c",
            "import time; time.sleep(60)",
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
        )
        processes.append(process)
        return process

    monkeypatch.setattr(asyncio, "create_subprocess_exec", slow_worker)
    registry = Registry.load(config)

    async def exercise() -> None:
        service = Service(registry, timeout=0.05)
        name = "get_timing_summary"
        source = registry.experiments["gcd"]
        result = await service.evidence(
            name, TOOLS[name], api.TimingArgs(experiment_id="gcd"), source
        )
        assert isinstance(result, Failure) and result.error.code == "timeout"
        assert processes[0].returncode is not None
        target = config.parent
        request = Request(
            tool=name,
            args={"experiment_id": "gcd"},
            source_path=str(source.root),
            source_device=source.identity[0],
            source_inode=source.identity[1],
        )
        with anyio.move_on_after(0.05) as cancel:
            await read_in_worker(request, TOOLS[name], target)
        assert cancel.cancelled_caught and processes[1].returncode is not None

    asyncio.run(exercise())


def test_official_client_cancels_inflight_request(recorded: tuple[Path, Path, Path]) -> None:
    config, _, _ = recorded
    # Inject a slow reader into the real server and keep the SDK transport,
    # discovery, request dispatch, and cancellation on the wire.
    code = """
import asyncio
import sys
from pathlib import Path
from autonomous_eda.mcp import server

async def wait(*args):
    server.audit("test", "started")
    await asyncio.sleep(60)

original = server.read_in_worker

async def slow_once(*args):
    server.read_in_worker = original
    await wait()

server.read_in_worker = slow_once
asyncio.run(server.serve(server.Registry.load(Path(sys.argv[1]))))
"""
    target = StdioServerParameters(command=sys.executable, args=["-I", "-c", code, str(config)])

    async def exercise() -> None:
        with (config.parent / "audit.log").open("w+", encoding="utf-8") as audit:
            async with Client(
                stdio_client(target, errlog=audit), read_timeout_seconds=10
            ) as client:
                await client.list_tools()
                with pytest.raises(MCPError):
                    await client.call_tool(
                        "get_timing_summary", {"experiment_id": "gcd"}, read_timeout_seconds=1
                    )
                with anyio.fail_after(10):
                    response = await invoke(client, "get_timing_summary", {"experiment_id": "gcd"})
                    assert isinstance(response, api.TimingSummary)
            audit.seek(0)
            events = [json.loads(line) for line in audit]
            assert any(event["outcome"] == "cancelled" for event in events)

    asyncio.run(exercise())

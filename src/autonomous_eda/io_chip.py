"""Evidence-bound SKY130 chip integration driven by the existing ICeWall checkpoint."""

from __future__ import annotations

import json
import re
import shutil
import subprocess
from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any

from . import io_chip_config, io_ring
from .io_chip_config import Status
from .io_chip_db import (
    LIBERTY,
    abstract_script,
    checked_mapping,
    connect_script,
    observe_script,
    route_script,
)
from .io_chip_layout import (
    CORE_GDS,
    KLAYOUT,
    LAYOUT_SCRIPT,
    STREAM,
    TECH,
    gds_matches,
    reopened,
    stream_args,
)
from .io_chip_run import (
    Stage,
    dispatch,
    image_hashes,
    openroad_args,
    pinned_version,
    verified_stage,
)
from .layout_render import LAYER_MAP, render_layout
from .manifest import fingerprint, sha256
from .orfs import preflight
from .reference import IMAGE, REVISION
from .verification.report import atomic, read_json, writer
from .visual_artifacts import Export
from .visual_charts import table

TRACKS = f"{io_ring.FLOW}/platforms/sky130hd/make_tracks.tcl"
GATES = {
    "lef_abstracts": "Requires OpenROAD abstract inspection",
    "pad_gds_views": "Requires matching pad, corner and filler GDS",
    "pad_liberty_interface": "Requires real Liberty pin directions and GPIO timing arcs",
    "pad_liberty": "Requires characterized wrapped GPIO Liberty",
    "pad_library_qualified": "Requires pin geometry equivalence and qualified timing views",
    "pad_placement": "Requires a verified ICeWall placement checkpoint",
    "logical_connectivity": "Requires complete chip mapping, functional GPIO modes and supplies",
    "database_signal_connectivity": "Requires a persisted OpenDB signal association",
    "signal_routing": "Requires detailed routes and readback for mapped core-to-pad nets",
    "power_connectivity": "Requires a qualified supply network and domain connectivity checks",
    "chip_gds": "Requires a routed DEF and matching nonempty cell views",
    "independent_reopening": "Requires a separate KLayout read of the exported GDS",
    "chip_geometry_extent": "Requires reopened geometry within the chip outline",
    "drc": "Requires a full-chip qualified DRC deck, supply network, and assembled layout",
    "lvs": "Requires full-chip extraction and an independent schematic including pad devices",
    "sta": "Requires pad modes, characterized models, extracted parasitics and chip constraints",
    "checkpoint_integrity": "Requires verified placement, tool and stage artifact hashes",
    "documentation": "Evaluated by repository documentation tests",
    "ci": "Evaluated by GitHub Actions for the reviewed commit",
}
LIBRARY_GATES = (
    "lef_abstracts",
    "pad_gds_views",
    "pad_liberty_interface",
    "pad_liberty",
    "pad_library_qualified",
)
SIGNOFF_GATES = ("drc", "lvs", "sta")
LayoutRunner = Callable[[str, dict[str, Any], dict[str, Path]], Stage]


def verdict(gates: dict[str, dict[str, str]]) -> Status:
    if gates["pad_library_qualified"]["status"] != "PASS":
        return "BLOCKED"
    states = {item["status"] for item in gates.values()}
    if "FAIL" in states:
        return "FAIL"
    return "PASS" if states == {"PASS"} else "BLOCKED"


@dataclass
class ChipReport:
    gates: dict[str, dict[str, str]] = field(
        default_factory=lambda: {
            name: {"status": "BLOCKED", "reason": reason} for name, reason in GATES.items()
        }
    )
    stages: dict[str, Any] = field(default_factory=dict)

    def set(self, name: str, status: Status, reason: str) -> bool:
        self.gates[name] = {"status": status, "reason": reason}
        return status == "PASS"

    def stage(self, root: Path, name: str, value: Stage) -> Stage:
        path = value.folder / "receipt.json"
        self.stages[name] = {
            **value.receipt,
            "receipt": path.relative_to(root).as_posix(),
            "receipt_sha256": sha256(path),
        }
        return value


def check_tool(report: ChipReport, gate: str, stage: Stage, marker: str, *outputs: str) -> bool:
    if stage.complete(marker, *outputs):
        return report.set(gate, "PASS", "Tool completed and produced hash-bound evidence")
    execution = stage.receipt["execution"]
    if execution["stop"] or execution["return_code"] is None:
        return report.set(gate, "BLOCKED", str(execution["stop"] or "tool did not run"))
    return report.set(gate, "FAIL", "Expected tool verdict or artifacts absent. See stage logs.")


def signal_readback(stage: Stage, mappings: list[tuple[str, ...]]) -> list[dict[str, Any]]:
    expected = {pad: (pin, external) for pad, _, _, pin, external, _ in mappings}
    observed = []
    for line in stage.output("observed.tsv").read_text().splitlines():
        kind, pad, pin, net, external, valid, length = line.split("\t")
        if kind != "signal" or expected.pop(pad, None) != (pin, external):
            raise ValueError("unexpected pad terminal observed in OpenDB")
        observed.append(
            {
                "pad": pad,
                "pin": pin,
                "core_net": net,
                "external_port": external,
                "associated": valid == "1",
                "wire_opcode_count": int(length),
            }
        )
    return observed


def tsv_prerequisites(stage: Stage) -> list[dict[str, Any]]:
    records = []
    for line in stage.output("connectivity.tsv").read_text().splitlines():
        kind, *fields = line.split("\t")
        if kind == "prerequisite":
            pad, pin, use, net, incompatible = fields
            records.append(
                {
                    "pad": pad,
                    "pin": pin,
                    "use": use,
                    "net": None if net == "-" else net,
                    "mixed_power_ground": incompatible == "1",
                }
            )
    return records


def expected_masters(config: dict[str, Any], placement: dict[str, Any]) -> dict[str, int]:
    names = Counter(pad["master"] for pad in config["pads"])
    names[config["corner"]] = len(placement["corners"])
    for name in config["fillers"]:
        names[name] = 0
    return dict(names)


def markdown(title: str, gates: dict[str, dict[str, str]]) -> str:
    rows = [f"# {title}", "", "| Gate | Status | Evidence or prerequisite |", "| --- | --- | --- |"]
    rows.extend(
        f"| {key.replace('_', ' ')} | {item['status']} | {item['reason']} |"
        for key, item in gates.items()
    )
    return "\n".join(rows) + "\n"


def assemble(placement_root: Path, config_path: Path, root: Path) -> dict[str, Any]:
    root = root.resolve()
    if root == placement_root.resolve():
        raise ValueError("use a separate chip evidence directory")
    config = io_chip_config.load(config_path.resolve())
    report = ChipReport()
    result: dict[str, Any] = {
        "schema_version": 1,
        "image": IMAGE,
        "orfs_revision": REVISION,
        "library_provenance": config.provenance,
        "signoff_policy": config.signoff,
        "signoff_policy_sha256": fingerprint({"schema_version": 1, "signoff": config.signoff}),
        "gates": report.gates,
        "stages": report.stages,
    }
    if config.signoff == "excluded":
        for name in SIGNOFF_GATES:
            report.set(name, "SKIPPED", "Manifest explicitly excludes full-chip signoff")
    root.mkdir(exist_ok=True, parents=True)
    with writer(root):
        # Withhold the previous summary during re-evaluation, while retaining stage attempts.
        atomic(root / "chip-report.json", {**result, "acceptance": "BLOCKED"})
        try:
            evaluate(placement_root.resolve(), root, config, result, report)
        except (OSError, ValueError, KeyError, TypeError, subprocess.TimeoutExpired) as error:
            result["diagnostic"] = str(error)
        result["acceptance"] = verdict(report.gates)
        library = {name: report.gates[name] for name in LIBRARY_GATES}
        qualification = {
            name: result[name]
            for name in ("schema_version", "image", "orfs_revision", "library_provenance")
        }
        for name in ("tools", "pinned_library", "pad_gds_inventory", "pad_liberty_interface"):
            if name in result:
                qualification[name] = result[name]
        qualification["gates"] = library
        qualification["stages"] = {
            name: report.stages[name] for name in LIBRARY_GATES if name in report.stages
        }
        atomic(root / "pad_library_qualification.json", qualification)
        (root / "pad_library_qualification.md").write_text(
            markdown("Pad library qualification", library), encoding="utf-8"
        )
        (root / "chip-report.md").write_text(
            markdown("ICeWall chip integration", report.gates), encoding="utf-8"
        )
        atomic(root / "chip-report.json", result)
    return result


def evaluate(
    placement_root: Path,
    root: Path,
    config: io_chip_config.ChipConfig,
    result: dict[str, Any],
    report: ChipReport,
) -> None:
    plan = io_ring.checked_plan(placement_root)
    placement = plan["configuration"]
    if not placement["enabled"]:
        for gate in GATES.keys() - {"documentation", "ci"}:
            report.set(gate, "SKIPPED", "Placement manifest disables chip IO")
        return
    result.update(top=placement["top"], platform=placement["platform"])
    if read_json(placement_root / "approval.json") != {"plan_digest": plan["plan_digest"]}:
        raise ValueError("placement plan approval is missing or differs from the plan")
    try:
        mappings = checked_mapping(config, placement)
    except ValueError as error:
        report.set("logical_connectivity", "FAIL", str(error))
        return
    docker = preflight()
    views = {f"{io_ring.FLOW}/{name}" for name in io_ring.inventory(placement)}
    deck_paths = [
        f"{io_ring.FLOW}/platforms/sky130hd/{name}"
        for name in (
            "drc/sky130hd.lydrc",
            "lvs/sky130hd.lylvs",
            "lib/sky130_fd_sc_hd__tt_025C_1v80.lib",
        )
    ]
    backend = image_hashes(docker, [*views, TRACKS, CORE_GDS, TECH, STREAM, KLAYOUT, *deck_paths])
    result["tools"] = {
        "OpenROAD": {
            "version": pinned_version(docker, io_ring.ENGINE, "-version"),
            "sha256": backend[io_ring.ENGINE],
        },
        "KLayout": {"version": pinned_version(docker, KLAYOUT, "-v"), "sha256": backend[KLAYOUT]},
    }
    engine_hash = {io_ring.ENGINE: backend[io_ring.ENGINE]}
    lef_hashes = {name: backend[name] for name in sorted(views) if name.endswith((".lef", ".tlef"))}
    result["core_only_collateral"] = {name: backend[name] for name in deck_paths}
    result["pinned_library"] = {name: backend[name] for name in sorted(views)}
    if plan["library"] != {**engine_hash, **result["pinned_library"]}:
        raise ValueError("placement library inventory differs from the pinned backend")
    result["placement_plan_digest"] = plan["plan_digest"]

    def openroad(
        name: str,
        script: str,
        outputs: tuple[str, ...],
        inputs: dict[str, Path] | None = None,
        collateral: dict[str, str] | None = None,
    ) -> Stage:
        return report.stage(
            root,
            name,
            dispatch(
                docker,
                root,
                name,
                io_ring.ENGINE,
                openroad_args(),
                backend={**engine_hash, **(collateral or {})},
                inputs=inputs or {},
                scripts={"stage.tcl": script},
                outputs=outputs,
            ),
        )

    def layout(
        name: str,
        spec: dict[str, Any],
        inputs: dict[str, Path],
    ) -> Stage:
        return report.stage(
            root,
            name,
            dispatch(
                docker,
                root,
                name,
                KLAYOUT,
                ["-b", "-r", "/work/probe.rb"],
                backend={KLAYOUT: backend[KLAYOUT]},
                inputs=inputs,
                scripts={
                    "probe.rb": LAYOUT_SCRIPT,
                    "layout.json": json.dumps(spec, sort_keys=True),
                },
                outputs=("layout-result.json",),
            ),
        )

    abstracts = openroad(
        "lef_abstracts", abstract_script(placement), ("abstracts.tsv",), collateral=lef_hashes
    )
    if not check_tool(
        report, "lef_abstracts", abstracts, "AEDA_ABSTRACTS_COMPLETE", "abstracts.tsv"
    ):
        return
    status = io_ring.report(placement_root)
    placement_valid = all(
        status["gates"][name] == "PASSED"
        for name in (
            "PAD_PLACEMENT_LEGAL",
            "PAD_CORNER_COMPLETENESS",
            "PAD_MAPPING_VALID",
        )
    ) and status.get("abstract_fillers_present")
    if not placement_valid:
        detail = status.get("diagnostic", GATES["pad_placement"])
        report.set(
            "pad_placement", "FAIL" if status["acceptance"] == "FAILED" else "BLOCKED", detail
        )
        return
    result["placement"] = status["placement"]
    result["placement_artifacts"] = status["artifacts"]
    masters = expected_masters(placement, status["placement"])
    gds_inputs = {f"pad-{i}.gds": path for i, path in enumerate(config.pad_gds)}
    gds_views = [f"/work/{name}" for name in gds_inputs]
    if gds_inputs and all(path.is_file() for path in gds_inputs.values()):
        scanning = layout(
            "pad_gds_views", {"mode": "library", "views": gds_views, "masters": masters}, gds_inputs
        )
        if check_tool(
            report, "pad_gds_views", scanning, "AEDA_LAYOUT_COMPLETE", "layout-result.json"
        ):
            observed = read_json(scanning.output("layout-result.json"))
            result["pad_gds_inventory"] = observed
            if not gds_matches(observed, masters):
                report.set(
                    "pad_gds_views",
                    "FAIL",
                    "Expected each selected LEF master in exactly one nonempty GDS cell",
                )
    if config.pad_liberty and config.pad_liberty.is_file():
        timing = openroad("pad_liberty_interface", LIBERTY, (), {"pad.lib": config.pad_liberty})
        if check_tool(report, "pad_liberty_interface", timing, "AEDA_LIBERTY_COMPLETE"):
            arcs = re.search(r"(?m)^AEDA_GPIO_TIMING_ARC_COUNT ([1-9]\d*)$", timing.stdout())
            if arcs is None:
                report.set("pad_liberty_interface", "FAIL", "Positive timing arc count missing")
            else:
                result["pad_liberty_interface"] = {"gpio_timing_arc_sets": int(arcs[1])}
            report.set(
                "pad_liberty",
                "BLOCKED",
                "Pad modes, voltage domains, and chip timing corner remain unqualified",
            )
        elif report.gates["pad_liberty_interface"]["status"] == "FAIL":
            report.set("pad_liberty", "FAIL", "The selected Liberty failed pin and arc validation")

    report.set(
        "pad_placement",
        "PASS",
        "Fixed pad orientations, locations, four corners and fillers verified",
    )
    placed = placement_root / "io-attempts" / status["attempt"]
    placement_inputs = {
        "placement.odb": placed / "placement.odb",
        "placement.def": placed / "placement.def",
        "core.v": placement_root / "core.v",
    }
    placement_hashes = {**status["artifacts"], **plan["inputs"]}
    if any(sha256(path) != placement_hashes[name] for name, path in placement_inputs.items()):
        raise ValueError("placement checkpoint changed before chip connection")
    connection = openroad(
        "connect",
        connect_script(mappings),
        ("connected.odb", "connected.def", "connectivity.tsv"),
        placement_inputs,
    )
    if not check_tool(
        report,
        "database_signal_connectivity",
        connection,
        "AEDA_MAPPING_COMPLETE",
        "connected.odb",
        "connected.def",
        "connectivity.tsv",
    ):
        if (
            "tool.stdout.log" in connection.receipt["outputs"]
            and "AEDA_MAPPING_VIOLATION" in connection.stdout()
        ):
            report.set(
                "logical_connectivity",
                "FAIL",
                "OpenDB reported an invalid mapped port, pin or driver",
            )
        return
    report.set("database_signal_connectivity", "BLOCKED", "Waiting for connected OpenDB readback")
    prerequisites = tsv_prerequisites(connection)
    result["unconnected_controls"] = [
        r for r in prerequisites if r["use"] == "SIGNAL" and r["net"] is None
    ]
    result["supply_terminals"] = [r for r in prerequisites if r["use"] in {"POWER", "GROUND"}]
    if any(r["mixed_power_ground"] for r in prerequisites):
        report.set(
            "power_connectivity",
            "FAIL",
            "OpenDB abutment associates POWER and GROUND terminals on shared nets",
        )

    def observe(
        name: str, checkpoint: Stage, filename: str, gate: str
    ) -> list[dict[str, Any]] | None:
        probe = openroad(
            name,
            observe_script(mappings, filename),
            ("observed.tsv",),
            {filename: checkpoint.output(filename)},
        )
        if not check_tool(report, gate, probe, "AEDA_OBSERVATION_COMPLETE", "observed.tsv"):
            return None
        report.set(gate, "BLOCKED", "Validating required terminal and net observations")
        return signal_readback(probe, mappings)

    samples = observe(
        "connected_readback", connection, "connected.odb", "database_signal_connectivity"
    )
    if samples is None:
        return
    result["signal_connections"] = samples
    if len(samples) != len(mappings) or any(not row["associated"] for row in samples):
        report.set(
            "database_signal_connectivity",
            "FAIL",
            "Reopened OpenDB is missing required pad terminals",
        )
        return
    report.set(
        "database_signal_connectivity", "PASS", "Reopened OpenDB verified all mapped pad terminals"
    )
    report.set("checkpoint_integrity", "PASS", "Verified artifact hashes at each completed stage")
    if not config.route:
        report.set("signal_routing", "SKIPPED", "Manifest explicitly disables detailed routing")
        return
    route = openroad(
        "route",
        route_script(config, mappings),
        ("routed.odb", "routed.def", "route.guide", "route-drc.rpt"),
        {"connected.odb": connection.output("connected.odb")},
        {TRACKS: backend[TRACKS]},
    )
    if not check_tool(
        report, "signal_routing", route, "AEDA_ROUTING_COMPLETE", "routed.odb", "routed.def"
    ):
        return
    report.set("signal_routing", "BLOCKED", "Waiting for routed OpenDB readback")
    drvs = re.findall(r"(?m)^AEDA_ROUTE_VIOLATIONS ([0-9]+)$", route.stdout())
    length = re.findall(r"(?m)^Total wire length = ([\d.]+) um\.$", route.stdout())
    routed = observe("route_readback", route, "routed.odb", "signal_routing")
    if routed is None:
        return
    result["routed_signal_connections"] = routed
    result["detailed_router_violations"] = int(drvs[0]) if len(drvs) == 1 else None
    result["total_signal_wire_length_um"] = float(length[-1]) if length else None
    if (
        drvs != ["0"]
        or len(routed) != len(mappings)
        or any(not row["associated"] or row["wire_opcode_count"] <= 0 for row in routed)
    ):
        report.set(
            "signal_routing",
            "FAIL",
            "Reopened routing has missing terminal/wire evidence or router violations",
        )
        return
    report.set(
        "signal_routing", "PASS", "Detailed router completed and mapped routes were read back"
    )
    if report.gates["pad_gds_views"]["status"] != "PASS":
        return

    export_layout(
        docker, root, report, result, placement, route, backend, gds_inputs, gds_views, layout
    )


def export_layout(
    docker: str,
    root: Path,
    report: ChipReport,
    result: dict[str, Any],
    placement: dict[str, Any],
    route: Stage,
    backend: dict[str, str],
    gds_inputs: dict[str, Path],
    gds_views: list[str],
    layout: LayoutRunner,
) -> None:
    # The pinned technology references merged.lef, which must accompany the DEF.
    technology_script = f"""cp {TECH} /work/technology.lyt
cat {" ".join(name for name in backend if name.endswith((".tlef", ".lef")))} > /work/merged.lef
exec {KLAYOUT} "$@"
"""
    top = placement["top"]
    stream = report.stage(
        root,
        "chip_gds",
        dispatch(
            docker,
            root,
            "chip_gds",
            "/bin/sh",
            ["/work/stream.sh", *stream_args(top, gds_views)],
            backend={
                name: digest
                for name, digest in backend.items()
                if name in {TECH, CORE_GDS, STREAM, KLAYOUT} or name.endswith((".tlef", ".lef"))
            },
            inputs={**gds_inputs, "routed.def": route.output("routed.def")},
            scripts={"stream.sh": technology_script},
            outputs=("chip.gds",),
        ),
    )
    if stream.receipt["execution"]["return_code"] != 0 or stream.receipt["execution"]["stop"]:
        report.set(
            "chip_gds",
            "BLOCKED" if stream.receipt["execution"]["stop"] else "FAIL",
            "KLayout stream-out did not complete. See stage logs.",
        )
        return
    if "chip.gds" not in stream.receipt["outputs"] or stream.output("chip.gds").stat().st_size == 0:
        report.set("chip_gds", "FAIL", "KLayout stream-out generated an empty file")
        return
    report.set("chip_gds", "PASS", "ORFS and KLayout stream-out produced a nonempty GDS")
    def_text = route.output("routed.def").read_text()
    components = def_text.split("\nCOMPONENTS ", 1)[1].split("\nEND COMPONENTS", 1)[0]
    counts = dict(Counter(re.findall(r"(?m)^\s*- \S+ (\S+)", components)))
    gds = stream.output("chip.gds")
    check = layout(
        "independent_reopening",
        {
            "mode": "reopen",
            "top": top,
            "views": ["/work/chip.gds"],
            "size_um": [placement["width_um"], placement["height_um"]],
        },
        {"chip.gds": gds},
    )
    if check_tool(
        report, "independent_reopening", check, "AEDA_LAYOUT_COMPLETE", "layout-result.json"
    ):
        result["reopened_layout"] = read_json(check.output("layout-result.json"))
        if not reopened(result["reopened_layout"], top, counts):
            report.set(
                "independent_reopening",
                "FAIL",
                "Reopened hierarchy, macro counts, chip boundary or required geometry differs",
            )
        elif result["reopened_layout"]["extent_inside_die"]:
            report.set(
                "chip_geometry_extent", "PASS", "Reopened geometrical bounds fit the outline"
            )
        else:
            report.set(
                "chip_geometry_extent", "FAIL", "Reopened geometry extends beyond the chip outline"
            )


def render(root: Path) -> dict[str, Any]:
    source = root / "chip-report.json"
    inputs = {source.name: sha256(source)}
    result = read_json(source)
    if result["gates"]["database_signal_connectivity"]["status"] != "PASS":
        raise ValueError("run a verified chip probe before rendering")
    for stage in result["stages"].values():
        receipt = root / stage["receipt"]
        if not receipt.resolve().is_relative_to(root.resolve()):
            raise ValueError("chip stage receipt escapes the run")
        current = verified_stage(receipt.parent.parent, stage["key"])
        if current.folder != receipt.parent or sha256(receipt) != stage["receipt_sha256"]:
            raise ValueError("stale chip report. Rerun the chip probe before rendering.")
    top, platform = result["top"], result["platform"]
    detail = f"{top}  |  {platform}  |  ICeWall chip probe"
    with TemporaryDirectory(prefix="aeda-chip-render-") as directory:
        work = Path(directory)
        export = Export(root, work, inputs[source.name][:32])
        export.plot(
            "signoff_gate_matrix",
            "Chip integration gates",
            detail,
            inputs,
            lambda ax: table(
                ax,
                ["Gate", "Outcome"],
                [
                    [key.replace("_", " ").capitalize(), item["status"]]
                    for key, item in result["gates"].items()
                ],
                widths=[0.62, 0.38],
            ),
        )
        connections = result.get("routed_signal_connections", result["signal_connections"])
        export.plot(
            "connectivity_matrix",
            "Core-to-pad signal connectivity",
            detail,
            inputs,
            lambda ax: table(
                ax,
                ["Pad pin", "Core net", "Chip port", "OpenDB wire"],
                [
                    [
                        f"{row['pad']}/{row['pin']}",
                        row["core_net"],
                        row["external_port"],
                        "Present" if row["wire_opcode_count"] else "Absent",
                    ]
                    for row in connections
                ],
                widths=[0.34, 0.17, 0.24, 0.25],
            ),
        )
        for image in export.images.values():
            image["render_configuration"].update(
                command="autonomous-eda io chip-render RUN_DIRECTORY", top=top, platform=platform
            )
        if result["gates"]["independent_reopening"]["status"] == "PASS":
            stage = result["stages"]["chip_gds"]
            relative_gds = Path(stage["receipt"]).parent / "chip.gds"
            shutil.copyfile(root / relative_gds, work / "source.gds")
            staged_gds = sha256(work / "source.gds")
            if staged_gds != stage["outputs"]["chip.gds"]:
                raise ValueError("chip GDS changed during rendering")
            version, settings = render_layout(work, top)
            settings.update(
                platform=platform,
                command=[
                    "klayout",
                    "-zz",
                    "-r",
                    "/work/render.rb",
                    "-rd",
                    f"layer_map={LAYER_MAP}",
                    "-rd",
                    f"top={top}",
                ],
            )
            for name in ("layout_overview", "layout_routing"):
                export.add(
                    name,
                    "klayout",
                    version,
                    {
                        **inputs,
                        relative_gds.as_posix(): staged_gds,
                    },
                    settings,
                )
        if sha256(source) != inputs[source.name]:
            raise ValueError("chip report changed during rendering")
        return export.publish()

"""Digest-approved ICeWall placement for the pinned Sky130 IO abstract views."""

from __future__ import annotations

import argparse
import json
import re
import shutil
import subprocess
import time
import tomllib
import uuid
from dataclasses import asdict
from pathlib import Path
from typing import Any

from .manifest import fingerprint, identifier, integer, relative_path, sha256
from .mcp.snapshot import ordinary
from .orfs import preflight
from .reference import IMAGE, ORFS_ROOT, REVISION
from .verification.policy import table
from .verification.process import execute
from .verification.report import atomic, read_json, writer

FLOW = f"{ORFS_ROOT}/flow"
ENGINE = f"{ORFS_ROOT}/tools/install/OpenROAD/bin/openroad"
PREFIX = "sky130_ef_io__"
GPIO = PREFIX + "gpiov2_pad_wrapped"
POWER = PREFIX + "vccd_lvc_pad"
GROUND = PREFIX + "vssd_lvc_pad"
CORNER = PREFIX + "corner_pad"
FILLERS = tuple(PREFIX + f"com_bus_slice_{size}um" for size in (20, 10, 5, 1))
SIDES = ("north", "south", "east", "west")
KINDS = {"input": GPIO, "output": GPIO, "power": POWER, "ground": GROUND}
GATES = (
    "PAD_LIBRARY_VALID",
    "PAD_MAPPING_VALID",
    "PAD_PLACEMENT_LEGAL",
    "PAD_CORNER_COMPLETENESS",
    "PAD_FILLER_CONTINUITY",
    "PAD_SIGNAL_CONNECTIVITY",
    "PAD_POWER_CONNECTIVITY",
    "PAD_GEOMETRY_VALID",
    "PAD_GDS_REOPEN",
    "PAD_DRC",
    "PAD_LVS",
)
LEFS = (
    "platforms/sky130hd/lef/sky130_fd_sc_hd.tlef",
    "platforms/sky130hd/lef/sky130_fd_sc_hd_merged.lef",
)


def checked_config(data: object) -> dict[str, Any]:
    fields = {
        "schema_version",
        "enabled",
        "platform",
        "top",
        "netlist",
        "width_um",
        "height_um",
        "clearance_um",
        "offset_um",
        "corner",
        "fillers",
        "pads",
    }
    raw = table(data, fields, "IO configuration")
    if type(raw.get("schema_version")) is not int or raw["schema_version"] != 1:
        raise ValueError("IO configuration requires schema_version = 1")
    if type(raw.get("enabled")) is not bool:
        raise ValueError("enabled must be a Boolean")
    if raw["enabled"] is False:
        if set(raw) != {"schema_version", "enabled"}:
            raise ValueError("disabled IO configuration accepts only schema_version and enabled")
        return dict(raw)
    if set(raw) != fields or raw["platform"] != "sky130hd":
        raise ValueError("enabled IO requires all documented fields and platform sky130hd")
    top = identifier(raw["top"], "top")
    netlist = str(relative_path(raw["netlist"], "netlist", (".v",)))
    width = integer(raw["width_um"], "width_um", 10000)
    height = integer(raw["height_um"], "height_um", 10000)
    clearance = integer(raw["clearance_um"], "clearance_um", 5000)
    offset = raw["offset_um"]
    if type(offset) is not int or not 0 <= offset <= 500:
        raise ValueError("offset_um must be an integer between 0 and 500")
    if clearance < offset + 220 or min(width, height) <= 2 * clearance:
        raise ValueError("die and core clearances cannot accommodate the pinned IO rows")
    if raw["corner"] != CORNER:
        raise ValueError("unsupported Sky130 IO corner master")
    fillers = raw["fillers"]
    if not isinstance(fillers, list) or not fillers or any(item not in FILLERS for item in fillers):
        raise ValueError("provide supported Sky130 IO filler masters")
    if len(set(fillers)) != len(fillers) or FILLERS[-1] not in fillers:
        raise ValueError("fillers must be unique and include the 1 µm master")
    raw_pads = raw["pads"]
    if not isinstance(raw_pads, list) or not 4 <= len(raw_pads) <= 64:
        raise ValueError("provide 4 to 64 pads")
    pads: list[dict[str, Any]] = []
    names = set()
    ports = set()
    for entry in raw_pads:
        item = table(
            entry, {"name", "kind", "master", "side", "location_um", "mirror", "port"}, "pad"
        )
        name = identifier(item.get("name"), "pad name")
        kind, side = item.get("kind"), item.get("side")
        if not isinstance(kind, str) or kind not in KINDS or side not in SIDES:
            raise ValueError(f"invalid pad kind or side for {name}")
        if item.get("master") != KINDS[kind] or name in names or name.startswith("IO_"):
            raise ValueError(f"invalid master or duplicate/reserved pad name: {name}")
        names.add(name)
        mirror = item.get("mirror", False)
        if type(mirror) is not bool or mirror:
            raise ValueError("mirrored placement is unqualified for these Sky130 rows")
        port = item.get("port")
        if kind in {"input", "output"}:
            port = identifier(port, "core port")
            if port in ports:
                raise ValueError(f"duplicate core port mapping: {port}")
            ports.add(port)
        elif port is not None:
            raise ValueError("supply pad allocation uses the selected power/ground master")
        location = item.get("location_um")
        span = width if side in {"north", "south"} else height
        if type(location) is not int or not offset + 220 <= location <= span - offset - 300:
            raise ValueError(f"illegal fixed location for {name}")
        pads.append(
            {
                "name": name,
                "kind": kind,
                "side": side,
                "master": KINDS[kind],
                "location_um": location,
                "port": port,
            }
        )
    if set(item["side"] for item in pads) != set(SIDES):
        raise ValueError("each side needs at least one pad")
    if not {"power", "ground"} <= {item["kind"] for item in pads}:
        raise ValueError("allocate both digital power and ground pads")
    pads.sort(key=lambda item: (item["side"], item["location_um"]))
    for a, b in zip(pads, pads[1:], strict=False):
        if a["side"] == b["side"] and b["location_um"] - a["location_um"] < 80:
            raise ValueError("fixed pad intervals overlap")
    return {
        **raw,
        "top": top,
        "netlist": netlist,
        "pads": pads,
        "fillers": [name for name in FILLERS if name in fillers],
    }


def load_config(path: Path) -> dict[str, Any]:
    with path.open("rb") as stream:
        raw = stream.read(64 * 1024 + 1)
    if len(raw) > 64 * 1024:
        raise ValueError("IO configuration exceeds its size limit")
    return checked_config(tomllib.loads(raw.decode("utf-8")))


def inventory(config: dict[str, Any]) -> list[str]:
    names = {config["corner"], *config["fillers"], *(pad["master"] for pad in config["pads"])}
    return [
        *LEFS,
        "platforms/sky130io/verilog/sky130_io.blackbox.v",
        "platforms/sky130io/lib/sky130_dummy_io.lib",
        *(f"platforms/sky130io/lef/{name}.lef" for name in sorted(names)),
    ]


def backend_hashes(docker: str, files: list[str]) -> dict[str, str]:
    output = subprocess.run(
        [
            docker,
            "run",
            "--rm",
            "--pull",
            "never",
            "--network",
            "none",
            "--cpus",
            "1",
            "--memory",
            "256m",
            "--entrypoint",
            "sha256sum",
            IMAGE,
            ENGINE,
            *(f"{FLOW}/{file}" for file in files),
        ],
        check=False,
        capture_output=True,
        text=True,
        timeout=30,
    )
    if output.returncode:
        raise ValueError("pinned OpenROAD executable or required SKY130 views are missing")
    records = {}
    for row in output.stdout.splitlines():
        digest, path = row.split(maxsplit=1)
        records[path] = digest
    if set(records) != {ENGINE, *(f"{FLOW}/{name}" for name in files)} or any(
        not re.fullmatch(r"[0-9a-f]{64}", value) for value in records.values()
    ):
        raise ValueError("incomplete pinned view inventory")
    return records


def script(config: dict[str, Any]) -> str:
    width, height = config["width_um"], config["height_um"]
    margin = config["clearance_um"]
    lines = [
        *(
            f"read_lef {FLOW}/{file}"
            for file in inventory(config)
            if file.endswith((".lef", ".tlef"))
        ),
        "read_verilog /work/core.v",
        f"link_design {config['top']}",
        "set block [ord::get_db_block]",
        "set ports [dict create]",
        "foreach term [$block getBTerms] {dict set ports [$term getName] [$term getIoType]}",
    ]
    expected = [f"{item['port']} {item['kind'].upper()}" for item in config["pads"] if item["port"]]
    lines.extend(
        [
            f"set expected [dict create {' '.join(expected)}]",
            "if {[lsort [dict keys $ports]] ne [lsort [dict keys $expected]]} {",
            '  error "core port inventory does not match the approved mapping"',
            "}",
            "dict for {name direction} $expected {",
            "  if {[dict get $ports $name] ne $direction} {",
            '    error "core port direction mismatch: $name"',
            "  }",
            "}",
            'puts "AEDA_IO_MAPPING_COMPLETE"',
            f"initialize_floorplan -die_area {{0 0 {width} {height}}} "
            f"-core_area {{{margin} {margin} {width - margin} {height - margin}}} -site unithd",
            "make_fake_io_site -name aeda_side -width 1 -height 210.965",
            "make_fake_io_site -name aeda_corner -width 200 -height 204",
            "make_io_sites -horizontal_site aeda_side -vertical_site aeda_side "
            f"-corner_site aeda_corner -offset {config['offset_um']}",
            f"place_corners {config['corner']}",
            *(
                f"place_pad -master {pad['master']} -row IO_{pad['side'].upper()} "
                f"-location {pad['location_um']} {pad['name']}"
                for pad in config["pads"]
            ),
            *(
                f"place_io_fill -row IO_{side.upper()} {' '.join(config['fillers'])}"
                for side in SIDES
            ),
            "connect_by_abutment",
            "write_db /work/placement.odb",
            "write_def /work/placement.def",
            'puts "AEDA_IO_PLACEMENT_COMPLETE"',
        ]
    )
    return "\n".join(lines) + "\n"


def plan(config_path: Path, root: Path) -> dict[str, Any]:
    config = load_config(config_path)
    root.mkdir(parents=True, exist_ok=False)
    record: dict[str, Any] = {
        "schema_version": 1,
        "configuration": config,
        "image": IMAGE,
        "orfs_revision": REVISION,
        "inputs": {},
        "library": {},
    }
    if config["enabled"]:
        source_root = config_path.resolve().parent
        netlist = (source_root / config["netlist"]).resolve()
        if not netlist.is_relative_to(source_root) or not netlist.is_file():
            raise ValueError("mapped core netlist is missing or out of scope")
        if netlist.stat().st_size > 16 * 1024**2:
            raise ValueError("mapped core netlist exceeds its size limit")
        shutil.copyfile(netlist, root / "core.v")
        (root / "placement.tcl").write_text(script(config), encoding="utf-8", newline="\n")
        record["inputs"] = {name: sha256(root / name) for name in ("core.v", "placement.tcl")}
        record["library"] = backend_hashes(preflight(), inventory(config))
    record["plan_digest"] = fingerprint(record)
    atomic(root / "plan.json", record)
    return record


def checked_plan(root: Path) -> dict[str, Any]:
    ordinary(root, directory=True)
    value = read_json(root / "plan.json")
    body = {key: item for key, item in value.items() if key != "plan_digest"}
    if (
        value["plan_digest"] != fingerprint(body)
        or value["image"] != IMAGE
        or value["orfs_revision"] != REVISION
        or value["schema_version"] != 1
    ):
        raise ValueError("IO plan identity changed")
    config = value["configuration"]
    if config["enabled"]:
        for name in ("core.v", "placement.tcl"):
            ordinary(root / name, directory=False)
        normalized = dict(config)
        normalized["pads"] = [
            {key: val for key, val in pad.items() if val is not None} for pad in config["pads"]
        ]
        if checked_config(normalized) != config:
            raise ValueError("IO plan configuration is not canonical")
        if (root / "placement.tcl").read_text(encoding="utf-8") != script(config):
            raise ValueError("IO Tcl differs from the approved generator")
        if value["inputs"] != {name: sha256(root / name) for name in ("core.v", "placement.tcl")}:
            raise ValueError("IO input hash mismatch")
    else:
        checked_config(config)
    return value


def approve(root: Path, digest: str) -> dict[str, Any]:
    value = checked_plan(root)
    if digest != value["plan_digest"]:
        raise ValueError("approval digest does not match the IO plan")
    receipt = {"plan_digest": digest}
    atomic(root / "approval.json", receipt)
    return receipt


def inspect_def(text: str, config: dict[str, Any]) -> dict[str, Any]:
    fixed = {
        match[1]: {
            "master": match[2],
            "x_dbu": int(match[3]),
            "y_dbu": int(match[4]),
            "orientation": match[5],
        }
        for match in re.finditer(
            r"(?m)^\s*- (\w+) (\w+) \+ FIXED \( (-?\d+) (-?\d+) \) (\w+) ;$", text
        )
    }
    units = re.search(r"UNITS DISTANCE MICRONS (\d+) ;", text)
    if units is None:
        raise ValueError("DEF lacks distance units")
    dbu = int(units[1])
    die = re.search(r"(?m)^DIEAREA \( 0 0 \) \( (\d+) (\d+) \) ;$", text)
    die_matches = die is not None and (int(die[1]), int(die[2])) == (
        config["width_um"] * dbu,
        config["height_um"] * dbu,
    )
    orientations = {"north": "FS", "south": "N", "east": "W", "west": "FW"}
    legal = (
        dbu > 0
        and die_matches
        and all(
            pad["name"] in fixed
            and fixed[pad["name"]]["master"] == pad["master"]
            and fixed[pad["name"]]["orientation"] == orientations[pad["side"]]
            and fixed[pad["name"]]["x_dbu" if pad["side"] in {"north", "south"} else "y_dbu"]
            == pad["location_um"] * dbu
            for pad in config["pads"]
        )
    )
    corners = [
        name
        for name, item in fixed.items()
        if name.startswith("IO_CORNER_") and item["master"] == config["corner"]
    ]
    fillers = {
        side: sum(
            name.startswith(f"IO_FILL_IO_{side.upper()}_") and item["master"] in config["fillers"]
            for name, item in fixed.items()
        )
        for side in SIDES
    }
    return {
        "dbu_per_um": dbu,
        "placement_matches": legal,
        "corners": corners,
        "fillers_by_side": fillers,
        "pads": {p["name"]: fixed.get(p["name"]) for p in config["pads"]},
    }


def report(root: Path) -> dict[str, Any]:
    root = root.resolve()
    outcome = {name: "BLOCKED" for name in GATES}
    try:
        value = checked_plan(root)
    except (OSError, ValueError, KeyError, TypeError) as error:
        return {
            "schema_version": 1,
            "gates": outcome,
            "acceptance": "BLOCKED",
            "diagnostic": str(error),
        }
    config = value["configuration"]
    digest = value["plan_digest"]
    result: dict[str, Any] = {
        "schema_version": 1,
        "plan_digest": digest,
        "image": IMAGE,
        "gates": outcome,
        "acceptance": "BLOCKED",
    }
    if not config["enabled"]:
        result.update(gates=dict.fromkeys(GATES, "NOT_APPLICABLE"), acceptance="NOT_APPLICABLE")
        return result
    result["diagnostic"] = (
        "The pinned IO library has no matching pad GDS or qualified Liberty. "
        "The placement probe leaves routed connectivity and chip decks unresolved."
    )
    receipt_path = root / "io-latest.json"
    if not receipt_path.is_file():
        return result
    try:
        selected = read_json(receipt_path)
        attempt = selected["attempt"]
        if selected["plan_digest"] != digest or not re.fullmatch(r"[0-9a-f]{32}", attempt):
            raise ValueError("IO attempt identity differs")
        result["attempt"] = attempt
        work = root / "io-attempts" / attempt
        ordinary(work.parent, directory=True)
        ordinary(work, directory=True)
        receipt = read_json(work / "receipt.json")
        if selected["receipt_sha256"] != sha256(work / "receipt.json"):
            raise ValueError("IO receipt hash mismatch")
        for name, expected in receipt["artifacts"].items():
            if name not in {
                "placement.def",
                "placement.odb",
                "openroad.stdout.log",
                "openroad.stderr.log",
            }:
                raise ValueError(f"unknown IO artifact: {name}")
            ordinary(work / name, directory=False)
            if sha256(work / name) != expected:
                raise ValueError(f"IO artifact hash mismatch: {name}")
        if not {"openroad.stdout.log", "openroad.stderr.log"} <= receipt["artifacts"].keys():
            raise ValueError("IO tool logs are unavailable")
        result.update(artifacts=receipt["artifacts"], execution=receipt["execution"])
        stdout = (work / "openroad.stdout.log").read_text(encoding="utf-8", errors="replace")
        if "AEDA_IO_MAPPING_COMPLETE" in stdout.splitlines():
            outcome["PAD_MAPPING_VALID"] = "PASSED"
        elif "core port" in stdout or "core port" in (work / "openroad.stderr.log").read_text(
            encoding="utf-8", errors="replace"
        ):
            outcome["PAD_MAPPING_VALID"] = "FAILED"
        if (
            receipt["execution"]["return_code"] == 0
            and not receipt["execution"]["stop"]
            and "AEDA_IO_PLACEMENT_COMPLETE" in stdout.splitlines()
            and {"placement.odb", "placement.def"} <= receipt["artifacts"].keys()
        ):
            detail = inspect_def((work / "placement.def").read_text(encoding="utf-8"), config)
            result["placement"] = detail
            result["abstract_library"] = "VERIFIED"
            outcome["PAD_PLACEMENT_LEGAL"] = "PASSED" if detail["placement_matches"] else "FAILED"
            outcome["PAD_CORNER_COMPLETENESS"] = (
                "PASSED" if len(detail["corners"]) == 4 else "FAILED"
            )
            result["abstract_fillers_present"] = all(detail["fillers_by_side"].values())
        if "FAILED" in outcome.values():
            result["acceptance"] = "FAILED"
    except (OSError, ValueError, KeyError, TypeError) as error:
        result["diagnostic"] = str(error)
    return result


def run(root: Path) -> dict[str, Any]:
    root = root.resolve()
    with writer(root):
        value = checked_plan(root)
        digest = value["plan_digest"]
        if read_json(root / "approval.json") != {"plan_digest": digest}:
            raise ValueError("approve the exact IO plan before dispatch")
        if not value["configuration"]["enabled"]:
            return report(root)
        docker = preflight()
        if backend_hashes(docker, inventory(value["configuration"])) != value["library"]:
            raise ValueError("pinned IO library or executable hash changed")
        previous = report(root)
        if all(
            previous["gates"][name] == "PASSED"
            for name in (
                "PAD_MAPPING_VALID",
                "PAD_PLACEMENT_LEGAL",
                "PAD_CORNER_COMPLETENESS",
            )
        ) and previous.get("abstract_fillers_present"):
            return previous
        attempt = uuid.uuid4().hex
        history = root / "io-attempts"
        history.mkdir(exist_ok=True)
        ordinary(history, directory=True)
        work = history / attempt
        work.mkdir()
        selection = {"plan_digest": digest, "attempt": attempt, "receipt_sha256": None}
        atomic(root / "io-latest.json", selection)
        for name, expected in value["inputs"].items():
            shutil.copyfile(root / name, work / name)
            if sha256(work / name) != expected:
                raise ValueError("approved IO input changed during staging")
        container = f"aeda-io-{attempt}"
        command = [
            docker,
            "run",
            "--rm",
            "--pull",
            "never",
            "--network",
            "none",
            "--name",
            container,
            "--cpus",
            "1",
            "--memory",
            "1200m",
            "--pids-limit",
            "128",
            "-v",
            f"{work}:/work",
            "--entrypoint",
            ENGINE,
            IMAGE,
            "-no_init",
            "-threads",
            "1",
            "-exit",
            "/work/placement.tcl",
        ]
        execution = execute(command, work, "openroad", time.monotonic() + 90, lambda: False)
        if execution.stop:
            subprocess.run(
                [docker, "rm", "-f", container], check=False, capture_output=True, timeout=10
            )
        artifacts = {
            name: sha256(work / name)
            for name in (execution.stdout, execution.stderr, "placement.odb", "placement.def")
            if (work / name).is_file()
        }
        receipt = work / "receipt.json"
        atomic(receipt, {"artifacts": artifacts, "execution": asdict(execution)})
        atomic(root / "io-latest.json", {**selection, "receipt_sha256": sha256(receipt)})
        if execution.stop == "cancelled":
            raise KeyboardInterrupt
        return report(root)


def configure(parser: argparse.ArgumentParser) -> None:
    actions = parser.add_subparsers(dest="action", required=True)
    actions.add_parser("doctor")
    create = actions.add_parser("plan")
    create.add_argument("config", type=Path)
    create.add_argument("directory", type=Path)
    approval = actions.add_parser("approve")
    approval.add_argument("directory", type=Path)
    approval.add_argument("--digest", required=True)
    for name in ("run", "resume", "report", "verify"):
        actions.add_parser(name).add_argument("directory", type=Path)
    render = actions.add_parser("render")
    render.add_argument("directory", type=Path)
    render.add_argument("--all", action="store_true", help="export all available IO charts")
    chip = actions.add_parser(
        "chip", help="connect and route the approved SKY130 pad-ring checkpoint"
    )
    chip.add_argument("placement", type=Path)
    chip.add_argument("config", type=Path)
    chip.add_argument("directory", type=Path)
    chip.add_argument("--require-acceptance", action="store_true")
    actions.add_parser("chip-render", help="render verified chip experiment outputs").add_argument(
        "directory", type=Path
    )


def render_report(root: Path) -> dict[str, Any]:
    from tempfile import TemporaryDirectory

    from .visual_artifacts import Export, atomic_json
    from .visual_charts import bars, table

    root = root.resolve()
    value = report(root)
    if "placement" not in value:
        raise ValueError("verified OpenDB and DEF placement evidence is required")
    source = root / "io-report.json"
    atomic_json(source, value)
    sources = {source.name: sha256(source)}
    detail = f"Sky130 IO abstracts  |  Attempt {value['attempt'][:12]}"
    with TemporaryDirectory(prefix="aeda-io-render-") as directory:
        export = Export(root, Path(directory), value["attempt"])
        export.plot(
            "io_verification",
            "IO placement and verification",
            detail,
            sources,
            lambda ax: table(
                ax,
                ["Gate", "Outcome"],
                [
                    [name.removeprefix("PAD_").replace("_", " ").lower(), state]
                    for name, state in value["gates"].items()
                ],
            ),
        )
        counts = value["placement"]["fillers_by_side"]
        export.plot(
            "io_fillers",
            "Placed continuity cells",
            detail,
            sources,
            lambda ax: bars(
                ax,
                [side.title() for side in SIDES],
                [counts[side] for side in SIDES],
                "Placed instances (count)",
            ),
        )
        if fingerprint(report(root)) != fingerprint(value):
            raise ValueError("IO placement evidence changed during rendering")
        return export.publish(replace_types=set(export.images) | set(export.unavailable))


def main(args: argparse.Namespace) -> int:
    result: dict[str, Any]
    if args.action == "doctor":
        views = backend_hashes(
            preflight(),
            inventory(
                {
                    "corner": CORNER,
                    "fillers": FILLERS,
                    "pads": [{"master": name} for name in sorted(set(KINDS.values()))],
                }
            ),
        )
        result = {
            "image": IMAGE,
            "placement_views": views,
            "pad_gds": "BLOCKED",
            "pad_timing_liberty": "BLOCKED",
        }
    elif args.action == "plan":
        result = plan(args.config, args.directory)
    elif args.action == "approve":
        result = approve(args.directory, args.digest)
    elif args.action in {"run", "resume"}:
        result = run(args.directory)
    elif args.action == "render":
        result = render_report(args.directory)
    elif args.action == "chip":
        from .io_chip import assemble

        result = assemble(args.placement, args.config, args.directory)
        print(json.dumps(result, indent=2, allow_nan=False))
        if args.require_acceptance:
            return 0 if result["acceptance"] == "PASS" else 1
        directly_checked = ("database_signal_connectivity", "signal_routing")
        return int(
            result["gates"][directly_checked[0]]["status"] != "PASS"
            or result["gates"][directly_checked[1]]["status"] not in {"PASS", "SKIPPED"}
        )
    elif args.action == "chip-render":
        from .io_chip import render

        result = render(args.directory.resolve())
    else:
        result = report(args.directory)
    print(json.dumps(result, indent=2, allow_nan=False))
    if args.action == "verify":
        return 0 if result["acceptance"] in {"PASSED", "NOT_APPLICABLE"} else 1
    if args.action in {"run", "resume"}:
        return 0 if result["gates"]["PAD_PLACEMENT_LEGAL"] in {"PASSED", "NOT_APPLICABLE"} else 1
    return 0

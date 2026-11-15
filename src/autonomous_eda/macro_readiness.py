"""Inventory a declared hard macro and check its native logical and geometry views."""

from __future__ import annotations

import json
import shutil
import tomllib
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .manifest import identifier, integer, relative_path, sha256
from .orfs import FLOW, container_command, execute, preflight
from .reference import IMAGE
from .verification.report import atomic

VIEW_SUFFIXES = {
    "verilog": (".v", ".sv"),
    "lef": (".lef",),
    "gds": (".gds",),
    "liberty": (".lib",),
}
IMPLEMENTATIONS = {"registers", "technology_mapped", "hard_macro", "unresolved"}
LEF_READER = """read_lef /OpenROAD-flow-scripts/flow/platforms/sky130hd/lef/sky130_fd_sc_hd.tlef
read_lef /work/lef.lef
set db [ord::get_db]
foreach lib [$db getLibs] {
  set master [$lib findMaster $::env(AEDA_MASTER)]
  if {$master eq "NULL"} { continue }
  set scale [expr {double([$lib getDbUnitsPerMicron])}]
  set width [expr {[$master getWidth]/$scale}]
  set height [expr {[$master getHeight]/$scale}]
  puts "AEDA_MASTER\t[$master getName]\t$width\t$height"
  foreach pin [$master getMTerms] {
    puts "AEDA_PIN\t[$pin getName]\t[$pin getIoType]\t[$pin getSigType]"
  }
}
"""
GDS_READER = """layout = RBA::Layout::new
layout.read('/work/gds.gds')
layout.top_cells.each do |cell|
  box = cell.bbox.to_dtype(layout.dbu)
  puts ['AEDA_GDS', cell.name, box.width, box.height, cell.child_instances].join("\\t")
end
"""


@dataclass(frozen=True)
class Macro:
    master: str
    kind: str
    implementation: str
    width_bits: int
    depth_words: int
    clock: str
    supplies: dict[str, str]
    orientations: list[str]
    views: dict[str, Path]
    target_corner: str


def load_macro(path: Path) -> Macro:
    root = path.resolve().parent
    data = tomllib.loads(path.read_text(encoding="utf-8"))
    expected = {
        "schema_version",
        "master",
        "kind",
        "implementation",
        "width_bits",
        "depth_words",
        "clock",
        "supplies",
        "orientations",
        "views",
        "target_corner",
    }
    if (
        set(data) != expected
        or type(data["schema_version"]) is not int
        or data["schema_version"] != 1
    ):
        raise ValueError("missing or unsupported macro manifest fields")
    master = identifier(data["master"], "master")
    kind = identifier(data["kind"], "kind")
    implementation = identifier(data["implementation"], "implementation")
    if kind not in {"sram", "rom", "register_file", "hard_macro"}:
        raise ValueError("unsupported macro kind")
    if implementation not in IMPLEMENTATIONS:
        raise ValueError("declare an explicit supported implementation policy")
    declared = data["views"]
    if not isinstance(declared, dict) or set(declared) - set(VIEW_SUFFIXES):
        raise ValueError("unsupported macro view declarations")
    views = {}
    for name, value in declared.items():
        candidate = root / relative_path(value, name, VIEW_SUFFIXES[name])
        if not candidate.resolve().is_relative_to(root):
            raise ValueError(f"macro view escapes the manifest directory: {name}")
        views[name] = candidate
    supplies = data["supplies"]
    if not isinstance(supplies, dict) or any(
        v not in ("POWER", "GROUND") for v in supplies.values()
    ):
        raise ValueError("supplies must map names to POWER or GROUND")
    orientations = data["orientations"]
    if (
        not isinstance(orientations, list)
        or not orientations
        or any(
            item not in ("R0", "R90", "R180", "R270", "MX", "MY", "MXR90", "MYR90")
            for item in orientations
        )
    ):
        raise ValueError("declare the allowed orientations explicitly")
    return Macro(
        master,
        kind,
        implementation,
        integer(data["width_bits"], "width_bits", 1048576),
        integer(data["depth_words"], "depth_words", 2**32),
        identifier(data["clock"], "clock"),
        supplies,
        orientations,
        views,
        identifier(data["target_corner"], "target_corner"),
    )


def logical_pins(module: dict[str, Any]) -> dict[str, str]:
    pins = {}
    for name, port in module.get("ports", {}).items():
        width = len(port["bits"])
        offset = port.get("offset", 0)
        for bit in range(offset, offset + width):
            pin = f"{name}[{bit}]" if width != 1 or offset else name
            pins[pin] = port["direction"].upper()
    return pins


def decode_records(path: Path, record: str) -> list[list[str]]:
    return [
        line.split("\t")[1:]
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.startswith(record + "\t")
    ]


def inspect_macro(manifest: Path, output: Path) -> dict[str, object]:
    manifest_sha = sha256(manifest)
    macro = load_macro(manifest)
    output = output.resolve()
    docker = preflight()
    output.mkdir(parents=True, exist_ok=False)
    container = f"aeda-{uuid.uuid4().hex}"
    base = container_command(docker, output, container, 1, 900)
    states: dict[str, Any] = {}
    for name, suffixes in VIEW_SUFFIXES.items():
        source = macro.views.get(name)
        if source is None or not source.is_file():
            states[name] = {"status": "MISSING"}
            continue
        path = output / (name + suffixes[0])
        digest = sha256(source)
        if name == "verilog":
            path = output / ("verilog" + source.suffix)
        shutil.copyfile(source, path)
        if sha256(path) != digest:
            raise ValueError(f"macro view changed during staging: {name}")
        states[name] = {"status": "AVAILABLE", "filename": path.name, "sha256": digest}
    (output / "macro-lef.tcl").write_bytes(LEF_READER.encode())
    (output / "macro-gds.rb").write_bytes(GDS_READER.encode())
    selections = {
        "verilog": [
            f"{FLOW}/../tools/install/yosys/bin/yosys",
            "-Q",
            "-T",
            "-p",
            f"read_verilog -lib -sv /work/{states['verilog'].get('filename', 'verilog.v')}; "
            f"hierarchy -check -top {macro.master}; write_json /work/logical.json",
        ],
        "lef": [
            f"{FLOW}/../tools/install/OpenROAD/bin/openroad",
            "-no_init",
            "-exit",
            "/work/macro-lef.tcl",
        ],
        "gds": ["klayout", "-b", "-r", "/work/macro-gds.rb"],
    }
    for name, arguments in selections.items():
        if states[name]["status"] != "AVAILABLE":
            continue
        log = output / (name + ".log")
        index = base.index("--workdir")
        command = [*base[:index], "--env", f"AEDA_MASTER={macro.master}", *base[index:], *arguments]
        code, duration = execute(command, 75, log, container)
        states[name].update(
            log=log.name, log_sha256=sha256(log), exit_code=code, runtime_seconds=duration
        )
        if code:
            states[name]["status"] = "BLOCKED"

    logical = None
    if states["verilog"]["status"] == "AVAILABLE":
        modules = json.loads((output / "logical.json").read_bytes())["modules"]
        if macro.master in modules:
            logical = logical_pins(modules[macro.master])
            states["verilog"].update(status="VERIFIED", pins=logical)
    lef: dict[str, Any] | None = None
    if states["lef"]["status"] == "AVAILABLE":
        masters = decode_records(output / "lef.log", "AEDA_MASTER")
        if len(masters) == 1 and masters[0][0] == macro.master:
            master_record = masters[0]
            lef = {
                "size_um": [float(master_record[1]), float(master_record[2])],
                "pins": {
                    name: {"direction": direction, "use": use}
                    for name, direction, use in decode_records(output / "lef.log", "AEDA_PIN")
                },
            }
            states["lef"].update(status="VERIFIED", **lef)
    gds: dict[str, Any] | None = None
    if states["gds"]["status"] == "AVAILABLE":
        cells = decode_records(output / "gds.log", "AEDA_GDS")
        if len(cells) == 1 and cells[0][0] == macro.master:
            cell_record = cells[0]
            gds = {
                "size_um": [float(cell_record[1]), float(cell_record[2])],
                "children": int(cell_record[3]),
            }
            states["gds"].update(status="VERIFIED", **gds)
    physical_pins = lef["pins"] if lef else {}
    checks = {}
    if logical is not None and lef is not None:
        signals = {
            name: item["direction"]
            for name, item in physical_pins.items()
            if item["use"] not in ("POWER", "GROUND")
        }
        power = {
            name: item["use"]
            for name, item in physical_pins.items()
            if item["use"] in ("POWER", "GROUND")
        }
        checks["signal_names_and_directions"] = signals == logical
        checks["supply_pins"] = power == macro.supplies
        checks["clock_port"] = logical.get(macro.clock) == "INPUT"
    if lef and gds:
        checks["lef_gds_extent"] = all(
            abs(a - b) <= 0.001 for a, b in zip(lef["size_um"], gds["size_um"])
        )
    for name in selections:
        if states[name]["status"] == "AVAILABLE":
            states[name].update(status="BLOCKED", reason="Expected master did not match")
    liberty = states["liberty"]
    reason = (
        f"Missing {macro.target_corner} Liberty for {macro.master}"
        if liberty["status"] == "MISSING"
        else "Liberty pin and corner checks are unavailable"
    )
    if sha256(manifest) != manifest_sha:
        raise ValueError("macro manifest changed during inspection")
    out: dict[str, object] = {
        "schema_version": 1,
        "macro": macro.master,
        "kind": macro.kind,
        "implementation": macro.implementation,
        "width_bits": macro.width_bits,
        "depth_words": macro.depth_words,
        "clock": macro.clock,
        "supplies": macro.supplies,
        "orientations": macro.orientations,
        "target_corner": macro.target_corner,
        "tool_image": IMAGE,
        "manifest_sha256": manifest_sha,
        "views": states,
        "checks": checks,
        "qualification": "BLOCKED",
        "reason": reason,
        "unverified": [
            "Liberty timing and corner",
            "GDS pin identity",
            "LVS",
            "orientation legality",
        ],
    }
    atomic(output / "macro.json", out)
    return out

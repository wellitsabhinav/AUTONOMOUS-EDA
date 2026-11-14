"""Export hash-bound checkpoint PNGs and inspectable geometry into a fresh bundle."""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import struct
import subprocess
import sys
import tempfile
import zlib
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any
from uuid import uuid4

from .diagnostic_artifacts import STAGE_REPORTS
from .evidence import get_experiment
from .layout_render import LAYER_MAP
from .manifest import sha256
from .orfs import preflight, remove_interrupted_container
from .reference import IMAGE, ORFS_ROOT
from .stage_renderer import KLAYOUT, OPENROAD
from .timing import parse_timing_report

CHECKPOINTS = (
    ("floorplan", "2_floorplan.odb"),
    ("place", "3_place.odb"),
    ("cts", "4_cts.odb"),
    ("grt", "5_1_grt.odb"),
    ("route", "5_route.odb"),
    ("finish", "6_final.odb"),
    ("gds", "6_final.gds"),
)
FORMATS = {".odb", ".def", ".gds"}
OPENROAD_BIN = f"{ORFS_ROOT}/tools/install/OpenROAD/bin/openroad"
PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"
PNG_KEY = b"autonomous-eda.provenance"


@dataclass(frozen=True)
class Source:
    stage: str
    path: str
    sha256: str
    evidence: str
    attempt: str | None = None
    timing: tuple[str, str] | None = None


def write_json(path: Path, value: object) -> None:
    path.write_text(
        json.dumps(value, ensure_ascii=True, sort_keys=True, separators=(",", ":"), allow_nan=False)
        + "\n",
        encoding="utf-8",
        newline="\n",
    )


def png_with_provenance(png: bytes, value: object) -> bytes:
    if not png.startswith(PNG_SIGNATURE) or png[12:16] != b"IHDR" or len(png) < 45:
        raise ValueError("Renderer did not return a PNG")
    payload = PNG_KEY + b"\0" + json.dumps(value, sort_keys=True, ensure_ascii=True).encode("ascii")
    chunk = b"tEXt" + payload
    return (
        png[:33]
        + struct.pack(">I", len(payload))
        + chunk
        + struct.pack(">I", zlib.crc32(chunk))
        + png[33:]
    )


def local_input(root: Path, name: str) -> Path:
    path = (root / name).resolve(strict=True)
    if Path(name).is_absolute() or not path.is_relative_to(root) or not path.is_file():
        raise ValueError(f"Input must be a file inside the selected root: {name}")
    return path


def parse_geometry(path: Path) -> dict[str, Any]:
    if path.stat().st_size > 120 * 1024**2:
        raise ValueError("Geometry exceeds 120 MiB")
    geometry: dict[str, Any] = {
        "cells": [],
        "nets": [],
        "terminals": [],
        "ports": [],
        "pins": [],
        "guides": [],
        "wires": [],
        "congestion": [],
        "polygons": [],
        "limitations": [],
    }
    with path.open(encoding="ascii") as stream:
        for line in stream:
            kind, *fields = [
                bytes.fromhex(value).decode("utf-8") for value in line.rstrip("\n").split("\t")
            ]
            if kind == "design":
                top, dbu, *box = fields
                geometry.update(top=top, dbu=float(dbu), bounds=list(map(int, box)))
            elif kind == "polygon":
                layer, *rings = fields
                geometry["polygons"].append(
                    [
                        layer,
                        [[list(map(int, p.split(","))) for p in ring.split()] for ring in rings],
                    ]
                )
            elif kind == "limitation":
                geometry["limitations"].extend(fields)
            else:
                names = {"terminal": "terminals", "congestion": "congestion"}
                has_box = kind in {"cell", "pin", "guide", "congestion", "wire"}
                key = names.get(kind, kind + "s")
                if key not in geometry:
                    raise ValueError(f"Unknown geometry record: {kind}")
                geometry[key].append([*fields[:-4], *map(int, fields[-4:])] if has_box else fields)
    if geometry.get("dbu", 0) <= 0 or len(geometry.get("bounds", [])) != 4:
        raise ValueError("Missing coordinate frame in geometry")
    for value in geometry.values():
        if isinstance(value, list) and value and isinstance(value[0], list):
            value.sort()
    return geometry


def timing_paths(root: Path, source: Source, geometry: dict[str, Any]) -> list[dict[str, Any]]:
    if source.timing is None:
        return []
    name, digest = source.timing
    try:
        with local_input(root, name).open("rb") as stream:
            raw = stream.read(4 * 1024**2 + 1)
    except FileNotFoundError:
        return []
    if len(raw) > 4 * 1024**2 or hashlib.sha256(raw).hexdigest() != digest:
        return []
    points = {
        f"{row[1]}/{row[2]}": [float(row[5]), float(row[6])]
        for row in geometry["terminals"]
        if len(row) == 7
    }
    for port, _, _, x1, y1, x2, y2 in geometry["pins"]:
        points.setdefault(port, [(x1 + x2) / 2, (y1 + y2) / 2])
    paths = []
    for path in parse_timing_report(raw, default_unit="ns").paths:
        if path.status != "VALID" or path.slack is None:
            continue
        anchors = [
            {"name": row.name, "point": points.get(row.name)}
            for row in path.launch
            if row.kind == "pin"
        ]
        if sum(anchor["point"] is not None for anchor in anchors) >= 2:
            paths.append(
                {
                    "check": path.check_type,
                    "slack_ns": path.slack.ns,
                    "report": name,
                    "sha256": digest,
                    "lines": [path.line_start, path.line_end],
                    "anchors": anchors,
                }
            )
    return paths


def render(
    docker: str,
    root: Path,
    out: Path,
    source: Source,
    run_id: str,
    top: str,
    platform: str,
    lefs: list[str],
    width: int,
) -> dict[str, Any]:
    if sys.platform != "linux":
        raise ValueError("Stage rendering requires Linux or WSL2")
    suffix = Path(source.path).suffix.lower()
    script = KLAYOUT if suffix == ".gds" else OPENROAD
    script_name = "render.rb" if suffix == ".gds" else "render.tcl"
    layer_map = LAYER_MAP if platform == "sky130hd" else ""
    args = (
        [
            "klayout",
            "-zz",
            "-r",
            "/work/render.rb",
            "-rd",
            f"top={top}",
            "-rd",
            f"layer_map={layer_map}",
            "-rd",
            f"width={width}",
        ]
        if suffix == ".gds"
        else [OPENROAD_BIN, "-no_init", "-threads", "1", "-gui", "-exit", "/work/render.tcl"]
    )
    environment = {
        "QT_QPA_PLATFORM": "offscreen",
        "AEDA_FORMAT": suffix[1:],
        "AEDA_WIDTH": str(width),
    }
    log = out / f"{source.stage}.log"
    with tempfile.TemporaryDirectory(prefix="aeda-stage-") as temporary:
        work = Path(temporary).resolve()
        inputs = work / "inputs"
        inputs.mkdir()
        local = local_input(root, source.path)
        staged = inputs / f"source{suffix}"
        shutil.copyfile(local, staged)
        if sha256(staged) != source.sha256:
            raise ValueError(f"Source changed before rendering: {source.path}")
        collateral = []
        if suffix == ".def":
            if not lefs:
                raise ValueError("DEF requires ordered --lef inputs (technology LEF first)")
            for number, lef in enumerate(lefs):
                path = local_input(root, lef)
                dest = inputs / f"{number:04}.lef"
                shutil.copyfile(path, dest)
                digest = sha256(dest)
                if sha256(path) != digest:
                    raise ValueError(f"LEF changed during staging: {lef}")
                collateral.append({"path": lef, "sha256": digest, "staged_as": dest.name})
        (work / script_name).write_text(script, encoding="utf-8", newline="\n")
        name = f"aeda-stage-{uuid4().hex}"
        command = [
            docker,
            "run",
            "--rm",
            "--pull=never",
            "--network=none",
            "--name",
            name,
            "--memory",
            "2048m",
            "--cpus",
            "1",
            "--user",
            f"{os.getuid()}:{os.getgid()}",
            *[arg for key, value in environment.items() for arg in ("-e", f"{key}={value}")],
            "--mount",
            f"type=bind,source={work},target=/work",
            "--mount",
            f"type=bind,source={inputs},target=/inputs,readonly",
            "--entrypoint",
            args[0],
            IMAGE,
            *args[1:],
        ]
        try:
            with log.open("wb") as stream:
                result = subprocess.run(
                    command, stdout=stream, stderr=subprocess.STDOUT, check=False, timeout=180
                )
        except (subprocess.TimeoutExpired, KeyboardInterrupt):
            remove_interrupted_container(docker, name)
            raise
        lines = log.read_text(encoding="utf-8", errors="replace").splitlines()
        if result.returncode or not any(line.startswith("AEDA_STAGE_OK") for line in lines):
            raise ValueError(f"Renderer exited {result.returncode}; see {log.name}")
        version = next(
            (
                line.removeprefix("AEDA_STAGE_OK ").strip()
                for line in lines
                if line.startswith("AEDA_STAGE_OK KLayout ")
            ),
            next((line.strip() for line in lines if line.startswith("OpenROAD ")), ""),
        )
        if not version:
            raise ValueError(f"Missing renderer version in {log.name}")
        geometry = parse_geometry(work / "geometry.tsv")
        if top and geometry["top"] != top:
            raise ValueError(f"Expected design top {top!r}, found {geometry['top']!r}")
        if sha256(local_input(root, source.path)) != source.sha256:
            raise ValueError(f"Source changed during rendering: {source.path}")
        geometry["timing"] = timing_paths(root, source, geometry)
        metadata = {
            "schema_version": 1,
            "run_id": run_id,
            "source": asdict(source),
            "tool": "KLayout" if suffix == ".gds" else "OpenROAD",
            "tool_version": version,
            "container_image": IMAGE,
            "generation_command": args,
            "environment": environment,
            "script_sha256": hashlib.sha256(script.encode()).hexdigest(),
            "collateral": collateral,
            "top": geometry["top"],
        }
        png = out / f"{source.stage}.png"
        data = out / f"{source.stage}.json"
        png.write_bytes(png_with_provenance((work / "layout.png").read_bytes(), metadata))
        write_json(data, geometry)
        return {
            "status": "rendered",
            "png": png.name,
            "png_sha256": sha256(png),
            "geometry": data.name,
            "geometry_sha256": sha256(data),
            "log": log.name,
            "provenance": metadata,
        }


def export(
    root: Path,
    output: Path,
    *,
    selections: list[tuple[str, str]],
    run_id: str | None,
    lefs: list[str],
    width: int,
    top: str = "",
) -> dict[str, Any]:
    root = root.resolve(strict=True)
    if not 128 <= width <= 2048:
        raise ValueError("PNG width must be between 128 and 2048")
    if not root.is_dir() or output.resolve().is_relative_to(root):
        raise ValueError("Write the bundle outside the input root")
    stages: dict[str, Any] = {}
    sources = []
    platform = ""
    if selections:
        if not run_id or not re.fullmatch(r"[A-Za-z0-9_.-]{1,128}", run_id):
            raise ValueError("Explicit checkpoints require --run-id")
        for stage, name in selections:
            if not re.fullmatch(r"[a-z][a-z0-9_-]{0,31}", stage) or stage in stages:
                raise ValueError(f"Invalid or duplicate stage: {stage}")
            if Path(name).suffix.lower() not in FORMATS:
                raise ValueError(f"Unsupported checkpoint format: {name}")
            stages[stage] = {"status": "missing", "source": name}
            try:
                digest = sha256(local_input(root, name))
            except FileNotFoundError:
                continue
            sources.append(Source(stage, name, digest, "operator-selected"))
    else:
        if run_id or lefs:
            raise ValueError("--run-id and --lef apply only to explicit --checkpoint inputs")
        experiment = get_experiment(root)
        run_id = experiment["run_id"]
        platform = experiment["platform"]
        top = top or str(experiment["input_manifest"].get("top") or "")
        attempts = {item["stage"]: item for item in experiment["attempts"]}
        timing_names = {item.stage: item.timing for item in STAGE_REPORTS}
        for stage, basename in CHECKPOINTS:
            artifact_stage = "finish" if stage == "gds" else stage
            # The run inventory records which checkpoint belongs to each attempt.
            records = [
                (name, artifact)
                for name, artifact in experiment["artifacts"].items()
                if Path(name).name == basename and artifact["stage"] == artifact_stage
            ]
            stages[stage] = {"status": "missing", "source": basename}
            if len(records) == 1:
                name, record = records[0]
                expected = record["sha256"]
                stages[stage] = {"status": record["availability"].lower(), "source": name}
                if expected and record["availability"] == "VERIFIED":
                    latest = attempts.get(artifact_stage)
                    if latest and (
                        latest["state"] != "passed" or str(latest["id"]) != record["attempt"]
                    ):
                        stages[stage]["status"] = "superseded"
                        continue
                    timing = None
                    if report_name := timing_names.get(stage):
                        key = (
                            Path("reports") / Path(name).parent.relative_to("results") / report_name
                        ).as_posix()
                        report = experiment["artifacts"].get(key)
                        if report and report["sha256"] and report["availability"] == "VERIFIED":
                            if report["attempt"] == record["attempt"]:
                                timing = key, report["sha256"]
                    sources.append(
                        Source(stage, name, expected, "recorded", record["attempt"], timing)
                    )
            elif records:
                stages[stage]["status"] = "ambiguous"
    docker = preflight() if sources else ""
    output.mkdir(parents=True, exist_ok=False)
    assert run_id is not None
    for source in sources:
        try:
            stages[source.stage] = render(
                docker, root, output, source, run_id, top, platform, lefs, width
            )
        except (OSError, ValueError, subprocess.TimeoutExpired) as error:
            stages[source.stage] = {
                "status": "failed",
                "source": asdict(source),
                "error": str(error),
                "log": f"{source.stage}.log"
                if (output / f"{source.stage}.log").is_file()
                else None,
            }
    bundle = {"schema_version": 1, "run_id": run_id, "order": list(stages), "stages": stages}
    write_json(output / "snapshots.json", bundle)
    return bundle

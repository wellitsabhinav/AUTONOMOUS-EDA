"""Plan and execute the pinned ORFS build in isolated Linux containers."""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import signal
import subprocess
import sys
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from .manifest import ORFSManifest
from .opentitan_physical import selected
from .reference import IMAGE, ORFS_ROOT, REVISION

FLOW = f"{ORFS_ROOT}/flow"
REFERENCE_DESIGNS = {
    ("sky130hd", "gcd"): ("gcd", 1.1),
    ("sky130hd", "ibex"): ("ibex_core", 8.0),
    ("sky130hd", "opentitan_aes"): ("aes", 8.5),
}

GDS_CHECK = """
layout = RBA::Layout::new
layout.read($gds)
top = layout.top_cells.find { |cell| cell.name == $top }
abort "Missing or empty GDS top cell #{$top}" unless top && !top.bbox.empty?
puts "Validated GDS top #{$top}, #{layout.cells} cells"
"""


@dataclass(frozen=True)
class Stage:
    name: str
    after: tuple[str, ...]
    outputs: tuple[str, ...]


def stage_plan(manifest: ORFSManifest, *, legacy: bool = False) -> tuple[Stage, ...]:
    if manifest.revision != REVISION:
        raise ValueError("this ORFS adapter requires the pinned reference image revision")
    expected = REFERENCE_DESIGNS.get((manifest.platform, manifest.design))
    if expected != (manifest.top, manifest.clock_period_ns):
        raise ValueError("the design, top, and clock must match a supported upstream reference")
    variant = f"{manifest.platform}/{manifest.design}/base"
    results = f"results/{variant}"
    logs = f"logs/{variant}"
    return (
        Stage("synth", (), (f"{results}/1_synth.odb", f"{results}/1_synth.sdc")),
        Stage(
            "floorplan", ("synth",), (f"{results}/2_floorplan.odb", f"{results}/2_floorplan.sdc")
        ),
        Stage("place", ("floorplan",), (f"{results}/3_place.odb", f"{results}/3_place.sdc")),
        Stage("cts", ("place",), (f"{results}/4_cts.odb", f"{results}/4_cts.sdc")),
        *(
            ()
            if legacy
            else (Stage("grt", ("cts",), (f"{results}/5_1_grt.odb", f"{results}/5_1_grt.sdc")),)
        ),
        Stage(
            "route",
            ("cts" if legacy else "grt",),
            (
                f"{results}/5_route.odb",
                f"{results}/5_route.sdc",
                *(() if legacy else (f"{logs}/5_1_grt.log", f"{logs}/5_2_route.json")),
            ),
        ),
        Stage(
            "finish",
            ("route",),
            (
                f"{results}/6_final.gds",
                f"{results}/6_final.odb",
                f"{results}/6_final.v",
                f"{results}/6_final.sdc",
                f"{results}/6_final.spef",
                f"{logs}/6_report.json",
                *(() if legacy else (f"{logs}/6_report.log",)),
            ),
        ),
    )


def preflight() -> str:
    docker = shutil.which("docker")
    if sys.platform != "linux" or docker is None:
        raise ValueError("the ORFS adapter requires Docker on Linux or WSL2 Ubuntu")
    result = subprocess.run(
        [docker, "image", "inspect", "--format", "{{.Os}}/{{.Architecture}}", IMAGE],
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    if result.returncode:
        raise ValueError(
            f"pinned image unavailable: {result.stderr.strip()}\nRun docker pull {IMAGE}"
        )
    if result.stdout.strip() != "linux/amd64":
        raise ValueError("pinned image inspection did not verify Linux/amd64")
    return docker


def container_command(
    docker: str,
    root: Path,
    name: str,
    cores: int,
    memory_mib: int,
    *,
    max_processes: int | None = None,
) -> list[str]:
    if sys.platform != "linux":
        raise ValueError("the ORFS adapter requires Linux")
    if any(char in str(root) for char in ',"\r\n'):
        raise ValueError("experiment path contains an unsupported Docker mount delimiter")
    return [
        docker,
        "run",
        "--rm",
        "--pull=never",
        "--network=none",
        "--name",
        name,
        "--cpus",
        str(cores),
        "--memory",
        f"{memory_mib}m",
        "--memory-swap",
        f"{memory_mib}m",
        *(["--pids-limit", str(max_processes)] if max_processes is not None else []),
        "--user",
        f"{os.getuid()}:{os.getgid()}",
        "--env",
        "HOME=/tmp",
        "--mount",
        f"type=bind,source={root},target=/work",
        "--workdir",
        FLOW,
        IMAGE,
    ]


def make_command(manifest: ORFSManifest, target: str) -> list[str]:
    config = (
        "/work/aes-input/config.mk"
        if selected(manifest.platform, manifest.design)
        else f"./{manifest.config}"
    )
    arguments = [
        "make",
        f"DESIGN_CONFIG={config}",
        "WORK_HOME=/work",
        f"NUM_CORES={manifest.cores}",
    ]
    if manifest.core_utilization is not None:
        arguments.append(f"CORE_UTILIZATION={manifest.core_utilization}")
    return [*arguments, target]


DispatchGuard = Callable[[], float]


def execute(
    command: list[str],
    timeout: int,
    log_path: Path,
    container: str,
    *,
    guard: DispatchGuard | None = None,
) -> tuple[int, float]:
    start = time.monotonic()
    with log_path.open("wb") as log:
        log.write((json.dumps(command) + "\n").encode())
        log.flush()
        try:
            if guard is None:
                result = subprocess.run(
                    command, stdout=log, stderr=subprocess.STDOUT, timeout=timeout, check=False
                )
                return result.returncode, time.monotonic() - start
            guard()
            with subprocess.Popen(command, stdout=log, stderr=subprocess.STDOUT) as process:
                try:
                    while True:
                        remaining = min(timeout - (time.monotonic() - start), guard())
                        if remaining <= 0:
                            raise subprocess.TimeoutExpired(command, timeout)
                        try:
                            return process.wait(
                                timeout=min(remaining, 0.5)
                            ), time.monotonic() - start
                        except subprocess.TimeoutExpired:
                            continue
                except BaseException:
                    process.kill()
                    raise
        except (subprocess.TimeoutExpired, KeyboardInterrupt) as error:
            subprocess.run(
                [command[0], "rm", "-f", container],
                stdout=log,
                stderr=subprocess.STDOUT,
                timeout=30,
                check=False,
            )
            if not isinstance(error, subprocess.TimeoutExpired):
                raise
            return 124, time.monotonic() - start


class DispatchStopped(KeyboardInterrupt):
    """An operator or campaign budget stopped a named container."""


def artifact_hashes(root: Path, stage: Stage) -> dict[str, str]:
    hashes = {}
    for relative in stage.outputs:
        path = root / relative
        if not path.resolve().is_relative_to(root) or not path.is_file():
            raise ValueError(f"missing or unsafe artifact: {relative}")
        if path.stat().st_size == 0:
            raise ValueError(f"empty artifact: {relative}")
        if path.suffix == ".json":
            data = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(data, dict) or not data:
                raise ValueError(f"empty or invalid metrics report: {relative}")
            if path.name == "6_report.json" and data.get("finish__flow__errors__count") != 0:
                raise ValueError(f"missing or nonzero ORFS error count: {relative}")
        with path.open("rb") as artifact:
            hashes[relative] = hashlib.file_digest(artifact, "sha256").hexdigest()
    return hashes


def preserve_invalid_artifacts(root: Path, first: str, history: Path) -> None:
    """Keep earlier checkpoints and move stale outputs outside Make's search paths."""
    prefix = {"synth": 1, "floorplan": 2, "place": 3, "cts": 4, "grt": 5, "route": 5, "finish": 6}[
        first
    ]
    for kind in ("results", "logs", "reports", "objects"):
        for path in (root / kind).glob("*/*/base/*"):
            match = re.match(r"([1-6])_", path.name)
            if first == "route" and path.name.startswith("5_1_"):
                continue
            if match is not None and int(match[1]) >= prefix:
                destination = history / path.relative_to(root)
                destination.parent.mkdir(parents=True, exist_ok=True)
                path.rename(destination)


def terminate_on_signal(signum: int, _frame: object) -> None:
    if signum == signal.SIGTERM:
        raise KeyboardInterrupt


def remove_interrupted_container(docker: str, name: str) -> None:
    if not re.fullmatch(r"aeda-[0-9a-f]{32}", name):
        raise ValueError(f"invalid experiment container name: {name}")
    result = subprocess.run(
        [
            docker,
            "container",
            "ls",
            "--all",
            "--filter",
            f"name=^/{name}$",
            "--format",
            "{{.Names}}",
        ],
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    if result.returncode:
        raise ValueError(f"cannot check interrupted container: {result.stderr.strip()}")
    if name in result.stdout.splitlines():
        cleanup = subprocess.run(
            [docker, "container", "rm", "-f", name],
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
        if cleanup.returncode:
            raise ValueError(f"cannot remove interrupted container: {cleanup.stderr.strip()}")

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
from typing import BinaryIO

from .manifest import ORFSManifest
from .opentitan_physical import selected
from .reference import IMAGE, ORFS_ROOT, REVISION
from .telemetry import (
    Observer,
    ResourceContext,
    classify,
    docker_inspect,
    owned_attempt,
    read_samples,
    receipt_path,
    samples_path,
    terminal_receipt,
    timestamp,
    update_peaks,
    watchdog_command,
    write_receipt,
)

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
    resources: ResourceContext | None = None,
) -> tuple[int, float]:
    if resources is not None:
        return execute_observed(command, timeout, log_path, container, resources, guard)
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


def execute_observed(
    command: list[str],
    timeout: int,
    log_path: Path,
    name: str,
    context: ResourceContext,
    guard: DispatchGuard | None,
) -> tuple[int, float]:
    start, started_at = time.monotonic(), timestamp()
    docker = command[0]
    create = watchdog_command(command, timeout, context.image)
    create[1] = "create"
    launch = [docker, "container", "start", "--attach", name]
    observer = Observer(docker, name, log_path, context.root)
    exit_code = delivered_signal = None
    timed_out = interrupted = created = False
    controller_signal = None
    with log_path.open("wb") as log:
        log.write((json.dumps(create) + "\n" + json.dumps(launch) + "\n").encode())
        log.flush()
        try:
            allowance = min(float(timeout), guard()) if guard is not None else float(timeout)
            if allowance <= 0:
                raise subprocess.TimeoutExpired(create, timeout)
            created = True
            result = subprocess.run(
                create,
                stdout=log,
                stderr=subprocess.STDOUT,
                timeout=min(allowance, 15),
                check=False,
            )
            exit_code = result.returncode
            if exit_code != 0:
                created = False
            if not exit_code:
                exit_code = None
                with subprocess.Popen(launch, stdout=log, stderr=subprocess.STDOUT) as process:
                    observer.thread.start()
                    try:
                        while True:
                            remaining = timeout - (time.monotonic() - start)
                            if guard is not None:
                                remaining = min(remaining, guard())
                            if remaining <= 0:
                                raise subprocess.TimeoutExpired(launch, timeout)
                            try:
                                exit_code = process.wait(timeout=min(remaining, 0.5))
                                break
                            except subprocess.TimeoutExpired:
                                continue
                    finally:
                        if process.poll() is None:
                            process.kill()
        except subprocess.TimeoutExpired:
            timed_out, exit_code = True, 124
        except DispatchStopped as error:
            interrupted = True
            timed_out = str(error) == "wall-clock budget exceeded"
            raise
        except ControllerSignal as error:
            interrupted = True
            controller_signal = error.signum
            raise
        except KeyboardInterrupt:
            interrupted = True
            raise
        finally:
            elapsed = time.monotonic() - start
            native = docker_inspect(docker, name) if created else {}
            try:
                if native.get("status") in {"running", "restarting", "paused"}:
                    killed = docker_lifecycle([docker, "container", "kill", name], log)
                    if killed:
                        delivered_signal = 9  # Docker's default kill signal is SIGKILL.
                    native = docker_inspect(docker, name) or native
                if native.get("status") in {"exited", "dead"} and not timed_out:
                    exit_code = native["exit_code"]
                if exit_code is not None and exit_code < 0:
                    delivered_signal = -exit_code
                observer.stop.set()
                if observer.thread.ident is not None:
                    observer.thread.join(timeout=0.1)
                receipt = terminal_receipt(
                    log_path,
                    context,
                    native,
                    observer,
                    started_at=started_at,
                    elapsed_seconds=elapsed,
                    exit_code=exit_code,
                    timed_out=timed_out,
                    termination_signal=delivered_signal,
                    interrupted=interrupted,
                    cleanup_succeeded=False,
                )
                receipt["controller_signal"] = controller_signal
                receipt["stage_timeout_seconds"] = timeout
                try:
                    write_receipt(receipt_path(log_path), receipt)
                except OSError as error:
                    log.write(f"Resource receipt unavailable: {error.strerror}\n".encode())
            finally:
                removed = not created or docker_lifecycle(
                    [docker, "container", "rm", "-f", name], log
                )
            receipt["cleanup_succeeded"] = removed
            try:
                write_receipt(receipt_path(log_path), receipt)
            except OSError:
                pass
            if observer.thread.is_alive():
                log.write(b"Resource observer did not join before cleanup\n")
            if not removed and native:
                raise ValueError(f"could not remove stopped container {name}")
    return exit_code if exit_code is not None else 125, elapsed


def docker_lifecycle(command: list[str], log: BinaryIO) -> bool:
    try:
        return (
            subprocess.run(
                command, stdout=log, stderr=subprocess.STDOUT, timeout=5, check=False
            ).returncode
            == 0
        )
    except (OSError, subprocess.TimeoutExpired):
        return False


class DispatchStopped(KeyboardInterrupt):
    """An operator or campaign budget stopped a named container."""


class ControllerSignal(KeyboardInterrupt):
    def __init__(self, signum: int) -> None:
        super().__init__(signum)
        self.signum = signum


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
        raise ControllerSignal(signum)


def remove_interrupted_container(docker: str, name: str) -> None:
    if not owned_attempt(name):
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


def record_interrupted_dispatch(docker: str, name: str, log: Path) -> None:
    """Record a crash-left container and its sampled counters before normal resume cleanup."""
    if not owned_attempt(name):
        raise ValueError(f"invalid experiment container name: {name}")
    if log.is_file() and not log.with_suffix(".resources-recovery.json").exists():
        native = docker_inspect(docker, name)
        peaks: dict[str, dict[str, object]] = {}
        diagnostic = None
        try:
            for row in read_samples(samples_path(log), allow_partial=True):
                peaks = update_peaks(peaks, row)
        except (OSError, ValueError):
            diagnostic = "The previous dispatch has no readable resource journal"
        code = native.get("exit_code") if native.get("status") in {"exited", "dead"} else None
        write_receipt(
            log.with_suffix(".resources-recovery.json"),
            {
                "schema_version": 1,
                "observed_at": timestamp(),
                "attempt_id": name,
                "native_state": native or None,
                "observed_maxima": peaks,
                "classification": classify(code, native, peaks, interrupted=True),
                "diagnostic": diagnostic,
            },
        )

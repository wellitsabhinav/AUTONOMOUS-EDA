"""Bounded, append-only resource observations for controller-owned Docker invocations."""

from __future__ import annotations

import json
import math
import os
import re
import shutil
import subprocess
import threading
import time
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from . import cgroup

SCHEMA_VERSION = 1
SAMPLE_INTERVAL_SECONDS = 2
MAX_SAMPLES = 8192
MAX_TREE_ENTRIES = 20000
MAX_SAMPLE_BYTES = 4096
DOCKER_INSPECT = (
    '{"id":{{json .Id}},"image_id":{{json .Image}},"pid":{{json .State.Pid}},'
    '"status":{{json .State.Status}},"exit_code":{{json .State.ExitCode}},'
    '"oom_killed":{{json .State.OOMKilled}},"started_at":{{json .State.StartedAt}},'
    '"finished_at":{{json .State.FinishedAt}},"nano_cpus":{{json .HostConfig.NanoCpus}},'
    '"memory_bytes":{{json .HostConfig.Memory}},'
    '"memory_swap_bytes":{{json .HostConfig.MemorySwap}},'
    '"pids_limit":{{json .HostConfig.PidsLimit}}}'
)


@dataclass(frozen=True)
class ResourceContext:
    root: Path
    stage: str
    run_id: str | None
    attempt_id: str
    image: str
    allocated_cpus: int
    memory_limit_bytes: int
    process_limit: int | None
    retry_count: int
    reused_checkpoints: tuple[str, ...] = ()


def timestamp() -> str:
    return datetime.now(UTC).isoformat()


def encode(value: object) -> str:
    return json.dumps(value, sort_keys=True, allow_nan=False, separators=(",", ":")) + "\n"


def receipt_path(log: Path) -> Path:
    return log.with_suffix(".resources.json")


def samples_path(log: Path) -> Path:
    return log.with_suffix(".resources.ndjson")


def write_receipt(path: Path, value: dict[str, Any]) -> None:
    temporary = path.with_suffix(".tmp")
    temporary.write_text(encode(value), encoding="utf-8", newline="\n")
    temporary.replace(path)


def docker_inspect(docker: str, name: str) -> dict[str, Any]:
    try:
        result = subprocess.run(
            [docker, "container", "inspect", "--format", DOCKER_INSPECT, name],
            capture_output=True,
            text=True,
            timeout=3,
            check=False,
        )
        if result.returncode == 0:
            data = json.loads(result.stdout)
            if isinstance(data, dict):
                return {**data, "source": "docker container inspect", "observed_at": timestamp()}
    except (OSError, ValueError, subprocess.TimeoutExpired):
        pass
    return {}


def bounded_tree_bytes(root: Path, stop: threading.Event) -> int | None:
    pending = [root]
    total = entries = 0
    try:
        while pending:
            with os.scandir(pending.pop()) as children:
                for child in children:
                    entries += 1
                    if stop.is_set() or entries > MAX_TREE_ENTRIES:
                        return None
                    try:
                        if child.is_dir(follow_symlinks=False):
                            pending.append(Path(child.path))
                        elif child.is_file(follow_symlinks=False):
                            total += child.stat(follow_symlinks=False).st_size
                    except FileNotFoundError:
                        continue
    except OSError:
        return None
    return total


class Observer:
    def __init__(self, docker: str, name: str, log: Path, root: Path) -> None:
        self.docker = docker
        self.name = name
        self.log = log
        self.root = root
        self.stop = threading.Event()
        self.peaks: dict[str, dict[str, Any]] = {}
        self.count = 0
        self.diagnostic = ""
        self.thread = threading.Thread(target=self.sample, daemon=True)

    def sample(self) -> None:
        identity: dict[str, Any] = {}
        roots: dict[str, Path] = {}
        first = time.monotonic()
        try:
            with samples_path(self.log).open("x", encoding="utf-8", newline="\n") as stream:
                while not self.stop.is_set() and self.count < MAX_SAMPLES:
                    if not roots:
                        identity = docker_inspect(self.docker, self.name) or identity
                        roots = cgroup.locate(identity.get("id", ""), identity.get("pid", 0))
                    observed_at = timestamp()
                    source = "linux cgroup v2" if "v2" in roots else "linux cgroup v1"
                    native = (
                        cgroup.sample_v2(roots["v2"]) if "v2" in roots else cgroup.sample_v1(roots)
                    )
                    values = {
                        key: {"value": value, "source": source} for key, value in native.items()
                    }
                    pid = identity.get("pid", 0)
                    filesystem = shutil.disk_usage(self.root)
                    for key, value, method in (
                        ("host_available_memory_bytes", cgroup.available_memory(), "/proc/meminfo"),
                        ("disk_capacity_bytes", filesystem.total, "statvfs experiment filesystem"),
                        ("disk_free_bytes", filesystem.free, "statvfs experiment filesystem"),
                        ("log_bytes", self.log.stat().st_size, "stat attempt log"),
                        (
                            "experiment_bytes",
                            bounded_tree_bytes(self.root, self.stop),
                            "bounded experiment tree stat",
                        ),
                        (
                            "process_rss_sum_bytes",
                            cgroup.process_rss(next(iter(roots.values()))) if roots else None,
                            "cgroup.procs and /proc/PID/status VmRSS",
                        ),
                        (
                            "temporary_bytes",
                            bounded_tree_bytes(Path(f"/proc/{pid}/root/tmp"), self.stop)
                            if roots
                            else None,
                            "bounded container /tmp tree stat",
                        ),
                    ):
                        if value is not None:
                            values[key] = {"value": value, "source": method}
                    if self.stop.is_set():
                        break
                    row = {
                        "schema_version": SCHEMA_VERSION,
                        "observed_at": observed_at,
                        "elapsed_seconds": time.monotonic() - first,
                        "container_id": identity.get("id"),
                        "measurements": values,
                    }
                    stream.write(encode(row))
                    stream.flush()
                    self.count += 1
                    self.peaks = update_peaks(self.peaks, row)
                    self.stop.wait(SAMPLE_INTERVAL_SECONDS)
        except OSError:
            self.diagnostic = "Resource sampling stopped after a filesystem error"
        if self.count == MAX_SAMPLES:
            self.diagnostic = "Resource sampling reached its retention limit"


def update_peaks(
    peaks: dict[str, dict[str, Any]], row: dict[str, Any]
) -> dict[str, dict[str, Any]]:
    latest = peaks.copy()
    for key, metric in row["measurements"].items():
        if key not in latest or metric["value"] >= latest[key]["value"]:
            latest[key] = {**metric, "observed_at": row["observed_at"]}
    return latest


def read_samples(path: Path, *, allow_partial: bool = False) -> list[dict[str, Any]]:
    """Validate a bounded journal. Crash recovery may ignore an unfinished final row."""
    samples: list[dict[str, Any]] = []
    with path.open("rb") as stream:
        while raw := stream.readline(MAX_SAMPLE_BYTES + 1):
            if len(samples) >= MAX_SAMPLES or len(raw) > MAX_SAMPLE_BYTES:
                raise ValueError("resource journal exceeds its schema bounds")
            if not raw.endswith(b"\n"):
                if allow_partial:
                    break
                raise ValueError("resource sample is incomplete")
            row = json.loads(raw)
            if (
                not isinstance(row, dict)
                or set(row)
                != {
                    "schema_version",
                    "observed_at",
                    "elapsed_seconds",
                    "container_id",
                    "measurements",
                }
                or type(row["schema_version"]) is not int
                or row["schema_version"] != SCHEMA_VERSION
            ):
                raise ValueError("unsupported resource sample schema")
            elapsed = row["elapsed_seconds"]
            if (
                type(elapsed) not in (int, float)
                or elapsed < 0
                or elapsed > 8 * 86400
                or not math.isfinite(elapsed)
                or (samples and elapsed < samples[-1]["elapsed_seconds"])
            ):
                raise ValueError("non-monotonic or invalid resource sample time")
            recorded = row["observed_at"]
            if (
                not isinstance(recorded, str)
                or datetime.fromisoformat(recorded).utcoffset() is None
            ):
                raise ValueError("resource sample needs a timezone-aware timestamp")
            container_id = row["container_id"]
            if container_id is not None and (
                not isinstance(container_id, str) or not re.fullmatch(r"[0-9a-f]{64}", container_id)
            ):
                raise ValueError("invalid resource sample container identity")
            measurements = row["measurements"]
            if not isinstance(measurements, dict) or any(
                not isinstance(metric, dict)
                or set(metric) != {"value", "source"}
                or type(metric["value"]) is not int
                or metric["value"] < 0
                or metric["value"] >= 2**64
                or not isinstance(metric["source"], str)
                or not 0 < len(metric["source"]) <= 128
                for metric in measurements.values()
            ):
                raise ValueError("invalid resource measurement")
            samples.append(row)
    return samples


def classify(
    exit_code: int | None,
    native: dict[str, Any],
    peaks: dict[str, dict[str, Any]],
    *,
    timed_out: bool = False,
    termination_signal: int | None = None,
    interrupted: bool = False,
) -> str:
    if native.get("oom_killed") is True or peaks.get("oom_kills", {}).get("value", 0) > 0:
        return "CONFIRMED_OOM"
    if timed_out:
        return "WALL_TIMEOUT"
    if termination_signal is not None:
        return "SIGNAL_TERMINATED"
    if exit_code == 0 and not interrupted:
        return "COMPLETED"
    if exit_code not in (None, 0) and peaks.get("process_limit_events", {}).get("value", 0) > 0:
        return "CPU_OR_PROCESS_LIMIT"
    if exit_code in (124, 137, None):
        if not peaks and native.get("oom_killed") is not True:
            return "RESOURCE_OBSERVATION_UNAVAILABLE"
        return "UNKNOWN_FAILURE"
    return "TOOL_FAILURE"


def watchdog_command(command: list[str], timeout: int, image: str) -> list[str]:
    """Keep the stopped container for inspection and bound execution if the controller dies."""
    image_index = command.index(image)
    options = [argument for argument in command[:image_index] if argument != "--rm"]
    arguments = command[image_index + 1 :]
    if "--entrypoint" in options:
        entrypoint = options.index("--entrypoint")
        arguments.insert(0, options[entrypoint + 1])
        del options[entrypoint : entrypoint + 2]
    return [
        *options,
        "--entrypoint",
        "/usr/bin/timeout",
        image,
        "--kill-after=5s",
        f"{timeout + 15}s",
        *arguments,
    ]


def terminal_receipt(
    log: Path,
    context: ResourceContext,
    native: dict[str, Any],
    observer: Observer,
    *,
    started_at: str,
    elapsed_seconds: float,
    exit_code: int | None,
    timed_out: bool,
    termination_signal: int | None,
    interrupted: bool,
    cleanup_succeeded: bool,
) -> dict[str, Any]:
    metadata = asdict(context)
    metadata.pop("root")
    state = classify(
        exit_code,
        native,
        observer.peaks,
        timed_out=timed_out,
        termination_signal=termination_signal,
        interrupted=interrupted,
    )
    unknown = {}
    for key, reason in (
        ("memory_kernel_peak_bytes", "Container cgroup high-water counter was unavailable"),
        ("memory_current_bytes", "No container memory sample was retained"),
        ("cpu_usage_usec", "No container CPU accounting sample was retained"),
        ("cpu_throttled_usec", "CPU throttling counter was unavailable"),
        ("oom_kills", "Cgroup OOM-kill counter was unavailable"),
        ("process_rss_sum_bytes", "Complete process RSS samples were unavailable"),
        ("temporary_bytes", "Container /tmp was inaccessible or exceeded the scan limit"),
    ):
        if key not in observer.peaks:
            unknown[key] = reason
    return {
        "schema_version": SCHEMA_VERSION,
        **metadata,
        "invocation_id": f"{context.attempt_id}/{log.stem}",
        "started_at": started_at,
        "ended_at": timestamp(),
        "elapsed_seconds": elapsed_seconds,
        "execution_source": "controller monotonic clock and configured dispatch policy",
        "swap_limit_bytes": 0,
        "native_state": native or None,
        "classification": state,
        "exit_code": exit_code,
        "timed_out": timed_out,
        "termination_signal": termination_signal,
        "interrupted": interrupted,
        "cleanup_succeeded": cleanup_succeeded,
        "sample_interval_seconds": SAMPLE_INTERVAL_SECONDS,
        "samples_retained": observer.count,
        "samples": samples_path(log).name,
        "observed_maxima": observer.peaks,
        "unknown": unknown,
        "diagnostic": observer.diagnostic or None,
    }


def owned_attempt(name: str) -> bool:
    return re.fullmatch(r"aeda-[0-9a-f]{32}", name) is not None

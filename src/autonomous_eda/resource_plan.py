"""Plan bounded retries using observed resources and verified ORFS checkpoints."""

from __future__ import annotations

import hashlib
import json
import math
import shutil
import sqlite3
import subprocess
import sys
import uuid
from contextlib import closing
from pathlib import Path
from typing import Any

from .doctor import available_cpus, memory_bytes
from .experiment import (
    check_inputs,
    checkpoint_valid,
    continue_run,
    db_version,
    execution_lock,
    now,
)
from .manifest import ORFSManifest, fingerprint, integer, load_manifest, sha256
from .orfs import preflight, stage_plan
from .resource_budget import MIB, run_guard, tree_bytes
from .telemetry import encode, receipt_path

POLICIES = ("evidence-required", "bounded-trial")


def checked_resource_receipt(
    root: Path, log: str, recorded_hashes: dict[str, str]
) -> dict[str, Any] | None:
    path = receipt_path(root / log)
    if not path.resolve().is_relative_to(root):
        return None
    relative = path.relative_to(root).as_posix()
    expected = recorded_hashes.get(relative)
    if not expected:
        return None
    try:
        with path.open("rb") as stream:
            raw = stream.read(65537)
        if len(raw) > 65536 or hashlib.sha256(raw).hexdigest() != expected:
            return None
        value = json.loads(raw)
        if (
            not isinstance(value, dict)
            or value.get("schema_version") != 1
            or value.get("attempt_id") != path.parent.name
        ):
            return None
        return value
    except (OSError, ValueError):
        return None


def observations(root: Path, db: sqlite3.Connection, stage: str) -> list[dict[str, Any]]:
    rows = db.execute(
        "SELECT id, state, runtime_seconds, artifacts, log, exit_code, detail "
        "FROM attempts WHERE stage=? ORDER BY id DESC LIMIT 32",
        (stage,),
    )
    results = []
    for attempt, state, runtime, hashes, relative, exit_code, detail in rows:
        log = root / relative
        artifacts = json.loads(hashes or "null") or {}
        if (
            not log.resolve().is_relative_to(root)
            or not log.is_file()
            or sha256(log) != artifacts.get(log.relative_to(root).as_posix())
        ):
            continue
        receipt = checked_resource_receipt(root, relative, artifacts)
        peak = (receipt or {}).get("observed_maxima", {}).get("memory_kernel_peak_bytes")
        runtime_observation = None
        if type(runtime) in (int, float) and math.isfinite(runtime) and runtime > 0:
            runtime_observation = {
                "value": runtime,
                "unit": "s",
                "source": f"run.sqlite3 attempts[{attempt}].runtime_seconds",
                "log_sha256": artifacts[log.relative_to(root).as_posix()],
            }
        results.append(
            {
                "attempt": attempt,
                "state": state,
                "exit_code": exit_code,
                "runtime": runtime_observation,
                "classification": (receipt or {}).get("classification")
                or (
                    "WALL_TIMEOUT"
                    if exit_code == 124 and "stage timeout" in (detail or "")
                    else None
                ),
                "memory_peak": (
                    {
                        **peak,
                        "unit": "B",
                        "artifact_sha256": artifacts[
                            receipt_path(log).relative_to(root).as_posix()
                        ],
                    }
                    if isinstance(peak, dict)
                    and type(peak.get("value")) is int
                    and peak["value"] > 0
                    else None
                ),
            }
        )
    return results


def disposition(
    records: list[dict[str, Any]],
    *,
    memory_limit: int,
    host_available: int | None,
    allocated_cpus: int,
    host_cpus: int | None,
    stage_seconds: int,
    free_bytes: int,
    storage_bytes: int,
    margin: float,
) -> tuple[str, list[str], int | None, float | None]:
    risks = []
    observed_peak = max(
        (row["memory_peak"]["value"] for row in records if row["memory_peak"]), default=None
    )
    longest = max((row["runtime"]["value"] for row in records if row["runtime"]), default=None)
    if host_available is not None and memory_limit * (1 + margin) > host_available:
        risks.append("The configured container limit plus host reserve exceeds available RAM")
    if host_cpus is not None and allocated_cpus > host_cpus:
        risks.append("The host exposes fewer CPUs than the pinned allocation")
    if free_bytes < storage_bytes:
        risks.append("The destination cannot accommodate the bounded retained storage budget")
    if observed_peak is not None and observed_peak * (1 + margin) > memory_limit:
        risks.append("The observed stage memory peak plus margin exceeds the container cap")
    if longest is not None and longest * (1 + margin) >= stage_seconds:
        risks.append("The retained elapsed time plus margin reaches the proposed stage limit")
    if risks:
        return "INSUFFICIENT_RESOURCES", risks, observed_peak, longest
    if host_available is None or host_cpus is None or longest is None:
        return (
            "UNKNOWN",
            ["Host allocation or verified stage elapsed time is unavailable"],
            (observed_peak),
            longest,
        )
    if not any(
        row["state"] == "passed" and row["runtime"] and row["memory_peak"] for row in records
    ):
        return (
            "RESOURCE_CONSTRAINED",
            ["Measured evidence does not yet establish a feasible completion budget"],
            observed_peak,
            longest,
        )
    return "READY", [], observed_peak, longest


def plan(
    root: Path,
    stage: str,
    *,
    stage_seconds: int,
    wall_seconds: int,
    artifact_mib: int,
    margin: float,
    policy: str,
    destination_parent: Path | None = None,
) -> dict[str, Any]:
    root = root.resolve()
    stage_seconds = integer(stage_seconds, "stage seconds", 86400)
    wall_seconds = integer(wall_seconds, "wall seconds", 7 * 86400)
    artifact_mib = integer(artifact_mib, "artifact MiB", 1048576)
    if not math.isfinite(margin) or not 0 <= margin <= 1 or policy not in POLICIES:
        raise ValueError("invalid planning margin or policy")
    if wall_seconds < stage_seconds:
        raise ValueError("wall seconds must cover the stage budget")
    manifest = load_manifest(root / "manifest.toml")
    if not isinstance(manifest, ORFSManifest):
        raise ValueError("resource planning requires an ORFS manifest")
    stages = stage_plan(manifest)
    if stage not in {item.name for item in stages}:
        raise ValueError(f"unknown implementation stage: {stage}")
    database = root / "run.sqlite3"
    with closing(sqlite3.connect(f"{database.as_uri()}?mode=ro", uri=True)) as db:
        if db_version(db) != 2:
            raise ValueError("resource planning requires a controller schema 2 run")
        memory_limit = check_inputs(root, db, manifest) * MIB
        run_id, image, configuration = db.execute(
            "SELECT run_id, image, configuration_sha256 FROM run"
        ).fetchone()
        checkpoints = {}
        for item in stages:
            if item.name == stage:
                break
            row = db.execute(
                "SELECT id, state, artifacts FROM attempts WHERE stage=? ORDER BY id DESC LIMIT 1",
                (item.name,),
            ).fetchone()
            if not row or row[1] != "passed":
                raise ValueError(f"missing successful upstream checkpoint: {item.name}")
            hashes = json.loads(row[2])
            if not checkpoint_valid(root, item, hashes):
                raise ValueError(f"upstream checkpoint failed hash validation: {item.name}")
            checkpoints[item.name] = {
                "attempt_id": row[0],
                "artifacts": {relative: hashes[relative] for relative in item.outputs},
            }
        records = observations(root, db, stage)
    total: int | None
    available: int | None
    try:
        total, available = memory_bytes()
    except (OSError, ValueError):
        total = available = None
    cpus = available_cpus()
    cloned_bytes = tree_bytes(root)
    cap = artifact_mib * MIB
    free = shutil.disk_usage(destination_parent or root).free
    verdict, reasons, peak, duration = disposition(
        records,
        memory_limit=memory_limit,
        host_available=available,
        allocated_cpus=manifest.cores,
        host_cpus=cpus,
        stage_seconds=stage_seconds,
        free_bytes=free,
        storage_bytes=cap,
        margin=margin,
    )
    if cloned_bytes * (1 + margin) >= cap:
        verdict = "INSUFFICIENT_RESOURCES"
        reasons.append("The copied experiment and planning margin reach the artifact cap")
    can_dispatch = verdict == "READY" or (
        verdict == "RESOURCE_CONSTRAINED" and policy == "bounded-trial"
    )
    result = {
        "schema_version": 1,
        "observed_at": now(),
        "source_run_id": run_id,
        "configuration_sha256": configuration,
        "manifest_sha256": sha256(root / "manifest.toml"),
        "image": image,
        "stage": stage,
        "policy": policy,
        "verdict": verdict,
        "execution_allowed": can_dispatch,
        "reasons": reasons,
        "margin_fraction": margin,
        "budget": {
            "stage_seconds": stage_seconds,
            "wall_seconds": wall_seconds,
            "artifact_bytes": cap,
            "memory_bytes": memory_limit,
            "swap_bytes": 0,
            "cpu_cores": manifest.cores,
            "source": "retained run configuration and explicit invocation limits",
        },
        "host": {
            "available_memory_bytes": available,
            "total_memory_bytes": total,
            "cpu_cores": cpus,
            "filesystem_free_bytes": free,
            "source": "host memory API, CPU affinity and destination filesystem stat",
        },
        "retained_source_bytes": {"value": cloned_bytes, "source": "retained experiment tree stat"},
        "observed_peak_bytes": peak,
        "observed_elapsed_seconds": duration,
        "observed_elapsed_headroom_seconds": stage_seconds - duration if duration else None,
        "attempts": records,
        "checkpoints": checkpoints,
        "uncertainty": (
            "Observed maxima and elapsed times from incomplete runs are lower bounds. "
            "A sampled kernel peak covers execution through its sample time."
        ),
    }
    return {**result, "plan_sha256": fingerprint(result)}


def copy_regular(source: str, destination: str) -> str:
    path = Path(source)
    if path.is_symlink() or not path.is_file():
        raise ValueError("checkpoint replica contains an unsafe file")
    return shutil.copy2(source, destination)


def retry(
    source: Path,
    destination: Path,
    stage: str,
    *,
    stage_seconds: int,
    wall_seconds: int,
    artifact_mib: int,
    margin: float,
    policy: str,
) -> tuple[dict[str, Any], bool]:
    source, destination = source.resolve(), destination.resolve()
    if stage != "route":
        raise ValueError("isolated retries currently support detailed route")
    if destination.exists() or destination.is_relative_to(source):
        raise ValueError("the retry needs a new experiment directory outside the source")
    if sys.platform != "linux":
        raise ValueError("isolated ORFS retries require Linux or WSL2")
    docker = preflight()
    cgroup = subprocess.run(
        [docker, "info", "--format", "{{.CgroupVersion}}"],
        capture_output=True,
        text=True,
        timeout=10,
        check=False,
    )
    if cgroup.returncode or cgroup.stdout.strip() not in {"1", "2"}:
        raise ValueError("the Docker daemon did not confirm Linux cgroup accounting support")
    with execution_lock(source):
        prepared = plan(
            source,
            stage,
            stage_seconds=stage_seconds,
            wall_seconds=wall_seconds,
            artifact_mib=artifact_mib,
            margin=margin,
            policy=policy,
            destination_parent=destination.parent,
        )
        if not prepared["execution_allowed"]:
            return prepared, False
        with closing(
            sqlite3.connect(f"{(source / 'run.sqlite3').as_uri()}?mode=ro", uri=True)
        ) as db:
            if db.execute("SELECT 1 FROM attempts WHERE state='running'").fetchone():
                raise ValueError("source has an interrupted or active dispatch")
            last = db.execute(
                "SELECT state FROM attempts WHERE stage=? ORDER BY id DESC LIMIT 1", (stage,)
            ).fetchone()
            if last is None or last[0] not in {"failed", "interrupted"}:
                raise ValueError("retry source must end in a failed or interrupted target stage")
            attempts = db.execute(
                "SELECT COUNT(*) FROM attempts WHERE stage=?", (stage,)
            ).fetchone()[0]
        for path in source.rglob("*"):
            if path.is_symlink():
                raise ValueError("checkpoint replica contains a symbolic link")
        shutil.copytree(source, destination, symlinks=True, copy_function=copy_regular)
    run_id = uuid.uuid4().hex
    with closing(sqlite3.connect(destination / "run.sqlite3")) as db, db:
        db.execute("UPDATE run SET run_id=?, created_at=?", (run_id, now()))
    (destination / "resource-plan.json").write_text(
        encode({"run_id": run_id, "plan": prepared}), encoding="utf-8"
    )
    guard = run_guard(destination, wall_seconds=wall_seconds, artifact_mib=artifact_mib)
    return prepared, continue_run(
        destination,
        guard=guard,
        max_retries=attempts,
        stage_timeouts={stage: stage_seconds},
        must_reuse=frozenset(prepared["checkpoints"]),
    )

"""Read bounded ORFS attempt telemetry and bind structured reports to retained evidence."""

from __future__ import annotations

import hashlib
import json
import math
import re
import sqlite3
from collections.abc import Callable
from contextlib import closing
from datetime import datetime
from pathlib import Path, PurePosixPath
from typing import Any

from . import telemetry
from .evidence import get_experiment
from .experiment import db_version
from .manifest import fingerprint
from .qualification import qualify
from .resource_plan import checked_resource_receipt

STAGES = ("synth", "floorplan", "place", "cts", "grt", "route", "finish", "drc", "lvs")
CLASSIFICATIONS = {
    "COMPLETED",
    "WALL_TIMEOUT",
    "CPU_OR_PROCESS_LIMIT",
    "CONFIRMED_OOM",
    "SIGNAL_TERMINATED",
    "TOOL_FAILURE",
    "RESOURCE_OBSERVATION_UNAVAILABLE",
    "UNKNOWN_FAILURE",
}
METRICS = {
    "memory_current_bytes": "B",
    "memory_kernel_peak_bytes": "B",
    "swap_current_bytes": "B",
    "process_rss_sum_bytes": "B",
    "host_available_memory_bytes": "B",
    "cpu_usage_usec": "us",
    "cpu_throttled_usec": "us",
    "cpu_throttle_events": "count",
    "oom_events": "count",
    "oom_kills": "count",
    "memory_limit_events": "count",
    "process_limit_events": "count",
    "experiment_bytes": "B",
    "temporary_bytes": "B",
    "disk_capacity_bytes": "B",
    "disk_free_bytes": "B",
    "log_bytes": "B",
}
SOURCES = {
    "linux cgroup v2",
    "linux cgroup v1",
    "/proc/meminfo",
    "statvfs experiment filesystem",
    "stat attempt log",
    "bounded experiment tree stat",
    "cgroup.procs and /proc/PID/status VmRSS",
    "bounded container /tmp tree stat",
}


def safe_log(path: str, stage: str) -> bool:
    parts = PurePosixPath(path).parts
    return (
        len(parts) == 3
        and parts[0] == "attempts"
        and telemetry.owned_attempt(parts[1])
        and parts[2] == f"{stage}.log"
    )


def read_bound(root: Path, relative: str, expected: str | None, maximum: int) -> bytes | None:
    path = root / relative
    if not expected or not path.resolve().is_relative_to(root):
        return None
    try:
        with path.open("rb") as stream:
            value = stream.read(maximum + 1)
        if len(value) <= maximum and hashlib.sha256(value).hexdigest() == expected:
            return value
    except OSError:
        pass
    return None


def project_maxima(record: Any) -> dict[str, Any]:
    if not isinstance(record, dict):
        return {}
    return {
        name: {"value": metric["value"], "source": metric["source"], "unit": METRICS[name]}
        for name, metric in sorted(record.items())
        if name in METRICS
        and isinstance(metric, dict)
        and type(metric.get("value")) is int
        and 0 <= metric["value"] < 2**64
        and isinstance(metric.get("source"), str)
        and metric["source"] in SOURCES
    }


def integer_or_unknown(value: object) -> int | None:
    return value if type(value) is int and -(2**63) <= value < 2**64 else None


def recorded_time(value: object) -> str | None:
    if not isinstance(value, str) or len(value) > 40:
        return None
    try:
        if datetime.fromisoformat(value).utcoffset() is not None:
            return value
    except ValueError:
        pass
    return None


def duration_or_unknown(value: object) -> float | int | None:
    if type(value) in (int, float) and isinstance(value, (int, float)) and 0 <= value <= 8 * 86400:
        return value
    return None


def route_progress(log: bytes) -> dict[str, Any]:
    iteration = None
    last = None
    events = 0
    for index, line in enumerate(log.splitlines(), 1):
        if match := re.search(
            rb"DRT-0195\] Start (\d+)(?:st|nd|rd|th) optimization iteration", line
        ):
            iteration = int(match[1])
        if match := re.search(rb"Completing (\d+)% with (\d+) violations", line):
            events += 1
            last = {
                "iteration": iteration,
                "percent": int(match[1]),
                "interim_violations": int(match[2]),
                "line": index,
            }
    return {"last_reported_iteration": iteration, "progress_rows": events, "last_progress": last}


def retain_samples(rows: list[dict[str, Any]], limit: int) -> list[dict[str, Any]]:
    if len(rows) > limit:
        step = math.ceil(len(rows) / (limit - 1))
        rows = [*rows[:-1:step][: limit - 1], rows[-1]]
    return [
        {
            "elapsed_seconds": row["elapsed_seconds"],
            "observed_at": row["observed_at"],
            **{
                name: item["value"]
                for name, item in project_maxima(row["measurements"]).items()
                if name in {"memory_current_bytes", "memory_kernel_peak_bytes", "cpu_usage_usec"}
            },
        }
        for row in rows
    ]


def describe_invocation(
    root: Path,
    log: str,
    artifacts: dict[str, str],
    state: str,
    sample_limit: int = 0,
    *,
    live: bool = False,
    prepare: Callable[[str], object] | None = None,
    attempt: str | None = None,
    run_stage: tuple[str, str] | None = None,
) -> dict[str, Any]:
    path = Path(log)
    receipt_file = telemetry.receipt_path(path).as_posix()
    recovery_file = path.with_suffix(".resources-recovery.json").as_posix()
    journal = telemetry.samples_path(path).as_posix()
    if prepare is not None:
        for relative in (log, receipt_file, recovery_file, journal):
            if relative in artifacts and (relative != journal or sample_limit):
                prepare(relative)
    record = checked_resource_receipt(root, log, artifacts, attempt=attempt, run_stage=run_stage)
    recovered = False
    if record is None:
        record = checked_resource_receipt(
            root, log, artifacts, recovered=True, attempt=attempt, run_stage=run_stage
        )
        if record is not None:
            recovered, receipt_file = True, recovery_file
    status = "VERIFIED" if record is not None else "UNKNOWN"
    if status == "UNKNOWN" and live and state == "running":
        status = "PROVISIONAL"
    info: dict[str, Any] = {"status": status, "classification": None, "measurements": {}}
    if record is not None:
        classification = record.get("classification")
        native = record.get("native_state") or {}
        if not isinstance(native, dict):
            native = {}
        docker_oom = native.get("oom_killed")
        timed_out = record.get("timed_out")
        reused = record.get("reused_checkpoints")
        info.update(
            classification=classification
            if isinstance(classification, str) and classification in CLASSIFICATIONS
            else None,
            receipt_sha256=artifacts[receipt_file],
            measurements=project_maxima(record.get("observed_maxima")),
            sample_interval_seconds=integer_or_unknown(record.get("sample_interval_seconds")),
            configured_memory_bytes=integer_or_unknown(native.get("memory_bytes")),
            configured_cpu_nanocores=integer_or_unknown(native.get("nano_cpus")),
            native_exit_code=integer_or_unknown(native.get("exit_code")),
            docker_oom_killed=docker_oom if type(docker_oom) is bool else None,
            termination_signal=integer_or_unknown(record.get("termination_signal")),
            stage_timeout_seconds=integer_or_unknown(record.get("stage_timeout_seconds")),
            timeout=timed_out if type(timed_out) is bool else None,
            retry_count=integer_or_unknown(record.get("retry_count")),
            elapsed_seconds=duration_or_unknown(record.get("elapsed_seconds")),
            reused_checkpoints=[
                stage for stage in STAGES if isinstance(reused, list) and stage in reused
            ],
        )
        if recovered:
            info["receipt_kind"] = "recovery"
        event = native.get("oom_event")
        if isinstance(event, dict) and event.get("source") == "docker events":
            info["docker_oom_event"] = {
                "source": "docker events",
                "time_ns": integer_or_unknown(event.get("time_ns")),
                "observed_at": recorded_time(event.get("observed_at")),
            }
    if sample_limit and (expected := artifacts.get(journal) or status == "PROVISIONAL"):
        sample_path = root / journal
        bound = (
            read_bound(root, journal, expected, 16 * 2**20) if isinstance(expected, str) else None
        )
        if status == "PROVISIONAL" or bound is not None:
            try:
                if sample_path.resolve().is_relative_to(root):
                    rows = telemetry.read_samples(
                        sample_path,
                        allow_partial=status == "PROVISIONAL" or recovered,
                        verified_bytes=bound,
                    )
                    info.update(samples=retain_samples(rows, sample_limit))
                    if status == "PROVISIONAL":
                        peaks: dict[str, dict[str, Any]] = {}
                        for row in rows:
                            peaks = telemetry.update_peaks(peaks, row)
                        info["measurements"] = project_maxima(peaks)
                    else:
                        info["samples_sha256"] = expected
            except (OSError, ValueError):
                pass
    if path.name == "route.log":
        raw = read_bound(root, log, artifacts.get(log), 4 * 2**20)
        if raw is not None:
            info["router"] = {**route_progress(raw), "log_sha256": artifacts[log]}
    return info


def small_json(
    root: Path, name: str, prepare: Callable[[str], object] | None, sha: str | None = None
) -> dict[str, Any]:
    if prepare is not None:
        prepare(name)
    path = root / name
    if not path.resolve().is_relative_to(root) or path.is_symlink():
        return {}
    try:
        with path.open("rb") as stream:
            raw = stream.read(65537)
        if len(raw) > 65536 or (sha and hashlib.sha256(raw).hexdigest() != sha):
            return {}
        value = json.loads(raw)
        return value if isinstance(value, dict) else {}
    except (OSError, ValueError):
        return {}


def verification_resources(
    root: Path,
    run_id: str | None,
    gds_sha: str | None,
    finish_artifacts: dict[str, str],
    sample_limit: int,
    prepare: Callable[[str], object] | None,
) -> list[dict[str, Any]]:
    """Read independent check dispatch receipts. Gate acceptance is evaluated separately."""
    checks: list[dict[str, Any]] = []
    if run_id is None or gds_sha is None:
        return checks
    for stage in ("drc", "lvs"):
        index = small_json(root, f"verification-{stage}.json", prepare)
        attempt = index.get("attempt")
        artifacts = index.get("artifacts")
        if (
            not isinstance(attempt, str)
            or not telemetry.owned_attempt(attempt)
            or not isinstance(artifacts, dict)
            or index.get("gds_sha256") != gds_sha
        ):
            continue
        log = f"attempts/{attempt}/{stage}.log"
        description = describe_invocation(
            root,
            log,
            artifacts,
            "unknown",
            sample_limit,
            prepare=prepare,
            run_stage=(run_id, stage),
        )
        if description["status"] == "VERIFIED":
            checks.append(
                {
                    "id": attempt,
                    "stage": stage,
                    "layout_sha256": gds_sha,
                    "exit_code": integer_or_unknown(index.get("exit_code")),
                    "dispatches": {"primary": description},
                }
            )
    latest = small_json(root, "verification/latest.json", prepare)
    run = latest.get("attempt")
    index_receipts = latest.get("receipts")
    if not isinstance(run, str) or not re.fullmatch(r"[a-f0-9]{32}", run):
        return checks
    digest = index_receipts.get("timing_coverage") if isinstance(index_receipts, dict) else None
    if not isinstance(digest, str) or not re.fullmatch(r"[a-f0-9]{64}", digest):
        return checks
    folder = f"verification/attempts/{run}/timing_coverage"
    receipt = small_json(root, f"{folder}/receipt.json", prepare, digest)
    outputs = receipt.get("outputs")
    inputs = receipt.get("inputs")
    if (
        receipt.get("gate") != "timing_coverage"
        or receipt.get("run_id") != run_id
        or not isinstance(outputs, dict)
        or not isinstance(inputs, dict)
    ):
        return checks
    for suffix in ("/6_final.odb", "/6_final.sdc", "/6_final.spef"):
        expected = [(path, sha) for path, sha in finish_artifacts.items() if path.endswith(suffix)]
        if len(expected) != 1 or inputs.get(expected[0][0]) != expected[0][1]:
            return checks
    hashes = {f"{folder}/{name}": sha for name, sha in outputs.items()}
    marker = f"{folder}/dispatch.txt"
    if prepare is not None and marker in hashes:
        prepare(marker)
    raw = read_bound(root, marker, hashes.get(marker), 40)
    if raw is not None:
        attempt = raw.decode("ascii", errors="replace")
        if telemetry.owned_attempt(attempt):
            description = describe_invocation(
                root,
                f"{folder}/coverage.stdout.log",
                hashes,
                "unknown",
                sample_limit,
                prepare=prepare,
                attempt=attempt,
                run_stage=(run_id, "timing_coverage"),
            )
            if description["status"] == "VERIFIED":
                checks.append(
                    {
                        "id": attempt,
                        "stage": "timing_coverage",
                        "verification_sha256": digest,
                        "exit_code": description["native_exit_code"],
                        "dispatches": {"primary": description},
                    }
                )
    return checks


def resource_page(
    root: Path,
    *,
    limit: int = 16,
    offset: int = 0,
    sample_limit: int = 0,
    live: bool = False,
    prepare: Callable[[str], object] | None = None,
) -> dict[str, Any]:
    if not 1 <= limit <= 32 or not 0 <= offset <= 16384:
        raise ValueError("resource page exceeds the supported range")
    if sample_limit != 0 and not 2 <= sample_limit <= 256:
        raise ValueError("sample limit must be between 2 and 256")
    root = root.resolve()
    path = (root / "run.sqlite3").resolve()
    if not path.is_relative_to(root):
        raise ValueError("run database is outside the registered directory")
    with closing(sqlite3.connect(f"{path.as_uri()}?mode=ro", uri=True)) as db:
        db.execute("PRAGMA trusted_schema=OFF")
        db.row_factory = sqlite3.Row
        version = db_version(db)
        run = db.execute("SELECT * FROM run LIMIT 2").fetchall()
        if len(run) != 1:
            raise ValueError("expected a single controller run identity")
        rows = db.execute(
            "SELECT * FROM attempts ORDER BY id DESC LIMIT ? OFFSET ?", (limit + 1, offset)
        ).fetchall()
        finish = db.execute(
            "SELECT artifacts FROM attempts WHERE stage='finish' ORDER BY id DESC LIMIT 1"
        ).fetchone()
    identity = dict(run[0])
    run_id = identity.get("run_id") if version == 2 else None
    if run_id is not None and (
        not isinstance(run_id, str) or not re.fullmatch(r"[a-f0-9]{32}", run_id)
    ):
        raise ValueError("invalid controller run identifier")
    result = []
    for row in map(dict, rows[:limit]):
        stage, state, log = row["stage"], row["state"], row["log"]
        artifacts = json.loads(row.get("artifacts") or "{}") or {}
        resource: dict[str, Any] = {"status": "UNKNOWN", "classification": None, "measurements": {}}
        valid_log = stage in STAGES and isinstance(log, str) and safe_log(log, stage)
        if valid_log:
            resource = describe_invocation(
                root, log, artifacts, state, sample_limit, live=live, prepare=prepare
            )
        dispatches = {"primary": resource}
        if stage == "finish" and valid_log:
            check = Path(log).with_name("gds-validation.log").as_posix()
            if telemetry.receipt_path(Path(check)).as_posix() in artifacts:
                dispatches["gds_reopen"] = describe_invocation(
                    root, check, artifacts, state, prepare=prepare
                )
        duration = row.get("runtime_seconds")
        attempt_log_digest = artifacts.get(log) if valid_log else None
        if attempt_log_digest and read_bound(root, log, attempt_log_digest, 4 * 2**20) is None:
            attempt_log_digest = None
        if (
            isinstance(duration, bool)
            or not isinstance(duration, (int, float))
            or not math.isfinite(duration)
            or duration < 0
        ):
            duration = None
        result.append(
            {
                "id": integer_or_unknown(row["id"]),
                "stage": stage if stage in STAGES else "unknown",
                "state": state
                if state in {"running", "passed", "failed", "interrupted"}
                else "unknown",
                "started_at": recorded_time(row.get("started_at")),
                "ended_at": recorded_time(row.get("ended_at")),
                "exit_code": integer_or_unknown(row.get("exit_code")),
                "duration_seconds": duration if attempt_log_digest else None,
                "duration_source": "controller monotonic runtime and recorded log"
                if attempt_log_digest
                else None,
                "log_sha256": attempt_log_digest,
                "dispatches": dispatches,
            }
        )
    finished = (json.loads(finish[0] or "{}") or {}) if finish else {}
    gds = [
        sha
        for path, sha in finished.items()
        if path.endswith("/6_final.gds")
        and isinstance(sha, str)
        and re.fullmatch(r"[a-f0-9]{64}", sha)
    ]
    return {
        "schema_version": 1,
        "run_id": run_id,
        "memory_limit_bytes": integer_or_unknown(identity["memory_mib"] * 2**20),
        "allocated_cpus": integer_or_unknown(json.loads(identity["manifest"]).get("cores")),
        "allocation_source": "controller run configuration",
        "next_offset": offset + limit if len(rows) > limit else None,
        "attempts": result,
        "verification_attempts": verification_resources(
            root, run_id, gds[0] if len(gds) == 1 else None, finished, sample_limit, prepare
        ),
    }


def input_approval(root: Path, run_id: str) -> list[dict[str, Any]]:
    path = root / "resource-plan.json"
    if not path.is_file() or not path.resolve().is_relative_to(root):
        return []
    try:
        with path.open("rb") as stream:
            value = json.loads(stream.read(64 * 1024))
        plan = dict(value["plan"])
        digest = plan.pop("plan_sha256")
        if digest != fingerprint(plan) or value["run_id"] != run_id:
            return []
        database = (root / "run.sqlite3").resolve()
        if not database.is_relative_to(root):
            return []
        with closing(sqlite3.connect(f"{database.as_uri()}?mode=ro", uri=True)) as db:
            db.execute("PRAGMA trusted_schema=OFF")
            if db.execute(
                "SELECT 1 FROM sqlite_schema WHERE type='table' AND name='retry_policy'"
            ).fetchone() and db.execute("SELECT plan_sha256 FROM retry_policy").fetchall() != [
                (digest,)
            ]:
                return []
        return [
            {
                "type": "explicit_retry_policy",
                "policy": plan["policy"],
                "plan_sha256": digest,
                "execution_allowed": plan["execution_allowed"],
                "budget": plan["budget"],
            }
        ]
    except (OSError, ValueError, KeyError, TypeError, sqlite3.Error):
        return []


def summary(
    root: Path, *, limit: int = 16, offset: int = 0, artifact: str | None = None
) -> dict[str, Any]:
    if not 1 <= limit <= 32 or not 0 <= offset <= 16384:
        raise ValueError("summary page exceeds the supported range")
    root = root.resolve()
    evidence = get_experiment(root)
    inventory = evidence["artifacts"]
    if artifact is not None and artifact not in inventory:
        raise ValueError("artifact is absent from the verified inventory")
    selected = sorted(inventory)[offset : offset + limit] if artifact is None else [artifact]
    resource = (
        resource_page(root, limit=limit, offset=offset)
        if (root / "run.sqlite3").is_file()
        else None
    )
    qualification = qualify(root, evidence)
    return {
        "schema_version": 1,
        "run_id": evidence["run_id"],
        "manifest": evidence["input_manifest"],
        "configuration_sha256": evidence["configuration_sha256"],
        "rtl_identity": evidence["rtl_identity"],
        "source_revision": evidence["source_revision"],
        "toolchain": evidence["toolchain"],
        "constraint_evidence": [inventory.get(path) for path in evidence["constraints"]],
        "metrics": dict(sorted(evidence["metrics"].items())[:256]),
        "qualification": qualification,
        "approval_events": input_approval(root, evidence["run_id"]),
        "resources": resource,
        "artifacts": {path: inventory[path] for path in selected},
        "next_artifact_offset": offset + limit if offset + limit < len(inventory) else None,
    }

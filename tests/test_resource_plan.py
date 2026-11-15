import json
import sqlite3
from contextlib import closing
from pathlib import Path

import pytest

from autonomous_eda.experiment import initialize
from autonomous_eda.manifest import ORFSManifest, fingerprint, load_manifest, sha256
from autonomous_eda.orfs import DispatchStopped
from autonomous_eda.resource_plan import checked_resource_receipt, disposition, plan, restore_budget
from autonomous_eda.telemetry import receipt_path


@pytest.mark.parametrize(
    ("runtime", "peak", "state", "available", "seconds", "free", "result"),
    [
        (30, 100, "passed", 1000, 60, 1000, "READY"),
        (30, None, "failed", 1000, 60, 1000, "RESOURCE_CONSTRAINED"),
        (30, 470, "failed", 1000, 60, 1000, "INSUFFICIENT_RESOURCES"),
        (30, None, "failed", 500, 60, 1000, "INSUFFICIENT_RESOURCES"),
        (30, None, "failed", 1000, 30, 1000, "INSUFFICIENT_RESOURCES"),
        (30, None, "failed", 1000, 60, 100, "INSUFFICIENT_RESOURCES"),
        (None, None, "failed", None, 60, 1000, "UNKNOWN"),
    ],
)
def test_preflight_disposition(
    runtime: int | None,
    peak: int | None,
    state: str,
    available: int | None,
    seconds: int,
    free: int,
    result: str,
) -> None:
    status, _reasons, measured_peak, measured_time = disposition(
        [
            {
                "state": state,
                "memory_peak": {"value": peak} if peak else None,
                "runtime": {"value": runtime} if runtime else None,
            }
        ],
        memory_limit=500,
        host_available=available,
        allocated_cpus=2,
        host_cpus=4,
        stage_seconds=seconds,
        free_bytes=free,
        storage_bytes=200,
        margin=0.1,
    )
    assert (status, measured_peak, measured_time) == (result, peak, runtime)


def test_bound_telemetry_and_trial_policy(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    manifest_path = Path("configs/gcd-sky130hd.toml")
    manifest = load_manifest(manifest_path)
    assert isinstance(manifest, ORFSManifest)
    root = tmp_path / "run"
    initialize(root, manifest_path, manifest, 512)
    log = root / "attempts" / ("aeda-" + "0" * 32) / "synth.log"
    log.parent.mkdir(parents=True)
    log.write_text("history")
    relative = log.relative_to(root).as_posix()
    resource = receipt_path(log)
    resource.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "attempt_id": log.parent.name,
                "classification": "COMPLETED",
                "observed_maxima": {"memory_kernel_peak_bytes": {"value": 300, "source": "cgroup"}},
            }
        )
    )
    artifacts = {relative: sha256(log), resource.relative_to(root).as_posix(): sha256(resource)}
    with closing(sqlite3.connect(root / "run.sqlite3")) as db, db:
        db.execute(
            "INSERT INTO attempts(stage,state,runtime_seconds,log,artifacts,exit_code) "
            "VALUES ('synth','passed',10,?,?,0)",
            (relative, json.dumps(artifacts)),
        )
    monkeypatch.setattr("autonomous_eda.resource_plan.memory_bytes", lambda: (2**31, 2**30))
    monkeypatch.setattr("autonomous_eda.resource_plan.available_cpus", lambda: 4)
    result = plan(
        root,
        "synth",
        stage_seconds=30,
        wall_seconds=60,
        artifact_mib=64,
        margin=0.1,
        policy="evidence-required",
    )
    assert result["verdict"] == "READY"
    assert checked_resource_receipt(root, relative, artifacts)
    assert checked_resource_receipt(root, "../untrusted.log", artifacts) is None
    resource.write_text("forged")
    assert checked_resource_receipt(root, relative, artifacts) is None
    strict = plan(
        root,
        "synth",
        stage_seconds=30,
        wall_seconds=60,
        artifact_mib=64,
        margin=0.1,
        policy="evidence-required",
    )
    bounded = plan(
        root,
        "synth",
        stage_seconds=30,
        wall_seconds=60,
        artifact_mib=64,
        margin=0.1,
        policy="bounded-trial",
    )
    assert strict["verdict"] == bounded["verdict"] == "RESOURCE_CONSTRAINED"
    assert strict["execution_allowed"] is False
    assert bounded["execution_allowed"] is True


def test_confirmed_oom_cap_is_a_lower_bound_even_with_zero_margin() -> None:
    status, reasons, *_ = disposition(
        [
            {
                "state": "failed",
                "classification": "CONFIRMED_OOM",
                "runtime": {"value": 12},
                "memory_limit": {"value": 512},
                "memory_peak": None,
            }
        ],
        memory_limit=512,
        host_available=2048,
        allocated_cpus=2,
        host_cpus=4,
        stage_seconds=40,
        free_bytes=2048,
        storage_bytes=512,
        margin=0,
    )
    assert status == "INSUFFICIENT_RESOURCES"
    assert "confirmed OOM" in reasons[0]


def test_resume_reapplies_the_bound_trial_and_rejects_rehash(tmp_path: Path) -> None:
    manifest_path = Path("configs/gcd-sky130hd.toml")
    manifest = load_manifest(manifest_path)
    assert isinstance(manifest, ORFSManifest)
    root = tmp_path / "run"
    initialize(root, manifest_path, manifest, 512)
    path = root / "resource-plan.json"
    with closing(sqlite3.connect(root / "run.sqlite3")) as db, db:
        run_id, config = db.execute("SELECT run_id,configuration_sha256 FROM run").fetchone()
        data = {
            "configuration_sha256": config,
            "execution_allowed": True,
            "policy": "bounded-trial",
            "stage": "route",
            "margin_fraction": 0.08,
            "budget": {
                "stage_seconds": 500,
                "wall_seconds": 1000,
                "artifact_bytes": 2**28,
                "memory_bytes": 512 * 2**20,
            },
            "checkpoints": dict.fromkeys(("synth", "floorplan", "place", "cts", "grt"), {}),
            "stage_attempt_counts": {"route": 2, "finish": 0},
        }
        digest = fingerprint(data)
        db.execute("CREATE TABLE retry_policy (plan_sha256 TEXT NOT NULL)")
        db.execute("INSERT INTO retry_policy VALUES (?)", (digest,))
        receipt = {"run_id": run_id, "plan": {**data, "plan_sha256": digest}}
        path.write_text(json.dumps(receipt), encoding="utf-8")
        spec = restore_budget(root, db, lambda: 2**30)
        assert spec and spec.stage_seconds == 500
        assert spec.attempt_limits == {"route": 3, "finish": 1}
        assert spec.guard() > 0
        low_ram = restore_budget(root, db, lambda: 2**28)
        assert low_ram
        with pytest.raises(DispatchStopped, match="host RAM"):
            low_ram.guard()
        db.execute("UPDATE run SET created_at='2000-01-01T00:00:00+00:00'")
        expired = restore_budget(root, db, lambda: 2**30)
        assert expired
        with pytest.raises(DispatchStopped, match="expired"):
            expired.guard()
        data["stage_attempt_counts"] = {"route": 99, "finish": 0}
        path.write_text(
            json.dumps({"run_id": run_id, "plan": {**data, "plan_sha256": fingerprint(data)}}),
            encoding="utf-8",
        )
        with pytest.raises(ValueError, match="database digest"):
            restore_budget(root, db, lambda: 2**30)
        path.unlink()
        with pytest.raises(FileNotFoundError):
            restore_budget(root, db, lambda: 2**30)

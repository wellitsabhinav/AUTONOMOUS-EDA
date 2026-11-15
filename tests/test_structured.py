import json
import sqlite3
from contextlib import closing
from pathlib import Path

import pytest

from autonomous_eda.experiment import initialize
from autonomous_eda.manifest import ORFSManifest, load_manifest, sha256
from autonomous_eda.structured import resource_page, route_progress
from autonomous_eda.telemetry import receipt_path


@pytest.fixture
def structured_run(tmp_path: Path) -> tuple[Path, Path]:
    manifest_path = Path("configs/gcd-sky130hd.toml")
    manifest = load_manifest(manifest_path)
    assert isinstance(manifest, ORFSManifest)
    root = tmp_path / "run"
    initialize(root, manifest_path, manifest, 1024)
    name = "aeda-" + "0" * 32
    log = root / "attempts" / name / "route.log"
    log.parent.mkdir(parents=True)
    log.write_text(
        "[INFO DRT-0195] Start 0th optimization iteration.\nCompleting 30% with 12 violations.\n"
    )
    receipt = receipt_path(log)
    receipt.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "attempt_id": name,
                "classification": "WALL_TIMEOUT",
                "native_state": {"oom_killed": False},
                "observed_maxima": {
                    "memory_kernel_peak_bytes": {"value": 1234, "source": "linux cgroup v2"},
                    "unsafe": {"value": 5678, "source": "secret"},
                },
            }
        )
    )
    hashes = {path.relative_to(root).as_posix(): sha256(path) for path in (log, receipt)}
    with closing(sqlite3.connect(root / "run.sqlite3")) as db, db:
        db.execute(
            "INSERT INTO attempts(stage,state,runtime_seconds,log,artifacts,exit_code) "
            "VALUES ('route','failed',10,?,?,124)",
            (log.relative_to(root).as_posix(), json.dumps(hashes)),
        )
    return root, log


def test_read_only_structured_page_and_tampering(structured_run: tuple[Path, Path]) -> None:
    root, log = structured_run
    db = root / "run.sqlite3"
    expected = sha256(db)
    page = resource_page(root)
    primary = page["attempts"][0]["dispatches"]["primary"]
    assert primary["status"] == "VERIFIED"
    assert primary["router"]["last_progress"]["interim_violations"] == 12
    assert primary["measurements"] == {
        "memory_kernel_peak_bytes": {"value": 1234, "source": "linux cgroup v2", "unit": "B"}
    }
    assert "secret" not in json.dumps(page)
    assert sha256(db) == expected
    receipt_path(log).write_text("tampered")
    log.write_text("tampered")
    attempt = resource_page(root)["attempts"][0]
    assert attempt["dispatches"]["primary"]["status"] == "UNKNOWN"
    assert attempt["duration_seconds"] is None


def test_router_progress_keeps_interim_scope() -> None:
    result = route_progress(
        b"[INFO DRT-0195] Start 1st optimization iteration.\n"
        b"Completing 100% with 87 violations.\n"
        b"[INFO DRT-0195] Start 2nd optimization iteration.\n"
    )
    assert result["last_reported_iteration"] == 2
    assert result["last_progress"] == {
        "iteration": 1,
        "percent": 100,
        "interim_violations": 87,
        "line": 2,
    }


def test_recovered_resource_receipt_is_exposed(structured_run: tuple[Path, Path]) -> None:
    root, log = structured_run
    recovery = log.with_suffix(".resources-recovery.json")
    receipt_path(log).rename(recovery)
    with closing(sqlite3.connect(root / "run.sqlite3")) as db, db:
        db.execute(
            "UPDATE attempts SET state='interrupted', artifacts=?",
            (json.dumps({recovery.relative_to(root).as_posix(): sha256(recovery)}),),
        )
    primary = resource_page(root)["attempts"][0]["dispatches"]["primary"]
    assert primary["status"] == "VERIFIED"
    assert primary["receipt_kind"] == "recovery"
    assert primary["measurements"]["memory_kernel_peak_bytes"]["value"] == 1234


def test_verification_dispatch_requires_run_layout_and_receipt(
    structured_run: tuple[Path, Path],
) -> None:
    root, log = structured_run
    gds = "1" * 64
    with closing(sqlite3.connect(root / "run.sqlite3")) as db, db:
        run_id = db.execute("SELECT run_id FROM run").fetchone()[0]
        db.execute(
            "INSERT INTO attempts(stage,state,log,artifacts) VALUES ('finish','passed',?,?)",
            ("attempts/finish.log", json.dumps({"results/sky130hd/gcd/base/6_final.gds": gds})),
        )
    receipt = receipt_path(log.with_name("drc.log"))
    receipt.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "attempt_id": log.parent.name,
                "stage": "drc",
                "run_id": run_id,
                "classification": "COMPLETED",
                "elapsed_seconds": 5,
                "native_state": {"memory_bytes": 512 * 2**20},
            }
        )
    )
    index = {
        "attempt": log.parent.name,
        "gds_sha256": gds,
        "exit_code": 0,
        "artifacts": {receipt.relative_to(root).as_posix(): sha256(receipt)},
    }
    path = root / "verification-drc.json"
    path.write_text(json.dumps(index))
    (result,) = resource_page(root)["verification_attempts"]
    assert result["stage"] == "drc"
    assert result["dispatches"]["primary"]["configured_memory_bytes"] == 512 * 2**20
    path.write_text(json.dumps({**index, "gds_sha256": "2" * 64}))
    assert resource_page(root)["verification_attempts"] == []
    path.write_text(json.dumps(index))
    receipt.write_text("tampered")
    assert resource_page(root)["verification_attempts"] == []

import json
import sqlite3
from contextlib import closing
from pathlib import Path

import pytest

from autonomous_eda.experiment import check_inputs, initialize, report, run_stages
from autonomous_eda.manifest import ORFSManifest, load_manifest
from autonomous_eda.orfs import artifact_hashes, stage_plan

REFERENCE = Path("configs/gcd-sky130hd.toml")


def test_stage_plan_and_validation(tmp_path: Path) -> None:
    manifest = load_manifest(REFERENCE)
    assert isinstance(manifest, ORFSManifest)
    stages = stage_plan(manifest)
    assert [stage.name for stage in stages] == [
        "synth",
        "floorplan",
        "place",
        "cts",
        "grt",
        "route",
        "finish",
    ]
    assert stages[-1].after == ("route",)
    with pytest.raises(ValueError, match="missing"):
        artifact_hashes(tmp_path, stages[0])
    for relative in stages[0].outputs:
        output = tmp_path / relative
        output.parent.mkdir(parents=True, exist_ok=True)
        output.touch()
    with pytest.raises(ValueError, match="empty"):
        artifact_hashes(tmp_path, stages[0])


def test_checkpoint_reuse_requires_matching_hashes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    manifest = load_manifest(REFERENCE)
    assert isinstance(manifest, ORFSManifest)
    root = tmp_path / "run"
    initialize(root, REFERENCE, manifest, 1024)
    first = stage_plan(manifest)[0]
    for relative in first.outputs:
        output = root / relative
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text("checkpoint", encoding="utf-8")
    monkeypatch.setattr("autonomous_eda.experiment.container_command", lambda *args: ["docker"])
    monkeypatch.setattr("autonomous_eda.experiment.execute", lambda *args, **kwargs: (1, 0.1))
    with closing(sqlite3.connect(root / "run.sqlite3")) as db:
        with db:
            db.execute(
                "INSERT INTO attempts(stage,state,artifacts,log) VALUES (?,?,?,?)",
                ("synth", "passed", json.dumps(artifact_hashes(root, first)), "original.log"),
            )
        assert not run_stages(root, db, manifest, "docker", 1024)
        assert [row["stage"] for row in report(root)["attempts"]] == ["synth", "floorplan"]
        (root / first.outputs[0]).write_text("damaged", encoding="utf-8")
        assert not run_stages(root, db, manifest, "docker", 1024)
        assert report(root)["attempts"][-1]["stage"] == "synth"
        assert list((root / "attempts").glob("*/previous/results/*/*/base/1_synth.odb"))


def test_interrupted_dispatch_recovers_and_changed_input_disallows_resume(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manifest = load_manifest(REFERENCE)
    assert isinstance(manifest, ORFSManifest)
    root = tmp_path / "run"
    initialize(root, REFERENCE, manifest, 1024)
    container = "aeda-" + "0" * 32
    removed = []

    def remove(_docker: str, name: str) -> None:
        removed.append(name)

    monkeypatch.setattr("autonomous_eda.experiment.remove_interrupted_container", remove)
    monkeypatch.setattr("autonomous_eda.experiment.container_command", lambda *args: ["docker"])
    monkeypatch.setattr("autonomous_eda.experiment.execute", lambda *args, **kwargs: (1, 0.1))
    with closing(sqlite3.connect(root / "run.sqlite3")) as db:
        with db:
            db.execute(
                "INSERT INTO attempts(stage,state,log) VALUES ('synth','running',?)",
                (f"attempts/{container}/synth.log",),
            )
        assert not run_stages(root, db, manifest, "docker", 1024)
        assert removed == [container]
        attempts = report(root)["attempts"]
        assert [attempt["state"] for attempt in attempts] == ["interrupted", "failed"]
        assert attempts[0]["ended_at"] and attempts[1]["command"]
        with (root / "manifest.toml").open("a") as stream:
            stream.write("\n# Different bytes\n")
        with pytest.raises(ValueError, match="immutable run input"):
            check_inputs(root, db, manifest)
        assert report(root)["input_error"]


def test_unknown_checkpoint_schema_fails_closed(tmp_path: Path) -> None:
    manifest = load_manifest(REFERENCE)
    assert isinstance(manifest, ORFSManifest)
    initialize(tmp_path / "run", REFERENCE, manifest, 1024)
    with closing(sqlite3.connect(tmp_path / "run" / "run.sqlite3")) as db:
        db.execute("PRAGMA user_version=99")
    with pytest.raises(ValueError, match="unsupported run database version"):
        report(tmp_path / "run")

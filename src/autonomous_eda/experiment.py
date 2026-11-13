"""Persist runs, attempts, stage checkpoints, and their evidence in SQLite."""

from __future__ import annotations

import json
import shutil
import signal
import sqlite3
import sys
import uuid
from collections.abc import Iterator
from contextlib import closing, contextmanager
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .doctor import available_cpus, memory_bytes
from .manifest import ORFSManifest, fingerprint, integer, load_manifest, sha256
from .metrics import summarize
from .orfs import (
    GDS_CHECK,
    Stage,
    artifact_hashes,
    container_command,
    execute,
    make_command,
    preflight,
    preserve_invalid_artifacts,
    remove_interrupted_container,
    stage_plan,
    terminate_on_signal,
)
from .reference import IMAGE


def now() -> str:
    return datetime.now(UTC).isoformat()


def verification_files(variant: str, target: str) -> tuple[str, str]:
    if target == "drc":
        return f"reports/{variant}/6_drc_count.rpt", f"reports/{variant}/6_drc.lyrdb"
    return f"logs/{variant}/6_lvs.log", f"results/{variant}/6_lvs.lvsdb"


def db_version(db: sqlite3.Connection) -> int:
    version = int(db.execute("PRAGMA user_version").fetchone()[0])
    if version not in {0, 2}:
        raise ValueError(f"unsupported run database version: {version}")
    return version


def initialize(root: Path, manifest_path: Path, manifest: ORFSManifest, memory_mib: int) -> None:
    stage_plan(manifest)
    integer(memory_mib, "memory_mib", 1048576)
    if load_manifest(manifest_path) != manifest:
        raise ValueError("manifest changed before initialization")
    root.mkdir(parents=True, exist_ok=False)
    shutil.copyfile(manifest_path, root / "manifest.toml")
    if load_manifest(root / "manifest.toml") != manifest:
        raise ValueError("manifest changed during initialization")
    (root / "validate_gds.rb").write_text(GDS_CHECK, encoding="utf-8")
    with closing(sqlite3.connect(root / "run.sqlite3")) as db, db:
        db.executescript("""
            BEGIN;
            PRAGMA user_version=2;
            CREATE TABLE run (
              manifest TEXT NOT NULL,
              image TEXT NOT NULL,
              memory_mib INTEGER NOT NULL,
              run_id TEXT NOT NULL UNIQUE,
              created_at TEXT NOT NULL,
              manifest_sha256 TEXT NOT NULL,
              configuration_sha256 TEXT NOT NULL,
              validation_sha256 TEXT NOT NULL
            );
            CREATE TABLE attempts (
              id INTEGER PRIMARY KEY,
              stage TEXT NOT NULL,
              state TEXT NOT NULL,
              runtime_seconds REAL,
              detail TEXT,
              artifacts TEXT,
              log TEXT NOT NULL,
              started_at TEXT,
              ended_at TEXT,
              command TEXT,
              exit_code INTEGER
            );
        """)
        db.execute(
            "INSERT INTO run VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (
                json.dumps(asdict(manifest)),
                IMAGE,
                memory_mib,
                uuid.uuid4().hex,
                now(),
                sha256(root / "manifest.toml"),
                fingerprint([asdict(manifest), IMAGE, memory_mib]),
                sha256(root / "validate_gds.rb"),
            ),
        )


@contextmanager
def execution_lock(root: Path) -> Iterator[None]:
    if sys.platform != "linux":
        raise ValueError("experiment execution requires Linux")
    import fcntl

    with (root / "execution.lock").open("a") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as error:
            raise ValueError("another process is already executing this experiment") from error
        previous = signal.signal(signal.SIGTERM, terminate_on_signal)
        try:
            yield
        finally:
            signal.signal(signal.SIGTERM, previous)


def check_inputs(root: Path, db: sqlite3.Connection, manifest: ORFSManifest) -> int:
    expected, image, limit = db.execute("SELECT manifest, image, memory_mib FROM run").fetchone()
    saved = json.loads(expected)
    saved.setdefault("core_utilization", None)
    if saved != asdict(manifest) or image != IMAGE:
        raise ValueError("manifest or tool image changed since initialization")
    if db_version(db) == 2:
        identity = db.execute(
            "SELECT manifest_sha256, configuration_sha256, validation_sha256 FROM run"
        ).fetchone()
        validation = root / "validate_gds.rb"
        if (
            not validation.resolve().is_relative_to(root)
            or validation.read_text(encoding="utf-8") != GDS_CHECK
        ):
            raise ValueError("invalid GDS validation script")
        if tuple(identity) != (
            sha256(root / "manifest.toml"),
            fingerprint([saved, image, limit]),
            sha256(root / "validate_gds.rb"),
        ):
            raise ValueError("immutable run input or configuration hash mismatch")
    elif (root / "validate_gds.rb").read_text(encoding="utf-8") != GDS_CHECK:
        raise ValueError("legacy GDS validation script differs from the controller's fixed script")
    return integer(limit, "memory_mib", 1048576)


def continue_run(root: Path) -> bool:
    root = root.resolve()
    if not (root / "run.sqlite3").is_file():
        raise ValueError(f"experiment database missing in {root}")
    manifest = load_manifest(root / "manifest.toml")
    if not isinstance(manifest, ORFSManifest):
        raise ValueError("physical runs require an ORFS manifest")
    cpus = available_cpus()
    if cpus is None or manifest.cores > cpus:
        raise ValueError("manifest requests more cores than this host exposes")
    with execution_lock(root), closing(sqlite3.connect(root / "run.sqlite3", timeout=1)) as db:
        memory_mib = check_inputs(root, db, manifest)
        total, _available = memory_bytes()
        if memory_mib * 2**20 > total:
            raise ValueError("configured memory exceeds the visible host limit")
        docker = preflight()
        cleanup_verification(root, docker)
        return run_stages(root, db, manifest, docker, memory_mib)


def cleanup_verification(root: Path, docker: str) -> None:
    for target in ("drc", "lvs"):
        status_file = root / f"verification-{target}.json"
        if status_file.is_file():
            existing = json.loads(status_file.read_text(encoding="utf-8"))
            if existing.get("exit_code") is None and existing.get("attempt"):
                remove_interrupted_container(docker, existing["attempt"])


def run_stages(
    root: Path, db: sqlite3.Connection, manifest: ORFSManifest, docker: str, memory_mib: int
) -> bool:
    version = db_version(db)
    for (log,) in db.execute("SELECT log FROM attempts WHERE state='running'"):
        remove_interrupted_container(docker, Path(log).parent.name)
    with db:
        if version == 2:
            db.execute(
                "UPDATE attempts SET ended_at=?, detail=? WHERE state='running'",
                (now(), "Previous dispatch did not record a terminal state"),
            )
        db.execute("UPDATE attempts SET state='interrupted' WHERE state='running'")
    dirty = False
    passed: set[str] = set()
    for stage in stage_plan(manifest, legacy=version == 0):
        if not set(stage.after).issubset(passed):
            raise ValueError(f"unmet dependency for {stage.name}")
        previous = db.execute(
            "SELECT state, artifacts FROM attempts WHERE stage=? ORDER BY id DESC LIMIT 1",
            (stage.name,),
        ).fetchone()
        if not dirty and previous and previous[0] == "passed":
            try:
                if checkpoint_valid(root, stage, json.loads(previous[1])):
                    passed.add(stage.name)
                    continue
            except (OSError, ValueError):
                # A lost or modified checkpoint invalidates dependent stages.
                pass
        name = f"aeda-{uuid.uuid4().hex}"
        attempt = root / "attempts" / name
        attempt.mkdir(parents=True)
        if not dirty:
            first = "grt" if stage.name == "route" and version == 0 else stage.name
            preserve_invalid_artifacts(root, first, attempt / "previous")
            dirty = True
        log = attempt / f"{stage.name}.log"
        command = container_command(docker, root, name, manifest.cores, memory_mib)
        commands = [command + make_command(manifest, stage.name)]
        if stage.name == "finish":
            gds = next(item for item in stage.outputs if item.endswith(".gds"))
            commands.append(
                command
                + [
                    "klayout",
                    "-b",
                    "-r",
                    "/work/validate_gds.rb",
                    "-rd",
                    f"gds=/work/{gds}",
                    "-rd",
                    f"top={manifest.top}",
                ]
            )
        if version == 2:
            check_inputs(root, db, manifest)
        with db:
            cursor = db.execute(
                "INSERT INTO attempts(stage,state,log) VALUES (?, 'running', ?)",
                (stage.name, str(log.relative_to(root))),
            )
            if version == 2:
                db.execute(
                    "UPDATE attempts SET started_at=?, command=? WHERE id=?",
                    (now(), json.dumps(commands), cursor.lastrowid),
                )
        state, detail, elapsed, hashes = "failed", None, 0.0, None
        code: int | None = None
        try:
            code, elapsed = execute(
                commands[0],
                manifest.stage_timeout_seconds,
                log,
                name,
            )
            if code:
                raise ValueError(f"ORFS {stage.name} exited {code}")
            hashes = artifact_hashes(root, stage)
            if version == 2 and log.is_file():
                hashes[log.relative_to(root).as_posix()] = sha256(log)
            if version == 2 and stage.name == "synth":
                yosys_log = f"logs/{manifest.platform}/{manifest.design}/base/1_2_yosys.log"
                if (root / yosys_log).resolve().is_relative_to(root) and (
                    root / yosys_log
                ).is_file():
                    hashes[yosys_log] = sha256(root / yosys_log)
            if len(commands) == 2:
                code, duration = execute(
                    commands[1],
                    manifest.stage_timeout_seconds,
                    attempt / "gds-validation.log",
                    name,
                )
                elapsed += duration
                if version == 2 and code == 0:
                    relative = (attempt / "gds-validation.log").relative_to(root).as_posix()
                    hashes[relative] = sha256(root / relative)
                if code:
                    raise ValueError(f"KLayout GDS validation exited {code}")
            state = "passed"
            passed.add(stage.name)
        except (OSError, ValueError) as error:
            detail = str(error)
        except KeyboardInterrupt:
            state, detail = "interrupted", "Stage execution interrupted"
            raise
        finally:
            with db:
                if version == 2:
                    if log.is_file():
                        hashes = hashes or {}
                        hashes[log.relative_to(root).as_posix()] = sha256(log)
                    db.execute(
                        "UPDATE attempts SET ended_at=?, exit_code=? WHERE id=?",
                        (now(), code, cursor.lastrowid),
                    )
                db.execute(
                    "UPDATE attempts SET state=?, runtime_seconds=?, detail=?, artifacts=? "
                    "WHERE id=?",
                    (state, elapsed, detail, json.dumps(hashes), cursor.lastrowid),
                )
        if state != "passed":
            return False
    return True


def checkpoint_valid(root: Path, stage: Stage, hashes: dict[str, str]) -> bool:
    if not hashes:
        return False
    actual = artifact_hashes(root, stage)
    return all(hashes.get(path) == digest for path, digest in actual.items()) and all(
        (root / path).resolve().is_relative_to(root) and sha256(root / path) == digest
        for path, digest in hashes.items()
        if path not in actual
    )


def report(root: Path) -> dict[str, Any]:
    root = root.resolve()
    database = root / "run.sqlite3"
    if not database.is_file():
        raise ValueError(f"experiment database missing in {root}")
    with closing(sqlite3.connect(database.as_uri() + "?mode=ro", uri=True)) as db:
        db.execute("BEGIN")
        version = db_version(db)
        db.row_factory = sqlite3.Row
        data = dict(db.execute("SELECT * FROM run").fetchone())
        attempts = [dict(row) for row in db.execute("SELECT * FROM attempts ORDER BY id")]
        manifest = ORFSManifest(**json.loads(data["manifest"]))
        input_error = None
        try:
            if load_manifest(root / "manifest.toml") != manifest:
                raise ValueError("run manifest differs from recorded inputs")
            check_inputs(root, db, manifest)
        except (OSError, ValueError) as error:
            input_error = str(error)
    data["manifest"] = json.loads(data["manifest"])
    for item in attempts:
        if item["artifacts"]:
            item["artifacts"] = json.loads(item["artifacts"])
        if item.get("command"):
            item["command"] = json.loads(item["command"])
    data["attempts"] = attempts
    latest = {item["stage"]: item for item in attempts}
    finish = latest.get("finish")
    gds_status = "not checked"
    if input_error:
        gds_status = "blocked"
    elif finish and finish["state"] == "passed":
        try:
            stages = stage_plan(manifest, legacy=version == 0)
            previous_id = 0
            gds_status = "passed"
            for stage in stages:
                attempt = latest.get(stage.name)
                if (
                    not attempt
                    or attempt["state"] != "passed"
                    or attempt["id"] <= previous_id
                    or not checkpoint_valid(root, stage, attempt["artifacts"])
                ):
                    gds_status = "blocked"
                    break
                previous_id = attempt["id"]
        except (OSError, ValueError):
            gds_status = "blocked"
    elif finish:
        gds_status = "blocked"
    design = data["manifest"]
    variant = Path(design["platform"], design["design"], "base")
    metrics, gates = summarize(
        root / "logs" / variant,
        root / "reports" / variant,
        root / "results" / variant,
        final_valid=gds_status == "passed",
        route_valid=gds_status == "passed",
    )
    data["metrics"] = metrics
    data["runtime_seconds_all_attempts"] = sum(item["runtime_seconds"] or 0 for item in attempts)
    if gds_status == "passed" and finish:
        gds_sha = finish["artifacts"][(Path("results") / variant / "6_final.gds").as_posix()]
        for target in ("drc", "lvs"):
            status_path = root / f"verification-{target}.json"
            if status_path.is_file():
                status = json.loads(status_path.read_text(encoding="utf-8"))
                if status["gds_sha256"] == gds_sha and status["exit_code"] != 0:
                    gates[target] = "blocked"
                if version == 2 and (
                    status.get("gds_sha256") != gds_sha
                    or not set(verification_files(variant.as_posix(), target)).issubset(
                        status.get("artifacts", {})
                    )
                    or not all(
                        (root / relative).resolve().is_relative_to(root)
                        and (root / relative).is_file()
                        and sha256(root / relative) == digest
                        for relative, digest in status.get("artifacts", {}).items()
                    )
                ):
                    gates[target] = "blocked"
            elif version == 2:
                gates[target] = "not checked"
    elif finish or input_error:
        gates.update(setup="blocked", hold="blocked", drc="blocked", lvs="blocked")
    data["gates"] = {
        "gds_integrity": gds_status,
        "rtl_regression": "not checked",
        "equivalence": "not checked",
        "timing_coverage": "not checked",
        **gates,
        "connectivity": "not checked",
    }
    verification = {}
    for target in ("drc", "lvs"):
        path = root / f"verification-{target}.json"
        if path.is_file():
            verification[target] = json.loads(path.read_text(encoding="utf-8"))
    data["verification"] = verification
    data["database_version"] = version
    data["input_error"] = input_error
    return data


def verify(root: Path) -> dict[str, Any]:
    if sys.platform != "linux":
        raise ValueError("physical verification requires Linux")
    root = root.resolve()
    if not (root / "run.sqlite3").is_file():
        raise ValueError(f"experiment database missing in {root}")
    manifest = load_manifest(root / "manifest.toml")
    if not isinstance(manifest, ORFSManifest):
        raise ValueError("physical verification requires an ORFS manifest")
    docker = preflight()
    with execution_lock(root):
        with closing(sqlite3.connect(root / "run.sqlite3")) as db:
            check_inputs(root, db, manifest)
        cleanup_verification(root, docker)
        baseline = report(root)
        if baseline["gates"]["gds_integrity"] != "passed":
            raise ValueError("verification requires a valid GDS checkpoint")
        memory_mib = integer(baseline["memory_mib"], "memory_mib", 1048576)
        finish = next(item for item in reversed(baseline["attempts"]) if item["stage"] == "finish")
        relative = f"results/{manifest.platform}/{manifest.design}/base/6_final.gds"
        variant = f"{manifest.platform}/{manifest.design}/base"
        for target in ("drc", "lvs"):
            name = f"aeda-{uuid.uuid4().hex}"
            attempt = root / "attempts" / name
            attempt.mkdir(parents=True)
            command = container_command(docker, root, name, manifest.cores, memory_mib)
            status = root / f"verification-{target}.json"
            started = now()
            result: dict[str, Any] = {
                "gds_sha256": finish["artifacts"][relative],
                "exit_code": None,
                "runtime_seconds": None,
                "attempt": name,
                "started_at": started,
            }
            write_verification_status(status, result)
            files = verification_files(variant, target)
            for path in files:
                original = root / path
                if original.is_file():
                    preserved = attempt / "previous" / path
                    preserved.parent.mkdir(parents=True, exist_ok=True)
                    original.rename(preserved)
            code, elapsed = execute(
                command + make_command(manifest, target),
                manifest.stage_timeout_seconds,
                attempt / f"{target}.log",
                name,
            )
            result.update(exit_code=code, runtime_seconds=elapsed)
            result.update(
                attempt=name,
                started_at=started,
                ended_at=now(),
                command=command + make_command(manifest, target),
                artifacts={path: sha256(root / path) for path in files if (root / path).is_file()},
            )
            log = attempt / f"{target}.log"
            if log.is_file():
                result["artifacts"][log.relative_to(root).as_posix()] = sha256(log)
            snapshot = json.dumps(result)
            (attempt / "verification.json").write_text(snapshot, encoding="utf-8")
            write_verification_status(status, result)
        return report(root)


def write_verification_status(path: Path, result: dict[str, Any]) -> None:
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(result), encoding="utf-8")
    temporary.replace(path)

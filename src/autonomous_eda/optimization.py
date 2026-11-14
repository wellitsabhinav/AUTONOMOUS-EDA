"""Durable, explicitly approved optimization campaigns."""

from __future__ import annotations

import json
import sqlite3
import subprocess
import time
from collections.abc import Iterator
from contextlib import closing, contextmanager
from dataclasses import asdict
from enum import StrEnum
from pathlib import Path
from typing import Any
from uuid import uuid4

from .doctor import available_cpus, memory_bytes
from .evidence import Experiment
from .experiment import continue_run, execution_lock, initialize, report, verify
from .manifest import fingerprint, load_manifest
from .mcp.snapshot import Source, ordinary
from .optimization_policy import (
    PROTECTED,
    Plan,
    candidate_grid,
    candidate_id,
    candidate_manifest,
    identify_baseline,
    load_plan,
    validate_plan,
)
from .optimization_proposer import propose
from .optimization_results import Outcome, evaluate, frontier
from .orfs import FLOW, DispatchStopped, preflight
from .reference import IMAGE

APPLICATION_ID = 0x4145444F


class Stop(StrEnum):
    BUDGET_EXHAUSTED = "BUDGET_EXHAUSTED"
    MAX_CANDIDATES_REACHED = "MAX_CANDIDATES_REACHED"
    NO_LEGAL_CANDIDATES = "NO_LEGAL_CANDIDATES"
    DUPLICATE_PROPOSALS = "DUPLICATE_PROPOSALS"
    BASELINE_INTEGRITY_FAILURE = "BASELINE_INTEGRITY_FAILURE"
    UNSUPPORTED_CONFIGURATION = "UNSUPPORTED_CONFIGURATION"
    REQUIRED_EVIDENCE_MISSING = "REQUIRED_EVIDENCE_MISSING"
    AUTHORIZATION_REVOKED = "AUTHORIZATION_REVOKED"
    TIMEOUT = "TIMEOUT"
    RESOURCE_LIMIT = "RESOURCE_LIMIT"
    USER_CANCELLED = "USER_CANCELLED"
    SEARCH_COMPLETE = "SEARCH_COMPLETE"
    EXECUTION_FAILURE = "EXECUTION_FAILURE"


class Stopped(DispatchStopped):
    def __init__(self, reason: Stop) -> None:
        self.reason = reason


def pack(value: object) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)


class Campaign:
    def __init__(self, root: Path) -> None:
        self.source = Source.register(root.absolute())
        self.root = self.source.root
        self.path = self.root / "campaign.sqlite3"

    @contextmanager
    def connect(self, *, create: bool = False) -> Iterator[sqlite3.Connection]:
        self.source.check()
        if not create:
            ordinary(self.path, directory=False)
        for suffix in ("-wal", "-shm", "-journal"):
            sidecar = self.path.with_name(self.path.name + suffix)
            if sidecar.exists() or sidecar.is_symlink():
                ordinary(sidecar, directory=False)
        mode = "rwc" if create else "rw"
        with closing(sqlite3.connect(self.path.as_uri() + f"?mode={mode}", uri=True)) as db:
            db.row_factory = sqlite3.Row
            db.execute("PRAGMA trusted_schema=OFF")
            if not create and db.execute("PRAGMA application_id").fetchone()[0] != APPLICATION_ID:
                raise ValueError("Unrecognized campaign database")
            with db:
                yield db

    def create(self, plan: Plan, baseline: Path) -> None:
        validate_plan(plan)
        if self.path.exists() or self.path.is_symlink():
            raise ValueError("Campaign already exists")
        with self.path.open("xb"):
            pass
        with self.connect(create=True) as db:
            db.executescript(f"""
                BEGIN;
                PRAGMA application_id={APPLICATION_ID};
                CREATE TABLE campaign (id INTEGER PRIMARY KEY CHECK(id=1), plan TEXT NOT NULL,
                    digest TEXT NOT NULL, baseline TEXT NOT NULL, approval TEXT,
                    state TEXT NOT NULL, reason TEXT, started REAL);
                CREATE TABLE candidates (id TEXT PRIMARY KEY, proposal TEXT NOT NULL,
                    digest TEXT NOT NULL, state TEXT NOT NULL, started REAL, ended REAL,
                    run_id TEXT, reason TEXT, outcome TEXT);
                CREATE TABLE events (seq INTEGER PRIMARY KEY, at REAL NOT NULL,
                    kind TEXT NOT NULL, detail TEXT NOT NULL);
            """)
            db.execute(
                "INSERT INTO campaign VALUES (1,?,?,?,NULL,'PROPOSED',NULL,NULL)",
                (pack(plan.body()), plan.digest, str(baseline)),
            )
            self.event(db, "PROPOSED", {"plan_digest": plan.digest})
            db.execute("UPDATE campaign SET state='VALIDATED'")
            self.event(db, "VALIDATED", {"baseline_digest": plan.baseline.digest})

    @staticmethod
    def event(db: sqlite3.Connection, kind: str, detail: object) -> None:
        db.execute(
            "INSERT INTO events(at,kind,detail) VALUES (?,?,?)", (time.time(), kind, pack(detail))
        )

    def row(self) -> sqlite3.Row:
        with self.connect() as db:
            row = db.execute("SELECT * FROM campaign WHERE id=1").fetchone()
            if not isinstance(row, sqlite3.Row):
                raise ValueError("Missing campaign plan")
            return row

    def checked_plan(self) -> tuple[Plan, Experiment]:
        row = self.row()
        base, evidence = identify_baseline(Path(row["baseline"]))
        data = json.loads(row["plan"])
        plan = load_plan(data, base, self.root)
        if row["digest"] != plan.digest:
            raise ValueError("Stored plan digest does not match")
        return plan, evidence

    def baseline_root(self) -> Path:
        return Source.register(Path(self.row()["baseline"])).root

    def approve(self, digest: str) -> None:
        plan, _ = self.checked_plan()
        if plan.baseline.design != "gcd":
            with self.connect() as db:
                self.event(
                    db,
                    "REJECTED",
                    {"reason": Stop.UNSUPPORTED_CONFIGURATION, "scope": "planning-only"},
                )
            raise ValueError("Ibex is planning-only under this authorization")
        if digest != plan.digest:
            self.record("REJECTED", {"reason": Stop.AUTHORIZATION_REVOKED})
            raise ValueError("Approval must supply the current plan digest")
        record = {
            "digest": plan.digest,
            "baseline_digest": plan.baseline.digest,
            "approved_at": time.time(),
            "expires_at": time.time() + 86400,
            "policy": "explicit_operator_approval",
        }
        with self.connect() as db:
            change = db.execute(
                "UPDATE campaign SET approval=?,state='APPROVED' "
                "WHERE state='VALIDATED' AND digest=?",
                (pack(record), digest),
            )
            if change.rowcount != 1:
                raise ValueError("Campaign cannot be approved in its current state")
            self.event(db, "APPROVED", record)

    def cancel(self) -> None:
        with self.connect() as db:
            change = db.execute(
                "UPDATE campaign SET state='CANCELLED',reason=? "
                "WHERE state IN ('PROPOSED','VALIDATED','APPROVED','EXECUTING')",
                (Stop.USER_CANCELLED,),
            )
            if change.rowcount:
                self.event(db, "CANCELLED", {"reason": Stop.USER_CANCELLED})

    def stop(self, reason: Stop) -> None:
        state = (
            "COMPLETED"
            if reason
            in {
                Stop.MAX_CANDIDATES_REACHED,
                Stop.SEARCH_COMPLETE,
                Stop.NO_LEGAL_CANDIDATES,
            }
            else "FAILED"
        )
        with self.connect() as db:
            change = db.execute(
                "UPDATE campaign SET state=?,reason=? WHERE state IN ('APPROVED','EXECUTING')",
                (state, reason),
            )
            if change.rowcount:
                self.event(db, state, {"reason": reason})

    def remaining(self, plan: Plan) -> float:
        row = self.row()
        current = time.time()
        if row["state"] == "CANCELLED":
            raise Stopped(Stop.USER_CANCELLED)
        approval = json.loads(row["approval"] or "{}")
        if (
            row["state"] not in {"APPROVED", "EXECUTING"}
            or row["digest"] != plan.digest
            or fingerprint(json.loads(row["plan"])) != plan.digest
            or approval.get("digest") != plan.digest
            or approval.get("baseline_digest") != plan.baseline.digest
            or approval.get("policy") != "explicit_operator_approval"
            or not isinstance(approval.get("approved_at"), (int, float))
            or not isinstance(approval.get("expires_at"), (int, float))
            or not approval["approved_at"] <= current < approval["expires_at"]
            or approval["expires_at"] - approval["approved_at"] > 86401
        ):
            raise Stopped(Stop.AUTHORIZATION_REVOKED)
        remaining = (row["started"] or current) + plan.budget.maximum_runtime - current
        if remaining <= 0:
            raise Stopped(Stop.BUDGET_EXHAUSTED)
        return remaining

    def entries(self) -> list[dict[str, Any]]:
        with self.connect() as db:
            return [
                {
                    **dict(row),
                    "proposal": json.loads(row["proposal"]),
                    "outcome": json.loads(row["outcome"]) if row["outcome"] else None,
                }
                for row in db.execute("SELECT * FROM candidates ORDER BY rowid")
            ]

    def reserve(self, plan: Plan, changes: dict[str, int], strategy: str) -> dict[str, Any]:
        self.remaining(plan)
        if changes not in candidate_grid(plan):
            self.record("REJECTED", {"reason": Stop.UNSUPPORTED_CONFIGURATION})
            raise Stopped(Stop.UNSUPPORTED_CONFIGURATION)
        cid = candidate_id(plan, changes)
        proposal = {
            "candidate_id": cid,
            "parent_plan_digest": plan.digest,
            "baseline_identity": plan.baseline.digest,
            "parameter_changes": changes,
            "strategy": strategy,
            "expected_effects": [
                "Changing floorplan utilization may alter placement distance "
                "and repair opportunities."
            ],
            "supporting_diagnosis": plan.baseline.diagnosis_identity,
            "evidence_references": sorted(plan.baseline.evidence),
            "predicted_risks": ["Timing or area may regress. Placement and routing may fail."],
            "required_validation": plan.body()["required_verification_gates"],
            "estimated_resource_cost": {
                "maximum_seconds": plan.budget.candidate_seconds,
                "memory_mib": plan.budget.maximum_memory,
                "cores": plan.baseline.cores,
            },
        }
        now = time.time()
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            state = db.execute("SELECT state FROM campaign").fetchone()[0]
            if state != "EXECUTING":
                raise Stopped(
                    Stop.USER_CANCELLED if state == "CANCELLED" else Stop.AUTHORIZATION_REVOKED
                )
            count = db.execute("SELECT count(*) FROM candidates").fetchone()[0]
            if count >= plan.budget.maximum_candidates:
                raise Stopped(Stop.MAX_CANDIDATES_REACHED)
            if db.execute("SELECT 1 FROM candidates WHERE id=?", (cid,)).fetchone():
                raise Stopped(Stop.DUPLICATE_PROPOSALS)
            db.execute(
                "INSERT INTO candidates VALUES (?,?,?,'EXECUTING',?,NULL,NULL,NULL,NULL)",
                (cid, pack(proposal), fingerprint(proposal), now),
            )
            self.event(db, "CANDIDATE_RESERVED", {"candidate_id": cid, "strategy": strategy})
        return self.entries()[-1]

    def finish_candidate(
        self,
        cid: str,
        state: str,
        reason: Stop | None,
        outcome: Outcome | None = None,
    ) -> None:
        with self.connect() as db:
            db.execute(
                "UPDATE candidates SET state=?,ended=?,reason=?,outcome=? WHERE id=?",
                (state, time.time(), reason, pack(asdict(outcome)) if outcome else None, cid),
            )
            self.event(db, state, {"candidate_id": cid, "reason": reason})

    def bind_run(self, cid: str, run_id: str) -> None:
        with self.connect() as db:
            db.execute(
                "UPDATE candidates SET run_id=? WHERE id=? AND run_id IS NULL", (run_id, cid)
            )

    def record(self, kind: str, detail: object) -> None:
        with self.connect() as db:
            self.event(db, kind, detail)


def attest(docker: str, timeout: float) -> None:
    check = subprocess.run(
        [
            docker,
            "run",
            "--rm",
            "--pull=never",
            "--network=none",
            "--cpus=1",
            "--memory=128m",
            "--workdir",
            FLOW,
            "--entrypoint",
            "sha256sum",
            IMAGE,
            *PROTECTED,
        ],
        capture_output=True,
        text=True,
        timeout=min(30, timeout),
        check=False,
    )
    if (
        check.returncode
        or dict(
            (name, digest) for digest, name in (line.split() for line in check.stdout.splitlines())
        )
        != PROTECTED
    ):
        raise Stopped(Stop.BASELINE_INTEGRITY_FAILURE)


def require_resources(plan: Plan) -> None:
    cpus = available_cpus()
    _, available = memory_bytes()
    if cpus is None or plan.baseline.cores > cpus or plan.budget.maximum_memory * 2**20 > available:
        raise Stopped(Stop.RESOURCE_LIMIT)


def candidate_root(campaign: Campaign, cid: str) -> Path:
    if len(cid) != 24 or any(ch not in "0123456789abcdef" for ch in cid):
        raise ValueError("Invalid candidate identity")
    parent = campaign.root / "candidates"
    parent.mkdir(exist_ok=True)
    ordinary(parent, directory=True)
    return parent / cid


def run_candidate(
    campaign: Campaign, plan: Plan, baseline: Experiment, entry: dict[str, Any]
) -> None:
    from .optimization_policy import snapshot

    cid, proposal = entry["id"], entry["proposal"]
    changes = proposal["parameter_changes"]
    if (
        fingerprint(proposal) != entry["digest"]
        or proposal["candidate_id"] != cid
        or proposal["parent_plan_digest"] != plan.digest
        or candidate_id(plan, changes) != cid
        or changes not in candidate_grid(plan)
    ):
        raise Stopped(Stop.AUTHORIZATION_REVOKED)
    run_root = candidate_root(campaign, cid)
    manifest = candidate_manifest(plan, changes)

    def guard() -> float:
        remaining = min(
            campaign.remaining(plan),
            float(entry["started"]) + plan.budget.candidate_seconds - time.time(),
        )
        if remaining <= 0:
            raise Stopped(Stop.TIMEOUT)
        return remaining

    def check_tree() -> None:
        Source.register(run_root)
        for path in run_root.rglob("*"):
            if path.is_symlink():
                raise ValueError("Candidate directory contains a linked artifact")
            ordinary(path, directory=path.is_dir())

    try:
        guard()
        require_resources(plan)
        if not run_root.exists() and not run_root.is_symlink():
            path = campaign.root / f"{cid}.toml"
            body = "schema_version = 1\n" + "".join(
                f"{key} = {json.dumps(value)}\n" for key, value in asdict(manifest).items()
            )
            try:
                with path.open("x", encoding="utf-8", newline="\n") as stream:
                    stream.write(body)
            except FileExistsError:
                ordinary(path, directory=False)
                if path.read_text(encoding="utf-8") != body:
                    raise Stopped(Stop.AUTHORIZATION_REVOKED) from None
            initialize(run_root, path, manifest, plan.budget.maximum_memory)
        check_tree()
        if load_manifest(run_root / "manifest.toml") != manifest:
            raise Stopped(Stop.AUTHORIZATION_REVOKED)
        recorded = report(run_root)
        if recorded["input_error"] or recorded["memory_mib"] != plan.budget.maximum_memory:
            raise Stopped(Stop.AUTHORIZATION_REVOKED)
        run_id = recorded["run_id"]
        if entry["run_id"] not in {None, run_id}:
            raise Stopped(Stop.AUTHORIZATION_REVOKED)
        if entry["run_id"] is None and recorded["attempts"]:
            raise Stopped(Stop.AUTHORIZATION_REVOKED)
        campaign.bind_run(cid, run_id)
        implemented = continue_run(run_root, guard=guard)
        if implemented:
            verify(run_root, guard=guard)
        guard()
        try:
            fresh, _ = campaign.checked_plan()
        except (OSError, ValueError, sqlite3.Error, KeyError) as error:
            raise Stopped(Stop.BASELINE_INTEGRITY_FAILURE) from error
        if fresh.digest != plan.digest:
            raise Stopped(Stop.BASELINE_INTEGRITY_FAILURE)
        result = snapshot(run_root)
        outcome = evaluate(
            plan, baseline, result, changes, attested=True, run_bound=result["run_id"] == run_id
        )
        reason = None if implemented else Stop.EXECUTION_FAILURE
        if implemented and outcome.gates["artifact_integrity"] != "PASSED":
            reason = Stop.REQUIRED_EVIDENCE_MISSING
        if any(item.get("exit_code") == 124 for item in result["attempts"][-1:]):
            reason = Stop.TIMEOUT
        if any(item.get("exit_code") == 137 for item in result["attempts"][-1:]):
            reason = Stop.RESOURCE_LIMIT
        campaign.finish_candidate(cid, "COMPLETED" if implemented else "FAILED", reason, outcome)
        if reason == Stop.RESOURCE_LIMIT:
            campaign.stop(reason)
    except Stopped as error:
        campaign.finish_candidate(
            cid, "CANCELLED" if error.reason == Stop.USER_CANCELLED else "FAILED", error.reason
        )
        if error.reason not in {Stop.TIMEOUT}:
            raise
    except (OSError, ValueError, sqlite3.Error, KeyError) as error:
        campaign.finish_candidate(cid, "FAILED", Stop.EXECUTION_FAILURE)
        campaign.record(
            "EXECUTION_ERROR", {"candidate_id": cid, "error_type": type(error).__name__}
        )


def run(campaign: Campaign) -> None:
    with execution_lock(campaign.root):
        try:
            row = campaign.row()
            if row["state"] not in {"APPROVED", "EXECUTING"}:
                raise ValueError("Campaign requires an approved, active plan")
            try:
                plan, baseline = campaign.checked_plan()
            except (OSError, ValueError, sqlite3.Error, KeyError) as error:
                raise Stopped(Stop.BASELINE_INTEGRITY_FAILURE) from error
            if plan.baseline.design != "gcd":
                raise Stopped(Stop.UNSUPPORTED_CONFIGURATION)
            require_resources(plan)
            campaign.remaining(plan)
            with campaign.connect() as db:
                change = db.execute(
                    "UPDATE campaign SET state='EXECUTING',started=coalesce(started,?) "
                    "WHERE state IN ('APPROVED','EXECUTING')",
                    (time.time(),),
                )
                if not change.rowcount:
                    raise Stopped(Stop.USER_CANCELLED)
            try:
                docker = preflight()
            except (OSError, ValueError) as error:
                raise Stopped(Stop.UNSUPPORTED_CONFIGURATION) from error
            attest(docker, campaign.remaining(plan))
            campaign.record("ATTESTED", {"image": IMAGE, "protected_inputs": PROTECTED})
            while True:
                campaign.remaining(plan)
                entries = campaign.entries()
                pending = next((item for item in entries if item["state"] == "EXECUTING"), None)
                if pending is not None:
                    run_candidate(campaign, plan, baseline, pending)
                    if campaign.row()["state"] != "EXECUTING":
                        break
                    continue
                if len(entries) >= plan.budget.maximum_candidates:
                    campaign.stop(Stop.MAX_CANDIDATES_REACHED)
                    break
                previous = {item["id"] for item in entries}
                available = [
                    item
                    for item in candidate_grid(plan)
                    if candidate_id(plan, item) not in previous
                ]
                if not available:
                    campaign.stop(Stop.SEARCH_COMPLETE if entries else Stop.NO_LEGAL_CANDIDATES)
                    break
                choice, strategy, events = propose(plan, baseline, available, entries)
                for event in events:
                    campaign.record("PROPOSER", event)
                try:
                    fresh, _ = campaign.checked_plan()
                except (OSError, ValueError, sqlite3.Error, KeyError) as error:
                    raise Stopped(Stop.BASELINE_INTEGRITY_FAILURE) from error
                if fresh.digest != plan.digest:
                    raise Stopped(Stop.BASELINE_INTEGRITY_FAILURE)
                campaign.reserve(plan, choice, strategy)
        except Stopped as error:
            campaign.stop(error.reason)
        except subprocess.TimeoutExpired:
            campaign.stop(Stop.TIMEOUT)
        except KeyboardInterrupt:
            pending = next(
                (entry["id"] for entry in campaign.entries() if entry["state"] == "EXECUTING"),
                None,
            )
            campaign.record("INTERRUPTED", {"candidate_id": pending})
            raise
        except (OSError, ValueError, sqlite3.Error):
            if campaign.row()["state"] == "EXECUTING":
                campaign.stop(Stop.EXECUTION_FAILURE)
            raise


def campaign_report(campaign: Campaign) -> dict[str, Any]:
    from .optimization_policy import snapshot

    row = campaign.row()
    plan, baseline = campaign.checked_plan()
    entries = campaign.entries()
    outcomes = {}
    for entry in entries:
        if entry["outcome"] is None:
            continue
        try:
            source = snapshot(candidate_root(campaign, entry["id"]))
            old = entry["outcome"]["evidence"]
            proposal = entry["proposal"]
            bound = (
                source["run_id"] == entry["run_id"] == old["run_id"]
                and entry["id"] == candidate_id(plan, proposal["parameter_changes"])
                and entry["digest"] == fingerprint(proposal)
            )
            evidence_unchanged = old["artifact_sha256"] == {
                name: item["sha256"]
                for name, item in source["artifacts"].items()
                if item["availability"] == "VERIFIED"
            }
            value = evaluate(
                plan,
                baseline,
                source,
                proposal["parameter_changes"],
                attested=bound and evidence_unchanged,
                run_bound=bound,
            )
            entry["outcome"] = asdict(value)
            outcomes[entry["id"]] = value
        except (OSError, ValueError, sqlite3.Error, KeyError):
            entry["outcome"] = None
            entry["evidence_status"] = "UNAVAILABLE"
    with campaign.connect() as db:
        events = [
            {**dict(event), "detail": json.loads(event["detail"])}
            for event in db.execute("SELECT * FROM events ORDER BY seq")
        ]
    return {
        "schema_version": 1,
        "optimization_id": plan.optimization_id,
        "plan_digest": plan.digest,
        "state": row["state"],
        "stop_reason": row["reason"],
        "plan": {
            key: value for key, value in plan.body().items() if key not in {"run_root", "endpoint"}
        },
        "approval": json.loads(row["approval"]) if row["approval"] else None,
        "baseline": asdict(evaluate(plan, baseline, baseline)),
        "candidates": entries,
        "events": events,
        "feasible_frontiers": {
            profile: frontier(outcomes, plan.objectives, profile)
            for profile in ("physical", "full")
        },
        "budget_consumption": {
            "candidates": len(entries),
            "candidate_wall_seconds": sum(
                (entry["ended"] or time.time()) - entry["started"] for entry in entries
            ),
            "elapsed_wall_seconds": (
                (events[-1]["at"] if row["reason"] else time.time()) - row["started"]
                if row["started"]
                else 0.0
            ),
        },
    }


def new_plan(
    campaign: Campaign,
    baseline: Path,
    **options: Any,
) -> Plan:
    base, _ = identify_baseline(baseline)
    if baseline.is_relative_to(campaign.root) or campaign.root.is_relative_to(baseline):
        raise ValueError("Campaign and baseline directories must be isolated")
    plan = Plan(1, uuid4().hex, base, str(campaign.root), **options)
    campaign.create(plan, baseline)
    return plan

"""Evaluate verification from current inputs and immutable attempt evidence."""

from __future__ import annotations

import json
import re
import shutil
import sys
import uuid
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any

from ..diagnostic_artifacts import STAGE_REPORTS
from ..evidence import Experiment, verified_artifact
from ..experiment import now
from ..manifest import fingerprint, sha256
from ..mcp.snapshot import Snapshot, Source, collect_evidence, ordinary, relative_parts
from . import rtl, timing
from .policy import (
    ALL_GATES,
    OBJECTIVES,
    PHYSICAL,
    PROFILES,
    RTL,
    Policy,
    State,
    load_policy,
    verdict,
)
from .process import Execution, log_text

CONFIG = "verification.toml"
LATEST = "verification/latest.json"
ATTEMPTS = "verification/attempts"
RESULT_LIMIT = 256 * 1024


def atomic(path: Path, value: object) -> None:
    with TemporaryDirectory(dir=path.parent, prefix=".record-") as directory:
        staged = Path(directory) / path.name
        staged.write_text(
            json.dumps(value, sort_keys=True, indent=2, allow_nan=False) + "\n",
            encoding="utf-8",
            newline="\n",
        )
        staged.replace(path)


def read_json(path: Path) -> dict[str, Any]:
    ordinary(path, directory=False)
    with path.open("rb") as stream:
        raw = stream.read(RESULT_LIMIT + 1)
    if len(raw) > RESULT_LIMIT:
        raise ValueError("verification record exceeds its size limit")
    result = json.loads(raw)
    if not isinstance(result, dict):
        raise ValueError("expected a verification record object")
    return result


@contextmanager
def writer(root: Path) -> Iterator[None]:
    folder = root / "verification"
    folder.mkdir(exist_ok=True)
    ordinary(folder, directory=True)
    lock = folder / "dispatch.lock"
    if lock.exists() or lock.is_symlink():
        ordinary(lock, directory=False)
    with lock.open("a+b") as stream:
        if sys.platform == "win32":
            import msvcrt

            stream.seek(0)
            msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl

            fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
        yield


def policy_at(root: Path, profile: str) -> Policy:
    config = root / CONFIG
    if config.exists() or config.is_symlink():
        ordinary(config, directory=False)
        return load_policy(profile, config)
    return load_policy(profile, None)


def selection(root: Path) -> dict[str, Any] | None:
    path = root / LATEST
    if not path.exists() and not path.is_symlink():
        return None
    ordinary(path.parent, directory=True)
    value = read_json(path)
    if (
        set(value) != {"schema_version", "profile", "policy_sha256", "attempt", "receipts"}
        or value["schema_version"] != 1
        or value["profile"] not in PROFILES
        or not re.fullmatch(r"[0-9a-f]{32}", value["attempt"])
        or not re.fullmatch(r"[0-9a-f]{64}", value["policy_sha256"])
        or not isinstance(value["receipts"], dict)
        or set(value["receipts"]) - {*RTL, "timing_coverage"}
        or any(not re.fullmatch(r"[0-9a-f]{64}", item) for item in value["receipts"].values())
    ):
        raise ValueError("invalid verification attempt index")
    return value


def native_run(root: Path) -> bool:
    return not (root / "run.sqlite3").exists() and not (root / "reference.json").exists()


def native_identity(root: Path) -> tuple[str, str | None]:
    # The run has an identity even when a configured design source goes missing.
    try:
        manifest, files = rtl.source_set(root, "manifest.toml")
        return fingerprint(["rtl-verification", manifest.fingerprint(files), files]), manifest.top
    except (OSError, ValueError):
        return fingerprint(["unresolved-rtl-verification", root.name]), None


def physical_snapshot(root: Path, target: Path) -> Experiment | None:
    if native_run(root):
        return None
    return collect_evidence(Snapshot(Source.register(root), target))


def verify_run(
    root: Path,
    profile: str,
    *,
    coverage: bool = False,
    decks: bool = False,
    cancelled: Callable[[], bool] = lambda: False,
) -> dict[str, Any]:
    root = root.resolve()
    policy = policy_at(root, profile)
    if not root.is_dir():
        raise ValueError("verification requires an existing run directory")
    with writer(root):
        attempt = uuid.uuid4().hex
        index: dict[str, Any] = {
            "schema_version": 1,
            "profile": profile,
            "policy_sha256": policy.digest,
            "attempt": attempt,
            "receipts": {},
        }
        # Invalidate old results before a new dispatch. The old attempt stays available.
        atomic(root / LATEST, index)
        deck_error = None
        if decks:
            from ..experiment import verify

            try:
                verify(root)
            except (OSError, ValueError) as error:
                deck_error = str(error)
        with TemporaryDirectory(dir=root / "verification", prefix=".snapshot-") as directory:
            snapshot = Path(directory)
            evidence = physical_snapshot(root, snapshot)
            run_id = evidence["run_id"] if evidence else native_identity(root)[0]
            jobs = rtl.selected(policy) if evidence is None else []
            if coverage and evidence is not None:
                jobs.append("timing_coverage")
            for gate in jobs:
                if cancelled():
                    break
                rel = f"{ATTEMPTS}/{attempt}/{gate}"
                work = root / rel
                work.mkdir(parents=True)
                if gate == "timing_coverage":
                    assert evidence is not None
                    record = timing.run_coverage(snapshot, work, evidence, policy, cancelled)
                else:
                    record = rtl.run_gate(root, work, gate, policy, cancelled)
                receipt = work / "receipt.json"
                record.update(
                    schema_version=1,
                    run_id=run_id,
                    attempt=attempt,
                    policy_sha256=policy.digest,
                    recorded_at=now(),
                )
                atomic(receipt, record)
                index["receipts"][gate] = sha256(receipt)
                atomic(root / LATEST, index)
                if record["diagnostic"] == "cancelled":
                    raise KeyboardInterrupt
            if cancelled():
                raise KeyboardInterrupt
        result = report_run(root)
        if deck_error:
            result["dispatch_diagnostics"] = [deck_error]
        return result


def gate_row(
    name: str, state: State, reason: str, policy: Policy, run_id: str, **data: Any
) -> dict[str, Any]:
    return {
        "gate": name,
        "applicable": state != "NOT_APPLICABLE",
        "applicability": "Core-only RTL run"
        if state == "NOT_APPLICABLE"
        else "Gate applies to this evidence scope",
        "requirement": "required"
        if name in policy.required or name.startswith("objective.")
        else "optional",
        "status": state,
        "diagnostic": reason,
        "run_id": run_id,
        "attempt": None,
        "tool": None,
        "tool_version": None,
        "execution": [],
        "inputs": {},
        "outputs": {},
        "evidence_references": [],
        "recorded_at": None,
        **data,
    }


def schema_gate(evidence: Experiment, name: str, policy: Policy) -> dict[str, Any]:
    gate = evidence["verification_gates"][name]
    state: State = "UNKNOWN" if gate["state"] == "NOT_CHECKED" else gate["state"]
    reason = {
        "PASSED": "Verified schema 1 tool evidence passed",
        "FAILED": "Verified schema 1 tool evidence reports a violation",
        "BLOCKED": "Required tool evidence is unavailable, changed or inconsistent",
        "UNKNOWN": "No recorded check for this gate",
    }[state]
    tools = {
        "setup": "openroad",
        "hold": "openroad",
        "router": "openroad",
        "synthesis": "yosys",
        "gds_generation": "klayout",
        "gds_reopen": "klayout",
        "platform_drc": "klayout",
        "lvs": "klayout",
    }
    tool = tools.get(name)
    artifacts = {
        ref: evidence["artifacts"][ref] for ref in gate["artifacts"] if ref in evidence["artifacts"]
    }
    attempts = {str(item["id"]): item for item in evidence["attempts"]}
    attempt = next(
        (attempts[item["attempt"]] for item in artifacts.values() if item["attempt"] in attempts),
        {},
    )
    if name in {"platform_drc", "lvs"}:
        key = "drc" if name == "platform_drc" else "lvs"
        attempt = (evidence["provenance"].get("verification") or {}).get(key, {})
    return gate_row(
        name,
        state,
        reason,
        policy,
        evidence["run_id"],
        tool=tool,
        tool_version=evidence["toolchain"].get(tool or ""),
        attempt=attempt.get("attempt", attempt.get("id")),
        execution=[
            {
                "command": attempt.get("command"),
                "return_code": attempt.get("exit_code"),
                "started_at": attempt.get("started_at"),
                "runtime_seconds": attempt.get("runtime_seconds"),
            }
        ]
        if attempt
        else [],
        outputs={ref: item["sha256"] for ref, item in artifacts.items()},
        evidence_references=gate["artifacts"],
    )


def read_receipt(
    root: Path,
    index: dict[str, Any] | None,
    policy: Policy,
    run_id: str,
    name: str,
    evidence: Experiment | None,
    evidence_root: Path,
) -> dict[str, Any]:
    blocked = gate_row(name, "BLOCKED", "No completed attempt for this policy", policy, run_id)
    if not index or name not in index["receipts"]:
        return blocked
    attempt = index["attempt"]
    rel = f"{ATTEMPTS}/{attempt}/{name}"
    work = root / rel
    try:
        for parent in reversed(work.relative_to(root).parents):
            ordinary(root / parent, directory=True)
        ordinary(work, directory=True)
        receipt = work / "receipt.json"
        ordinary(receipt, directory=False)
        digest = index["receipts"][name]
        if sha256(receipt) != digest:
            raise ValueError("Verification receipt was modified")
        row = read_json(receipt)
        if (
            row["schema_version"] != 1
            or row["attempt"] != attempt
            or row["run_id"] != run_id
            or row["policy_sha256"] != index["policy_sha256"]
            or index["policy_sha256"] != policy.digest
            or row["gate"] != name
        ):
            raise ValueError("Verification attempt identity or policy changed")
        if row["status"] in {"PASSED", "FAILED"}:
            current: dict[str, str | None]
            if name == "timing_coverage":
                assert evidence is not None
                current = {
                    path: evidence["artifacts"][path]["sha256"]
                    for path in timing.paths(evidence)
                    if verified_artifact(evidence["artifacts"], path)
                }
                if set(current) != set(timing.paths(evidence)):
                    raise ValueError("Required timing inputs are missing or modified")
            else:
                _, native_inputs = rtl.inputs(root, name, policy)
                current = dict(native_inputs)
            if not current or current != row["inputs"]:
                raise ValueError("Verification input hash mismatch")
        outputs = row["outputs"]
        for output, expected in outputs.items():
            if len(relative_parts(output)) != 1:
                raise ValueError("Invalid verification artifact path")
            ordinary(work / output, directory=False)
            if sha256(work / output) != expected:
                raise ValueError(f"Verification output hash mismatch: {output}")
        runs = [Execution(**item) for item in row["execution"]]
        status: State = row["status"]
        reason = row["diagnostic"]
        context = None
        if status in {"PASSED", "FAILED"}:
            for run in runs:
                if run.stdout not in outputs or run.stderr not in outputs:
                    raise ValueError("Execution log is absent from the receipt")
            if name == "timing_coverage":
                assert evidence is not None
                if (
                    len(runs) != 1
                    or row["tools"]["OpenSTA"]["version"] != evidence["toolchain"]["image"]
                ):
                    raise ValueError("Timing execution and tool identity differ")
                status, reason = timing.coverage_outcome(
                    work, runs[0], evidence, evidence_root, policy
                )
                context, _, _ = timing.inspect_context(evidence_root, evidence, policy)
                checks, clocks = timing.parse_coverage(log_text(work, runs[0].stdout))
                context.update(coverage_checks=checks, observed_clocks=clocks)
            else:
                for tool in rtl.TOOLS[name]:
                    path = shutil.which(tool)
                    if not path or sha256(Path(path)) != row["tools"][tool]["sha256"]:
                        raise ValueError(f"Verification tool identity changed: {tool}")
                status, reason = rtl.assess(name, work, runs[len(rtl.TOOLS[name]) :], policy)
            if row["status"] != status:
                raise ValueError("Receipt status does not match retained execution")
        elif status != "BLOCKED":
            raise ValueError("Unsupported verification receipt status")
        receipts = {
            f"{rel}/receipt.json": digest,
            **{f"{rel}/{file}": sha for file, sha in outputs.items()},
        }
        return gate_row(
            name,
            status,
            reason,
            policy,
            run_id,
            attempt=attempt,
            recorded_at=row["recorded_at"],
            execution=row["execution"],
            tools=row["tools"],
            inputs=row["inputs"],
            outputs=receipts,
            evidence_references=list(receipts),
            **({"context": context} if context is not None else {}),
        )
    except (OSError, ValueError, KeyError, TypeError, IndexError) as error:
        return {**blocked, "attempt": attempt, "diagnostic": str(error)}


def physical_rows(
    root: Path, evidence: Experiment, policy: Policy
) -> tuple[dict[str, Any], dict[str, Any]]:
    rows = {name: schema_gate(evidence, name, policy) for name in evidence["verification_gates"]}
    context, refs, problem = timing.inspect_context(root, evidence, policy)
    refs = list(refs)
    artifacts = evidence["artifacts"]
    timing_inputs = {
        path: artifacts[path]["sha256"] if path in artifacts else None for path in refs
    }
    for name in ("setup", "hold"):
        rows[name].update(context=context, inputs=timing_inputs)
        metric = evidence["metrics"][f"{name}_worst_slack_ns"]
        rows[name]["measurement"] = metric
        if problem and (
            rows[name]["status"] == "PASSED"
            or policy.timing.corner
            or policy.timing.sdc_sha256
            or "missing or modified" in problem
        ):
            rows[name].update(status="BLOCKED", diagnostic=problem)
        elif rows[name]["status"] == "FAILED":
            rows[name]["diagnostic"] = f"Final {name} violation, worst slack {metric['value']} ns"
    coverage: State = "BLOCKED"
    if not problem:
        failure = timing.retained_failure(root, evidence)
        if failure:
            rel, problem = failure
            refs.append(rel)
            coverage = "FAILED"
    rows["timing_coverage"] = gate_row(
        "timing_coverage",
        coverage,
        problem or "No complete final OpenSTA coverage report",
        policy,
        evidence["run_id"],
        tool="OpenSTA",
        context=context,
        inputs=timing_inputs,
        outputs={ref: artifacts[ref]["sha256"] if ref in artifacts else None for ref in refs},
        evidence_references=refs,
    )
    gaps = [rel for rel, item in artifacts.items() if item["availability"] != "VERIFIED"]
    input_error = evidence["provenance"].get("input_error")
    rows["artifact_integrity"] = gate_row(
        "artifact_integrity",
        "PASSED" if artifacts and not gaps and not input_error else "BLOCKED",
        input_error
        or (
            f"{len(gaps)} missing, modified or unverified recorded artifacts"
            if gaps
            else "Recorded artifact hashes match"
            if artifacts
            else "No artifact inventory"
        ),
        policy,
        evidence["run_id"],
        inputs={rel: item["sha256"] for rel, item in artifacts.items()},
        evidence_references=gaps,
    )
    return rows, context


def implementation(evidence: Experiment | None) -> State:
    if evidence is None:
        return "NOT_APPLICABLE"
    latest = {item["stage"]: item["state"] for item in evidence["attempts"]}
    if evidence["provenance"].get("input_error"):
        return "BLOCKED"
    if "failed" in latest.values():
        return "FAILED"
    if evidence["verification_gates"]["gds_reopen"]["state"] != "PASSED":
        return "BLOCKED"
    if evidence["origin"] == "external" or all(
        latest.get(item.stage) == "passed" for item in STAGE_REPORTS
    ):
        return "PASSED"
    return "BLOCKED"


def objective_rows(
    evidence: Experiment | None, policy: Policy, run_id: str
) -> list[dict[str, Any]]:
    rows = []
    for name, bound in policy.objectives:
        metric = evidence["metrics"].get(name) if evidence else None
        value = metric["value"] if metric and metric["status"] == "MEASURED" else None
        mode = OBJECTIVES[name]
        status: State = "BLOCKED"
        reason = f"{name} is unavailable"
        if value is not None and metric:
            status = "PASSED" if (value <= bound if mode == "max" else value >= bound) else "FAILED"
            reason = f"{value} {metric['unit']} measured, {mode} bound {bound} {metric['unit']}"
        ref = metric["artifact"] if metric else None
        sources = {ref: evidence["artifacts"][ref]["sha256"]} if ref and evidence else {}
        rows.append(
            gate_row(
                f"objective.{name}",
                status,
                reason,
                policy,
                run_id,
                measurement=metric,
                bound=bound,
                comparison=mode,
                inputs=sources,
                evidence_references=list(sources),
            )
        )
    return rows


def evaluate(
    root: Path,
    evidence: Experiment | None,
    evidence_root: Path,
    policy: Policy,
    index: dict[str, Any] | None,
    *,
    recorded_only: bool = False,
) -> dict[str, Any]:
    run_id, top = (evidence["run_id"], evidence["design"]) if evidence else native_identity(root)
    rows: dict[str, Any] = {}
    context: dict[str, Any] = {}
    if evidence is not None:
        rows, context = physical_rows(evidence_root, evidence, policy)
    else:
        state: State = "PASSED" if top else "BLOCKED"
        inputs = rtl.source_set(root, "manifest.toml")[1] if top else {}
        rows["artifact_integrity"] = gate_row(
            "artifact_integrity",
            state,
            "Current RTL manifest inputs are hashable"
            if top
            else "The explicit RTL source manifest or its inputs are unavailable",
            policy,
            run_id,
            inputs=inputs,
            evidence_references=list(inputs),
        )
    for name in ALL_GATES:
        if evidence is None and name in RTL and not recorded_only:
            rows[name] = read_receipt(root, index, policy, run_id, name, evidence, evidence_root)
        elif name == "timing_coverage" and evidence is not None and index and not recorded_only:
            if name in index["receipts"]:
                live = read_receipt(root, index, policy, run_id, name, evidence, evidence_root)
                if rows[name]["status"] != "FAILED":
                    rows[name] = {**rows[name], **live}
        elif name not in rows:
            applies = evidence is not None or name not in PHYSICAL and name != "connectivity"
            rows[name] = gate_row(
                name,
                "UNKNOWN" if applies else "NOT_APPLICABLE",
                "No supported verification evidence"
                if applies
                else "RTL-only runs have no physical layout",
                policy,
                run_id,
            )
    for name in policy.required:
        if rows[name]["status"] in {"UNKNOWN", "NOT_APPLICABLE"}:
            # Built-in physical requirements cannot be waived by supplying an RTL-only directory.
            rows[name].update(
                status="BLOCKED", applicable=True, applicability="Required by the selected profile"
            )
    objectives = objective_rows(evidence, policy, run_id)
    for row in objectives:
        gate = row["gate"]
        for check in ("setup", "hold"):
            if gate == f"objective.{check}_worst_slack_ns" and rows[check]["status"] == "BLOCKED":
                row.update(status="BLOCKED", diagnostic=rows[check]["diagnostic"])
        rows[row["gate"]] = row
    required = [rows[name]["status"] for name in policy.required]
    objective = verdict([row["status"] for row in objectives])
    impl = implementation(evidence)
    physical = (
        verdict([rows[name]["status"] for name in (*PHYSICAL, "artifact_integrity")])
        if evidence
        else "NOT_APPLICABLE"
    )
    physical_profile = any(name in PHYSICAL for name in policy.required)
    full = verdict([*required, objective, impl] if physical_profile else [*required, objective])
    matrix = {
        "schema_version": 1,
        "run_id": run_id,
        "design": top,
        "profile": policy.profile,
        "profile_sha256": policy.digest,
        "attempt": index["attempt"] if index else None,
        "toolchain": evidence["toolchain"] if evidence else {},
        "timing_context": context,
        "run_inputs": {
            "rtl_identity": evidence["rtl_identity"],
            "manifest": evidence["input_manifest"],
            "configuration_sha256": evidence["configuration_sha256"],
        }
        if evidence
        else {"rtl_identity": run_id},
        "verdicts": {
            "implementation_completion": impl,
            "verification": verdict(required),
            "physical_feasibility": physical,
            "optimization_objectives": objective,
            "full_acceptance": full,
        },
        "gates": rows,
    }
    return {**matrix, "decision_sha256": fingerprint(matrix)}


def report_run(root: Path, profile: str | None = None) -> dict[str, Any]:
    root = root.resolve()
    if not root.is_dir():
        raise ValueError("verification report requires an existing run directory")
    index = selection(root)
    policy = policy_at(root, profile or (index["profile"] if index else "full"))
    if index and index["profile"] != policy.profile:
        index = None
    with TemporaryDirectory(prefix="aeda-acceptance-") as directory:
        snapshot = Path(directory)
        evidence = physical_snapshot(root, snapshot)
        result = evaluate(root, evidence, snapshot, policy, index)
    latest = selection(root)
    if latest and latest["profile"] != policy.profile:
        latest = None
    if index != latest:
        raise ValueError("Verification attempt changed during evaluation; retry report")
    return result


def markdown(report: dict[str, Any]) -> str:
    def cell(text: object) -> str:
        return str(text).replace("|", "\\|").replace("\n", " ").replace("<", "&lt;")

    lines = [
        f"# Verification for {cell(report['design'] or report['run_id'])}",
        "",
        f"Run `{report['run_id']}` · Profile `{report['profile']}` · "
        f"Decision `{report['decision_sha256']}`",
        "",
    ]
    lines.extend(
        f"- {name.replace('_', ' ').capitalize()} **{state}**"
        for name, state in report["verdicts"].items()
    )
    lines.extend(
        [
            "",
            "| Gate | Requirement | Outcome | Evidence and diagnosis |",
            "| --- | --- | --- | --- |",
        ]
    )
    for gate in report["gates"].values():
        detail = cell(gate["diagnostic"])
        if gate["evidence_references"]:
            detail += f" ({len(gate['evidence_references'])} evidence references)"
        lines.append(f"| {gate['gate']} | {gate['requirement']} | {gate['status']} | {detail} |")
    return "\n".join(lines) + "\n"

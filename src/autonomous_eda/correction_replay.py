"""Plan, review and replay an isolated snapshot after a recorded renderer crash."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .diagnostic_review import digest
from .evidence import get_experiment
from .manifest import sha256
from .stage_snapshots import CHECKPOINTS, export, local_input
from .verification.report import atomic


def record(path: Path) -> dict[str, Any]:
    with path.open("rb") as stream:
        raw = stream.read(16 * 1024**2 + 1)
    if len(raw) > 16 * 1024**2:
        raise ValueError("correction record exceeds 16 MiB")
    value = json.loads(raw)
    if not isinstance(value, dict):
        raise ValueError("expected a correction record")
    return value


def event(state: str) -> dict[str, str]:
    return {"state": state, "timestamp": datetime.now(UTC).isoformat()}


def checkpoint(run: Path, stage: str) -> dict[str, str]:
    paths = dict(CHECKPOINTS)
    if stage not in paths or stage == "gds":
        raise ValueError("correction replay requires one recorded OpenDB checkpoint stage")
    evidence = get_experiment(run)
    if evidence["provenance"]["input_error"]:
        raise ValueError("controller inputs are stale")
    attempt = next((a for a in reversed(evidence["attempts"]) if a["stage"] == stage), None)
    if not attempt or attempt["state"] != "passed":
        raise ValueError("the latest checkpoint attempt did not pass")
    candidates = [
        (path, artifact["sha256"])
        for path, artifact in evidence["artifacts"].items()
        if Path(path).name == paths[stage]
        and artifact["stage"] == stage
        and artifact["attempt"] == str(attempt["id"])
        and artifact["availability"] == "VERIFIED"
    ]
    if len(candidates) != 1:
        raise ValueError("the stage does not have one verified checkpoint")
    source, checksum = candidates[0]
    if checksum is None:
        raise ValueError("the recorded checkpoint has no source digest")
    return {
        "run_id": evidence["run_id"],
        "top": evidence["input_manifest"]["top"],
        "attempt": str(attempt["id"]),
        "path": source,
        "sha256": checksum,
    }


def plan(
    bundle: Path, log_root: Path, run: Path, review: Path, output: Path, stage: str, width: int
) -> dict[str, Any]:
    bundle, log_root, run = (item.resolve(strict=True) for item in (bundle, log_root, run))
    review, output = review.resolve(), output.resolve()
    if output.exists() or output.is_relative_to(run):
        raise ValueError("choose a fresh snapshot output directory outside the retained run")
    if not 128 <= width <= 2048:
        raise ValueError("PNG width must be between 128 and 2048")
    diagnosis = local_input(bundle, "diagnostics.json")
    diagnosis_sha256 = sha256(diagnosis)
    findings = record(diagnosis)
    if sha256(diagnosis) != diagnosis_sha256:
        raise ValueError("diagnosis changed during review")
    proposal = next(
        (
            item
            for item in findings["proposals"]
            if item["code"] == "OPENROAD_PROCESS_SIGNAL" and item["stage"] == stage
        ),
        None,
    )
    if proposal is None:
        raise ValueError("the stage has no renderer crash correction")
    binding = {key: value for key, value in proposal.items() if key != "approval_digest"}
    if digest(binding) != proposal["approval_digest"]:
        raise ValueError("the diagnosis proposal changed")
    log = local_input(log_root, proposal["log"])
    log_sha256 = sha256(log)
    if log_sha256 != proposal["log_receipt"]["prefix_sha256"]:
        raise ValueError("the retained log does not match the full diagnosed log")
    inputs = {
        "diagnosis": str(diagnosis),
        "diagnosis_sha256": diagnosis_sha256,
        "log_root": str(log_root),
        "log": proposal["log"],
        "log_sha256": log_sha256,
        "run": str(run),
        "checkpoint": checkpoint(run, stage),
    }
    action = {"kind": "isolated_snapshot", "stage": stage, "width": width, "output": str(output)}
    value = {"schema_version": 1, "inputs": inputs, "action": action}
    result = {
        **value,
        "plan_sha256": digest(value),
        "events": [event(state) for state in ("OBSERVED", "DIAGNOSED", "PROPOSED")],
    }
    review.mkdir(parents=True, exist_ok=False)
    atomic(review / "plan.json", result)
    return result


def load_plan(review: Path, expected: str) -> dict[str, Any]:
    value = record(local_input(review.resolve(strict=True), "plan.json"))
    payload = {key: value[key] for key in ("schema_version", "inputs", "action")}
    if value["plan_sha256"] != expected or digest(payload) != expected:
        raise ValueError("correction plan digest does not match")
    return value


def review_plan(review: Path, expected: str, *, decision: str) -> dict[str, Any]:
    value = load_plan(review, expected)
    if decision not in ("APPROVED", "REJECTED"):
        raise ValueError("decision must approve or reject the exact plan")
    result = {
        "schema_version": 1,
        "plan_sha256": expected,
        "state": decision,
        "events": [*value["events"], event("REVIEWED"), event(decision)],
    }
    with (review / "decision.json").open("x", encoding="utf-8", newline="\n") as stream:
        json.dump(result, stream, indent=2)
        stream.write("\n")
    return result


def execute_plan(review: Path, expected: str) -> dict[str, Any]:
    review = review.resolve(strict=True)
    value = load_plan(review, expected)
    if not (review / "decision.json").is_file():
        raise ValueError("correction execution requires the exact approved plan")
    decision = record(local_input(review, "decision.json"))
    if decision["plan_sha256"] != expected or decision["state"] != "APPROVED":
        raise ValueError("correction execution requires the exact approved plan")
    action, inputs = value["action"], value["inputs"]
    if action["kind"] != "isolated_snapshot":
        raise ValueError("unsupported corrective action")
    run, stage, output = Path(inputs["run"]), action["stage"], Path(action["output"])
    actual = checkpoint(run, stage)
    if (
        actual != inputs["checkpoint"]
        or sha256(Path(inputs["diagnosis"])) != inputs["diagnosis_sha256"]
        or sha256(local_input(Path(inputs["log_root"]), inputs["log"])) != inputs["log_sha256"]
    ):
        raise ValueError("approved correction inputs changed")
    with (review / "execution.claim").open("x", encoding="utf-8", newline="\n") as claim:
        claim.write(expected + "\n")
    result: dict[str, Any] = {
        "schema_version": 1,
        "plan_sha256": expected,
        "state": "EXECUTED",
        "events": [*decision["events"], event("EXECUTED")],
    }
    try:
        bundle = export(
            run,
            output,
            selections=[(stage, actual["path"])],
            run_id=actual["run_id"],
            lefs=[],
            width=action["width"],
            top=actual["top"],
        )
        stage_data = bundle["stages"][stage]
        if stage_data["status"] != "rendered":
            raise ValueError(f"isolated renderer returned {stage_data['status']}")
        if stage_data["provenance"]["source"]["sha256"] != actual["sha256"]:
            raise ValueError("renderer used a different checkpoint")
        for kind in ("png", "geometry"):
            if sha256(local_input(output, stage_data[kind])) != stage_data[kind + "_sha256"]:
                raise ValueError("renderer output changed")
        result.update(
            state="VERIFIED",
            snapshot_sha256=sha256(output / "snapshots.json"),
            stage=stage_data,
        )
        result["events"].append(event("VERIFIED"))
    except (OSError, ValueError, KeyboardInterrupt) as error:
        result["error"] = str(error) or "execution interrupted"
        if isinstance(error, KeyboardInterrupt):
            atomic(review / "execution.json", result)
            raise
    atomic(review / "execution.json", result)
    return result

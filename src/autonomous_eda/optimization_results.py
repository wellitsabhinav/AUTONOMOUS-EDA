"""Evaluate independent gates and controlled optimization objectives."""

from dataclasses import asdict, dataclass
from typing import Any

from .diagnostic_artifacts import STAGE_REPORTS
from .evidence import Experiment, GateState
from .manifest import fingerprint
from .metrics import Number
from .optimization_policy import FULL, OBJECTIVES, PHYSICAL, Plan, candidate_manifest
from .reference import IMAGE, REVISION


@dataclass(frozen=True)
class Outcome:
    execution_completed: bool
    physical_feasibility: str
    full_acceptance: GateState
    gates: dict[str, GateState]
    excluded_physical_gates: tuple[str, ...]
    comparable: bool
    incompatibilities: tuple[str, ...]
    measured: dict[str, Number | None]
    differences: dict[str, dict[str, Number | None]]
    missing_objectives: tuple[str, ...]
    evidence: dict[str, Any]


def verdict(gates: dict[str, GateState], required: tuple[str, ...]) -> GateState:
    states = [gates.get(name, "NOT_CHECKED") for name in required]
    if "FAILED" in states:
        return "FAILED"
    return "PASSED" if all(state == "PASSED" for state in states) else "BLOCKED"


def metric(evidence: Experiment, name: str) -> Number | None:
    if name == "runtime_seconds":
        rows = [
            item["value"]
            for key, item in evidence["metrics"].items()
            if key.startswith(("stage_runtime.", "verification_runtime."))
            and item["status"] == "MEASURED"
            and item["value"] is not None
        ]
        return sum(rows) if rows else None
    entry = evidence["metrics"].get(name)
    return entry["value"] if entry and entry["status"] == "MEASURED" else None


def evaluate(
    plan: Plan,
    baseline: Experiment,
    evidence: Experiment,
    changes: dict[str, int] | None = None,
    *,
    attested: bool = False,
    run_bound: bool = False,
) -> Outcome:
    base = plan.baseline
    gates = {key: gate["state"] for key, gate in evidence["verification_gates"].items()}
    history = {item["stage"]: item["state"] for item in evidence["attempts"]}
    completed = all(history.get(spec.stage) == "passed" for spec in STAGE_REPORTS)
    if not history and evidence["origin"] == "external":
        # A retained reference can prove terminal output while lacking stage checkpoints.
        completed = gates.get("gds_generation") == gates.get("gds_reopen") == "PASSED"
    gates["physical_stages"] = (
        "PASSED"
        if completed and history
        else ("FAILED" if any(state == "failed" for state in history.values()) else "NOT_CHECKED")
    )
    gates["artifact_integrity"] = (
        "PASSED"
        if evidence["artifacts"]
        and all(item["availability"] == "VERIFIED" for item in evidence["artifacts"].values())
        else "BLOCKED"
    )
    issues = [
        key
        for key in ("design", "platform", "source_revision", "corner")
        if evidence[key] != baseline[key]
    ]
    if baseline["toolchain"] != evidence["toolchain"]:
        issues.append("toolchain")
    if baseline["required_gates"] != evidence["required_gates"]:
        issues.append("verification_profile")
    sdc = evidence["artifacts"].get(f"results/{base.platform}/{base.design}/base/1_synth.sdc")
    if (
        sdc is None
        or sdc["availability"] != "VERIFIED"
        or sdc["sha256"] != base.sdc_sha256
        or metric(evidence, "clock_period_ns") != base.clock
    ):
        issues.append("constraints")
    if changes is not None and (
        evidence["rtl_identity"] != fingerprint([IMAGE, base.config, REVISION])
    ):
        issues.append("rtl_identity")
    if changes is not None:
        manifest = candidate_manifest(plan, changes)
        if (
            asdict(manifest) != evidence["input_manifest"]
            or evidence["origin"] != "controller"
            or evidence["provenance"]["memory_mib"] != plan.budget.maximum_memory
            or evidence["provenance"]["input_error"]
            or not attested
            or not run_bound
        ):
            issues.append("input_identity")
    elif evidence["run_id"] != base.run_id:
        issues.append("baseline_identity")
    gates["input_identity"] = "BLOCKED" if issues else "PASSED"
    names = tuple(dict.fromkeys((*OBJECTIVES, "hold_worst_slack_ns", "instance_count")))
    measured = {name: metric(evidence, name) for name in names}
    differences: dict[str, dict[str, Number | None]] = {}
    for name, value in measured.items():
        old = metric(baseline, name)
        first, second = baseline["metrics"].get(name), evidence["metrics"].get(name)
        compatible = name == "runtime_seconds" or (
            first is not None
            and second is not None
            and all(
                first[key] == second[key]
                for key in ("unit", "stage", "corner", "method", "reference")
            )
        )
        if not issues and compatible and old is not None and value is not None:
            delta = value - old
            differences[name] = {
                "absolute": delta,
                "percent": 100 * delta / old
                if old > 0
                and name
                not in {
                    "setup_worst_slack_ns",
                    "hold_worst_slack_ns",
                }
                else None,
            }
    absent = tuple(name for name in plan.objectives if name not in differences)
    physical = verdict(gates, PHYSICAL)
    full = verdict(gates, FULL)
    if full == "PASSED" and absent:
        full = "BLOCKED"
    selected = {name: evidence["metrics"][name] for name in names if name in evidence["metrics"]}
    return Outcome(
        completed,
        {"PASSED": "FEASIBLE", "FAILED": "INFEASIBLE", "BLOCKED": "UNVERIFIED"}[physical],
        full,
        gates,
        tuple(name for name in FULL if name not in PHYSICAL),
        not issues,
        tuple(issues),
        measured,
        differences,
        absent,
        {
            "run_id": evidence["run_id"],
            "metrics": selected,
            "verification": evidence["verification_gates"],
            "artifact_sha256": {
                path: item["sha256"]
                for path, item in evidence["artifacts"].items()
                if item["availability"] == "VERIFIED"
            },
        },
    )


def frontier(results: dict[str, Outcome], objectives: tuple[str, ...], profile: str) -> list[str]:
    if profile not in {"physical", "full"}:
        raise ValueError("Unknown verification profile")
    eligible = {
        name: item
        for name, item in results.items()
        if item.comparable
        and not item.missing_objectives
        and all(item.measured.get(key) is not None for key in objectives)
        and (
            item.physical_feasibility == "FEASIBLE"
            if profile == "physical"
            else item.full_acceptance == "PASSED"
        )
    }

    def dominates(a: Outcome, b: Outcome) -> bool:
        left = []
        right = []
        for name in objectives:
            sign = 1 if OBJECTIVES[name] == "max" else -1
            x, y = a.measured[name], b.measured[name]
            assert x is not None and y is not None
            left.append(sign * x)
            right.append(sign * y)
        return all(x >= y for x, y in zip(left, right, strict=True)) and left != right

    return [
        name
        for name, result in eligible.items()
        if not any(dominates(other, result) for other in eligible.values())
    ]

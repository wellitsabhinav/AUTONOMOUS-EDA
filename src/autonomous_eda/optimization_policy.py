"""Pinned input identity, plan validation and reproducible candidate generation."""

from __future__ import annotations

import itertools
import random
import re
from collections.abc import Mapping
from dataclasses import asdict, dataclass
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any, Literal

from .diagnosis import diagnose_source
from .evidence import GATES, SKY130_LIBRARY, Experiment
from .manifest import ORFSManifest, fingerprint
from .mcp.snapshot import Source, load_evidence
from .reference import IMAGE, REVISION

PHYSICAL = (
    "input_identity",
    "artifact_integrity",
    "physical_stages",
    "synthesis",
    "gds_generation",
    "gds_reopen",
    "setup",
    "hold",
    "router",
    "platform_drc",
    "lvs",
)
FULL = tuple(dict.fromkeys((*PHYSICAL, *GATES)))
OBJECTIVES = {
    "setup_worst_slack_ns": "max",
    "standard_cell_area_um2": "min",
    "estimated_power_w": "min",
    "routed_wirelength_um": "min",
    "global_route_total_overflow": "min",
    "runtime_seconds": "min",
}
CONFIG_HASHES = {
    "gcd": "c43fb747fc0ebea5c787bd9a9ddf9a2b3b2319d67b17ba77aca81a83e99f7ef0",
    "ibex": "80d78b6e1f79e4e98cab824eeecdaf694bd9b212ac247b7e81cf34586cdf8b9a",
}
PROTECTED = {
    "scripts/variables.yaml": "460a34bd4e348b1062d72de46ddacaa56db83511b4238e2c3749f51ee6e71b66",
    "designs/src/gcd/gcd.v": "5009f224876d39e5e77e59ee1528cb0d2a03697251f0971b77cf3e8b0426dd3e",
    "platforms/sky130hd/config.mk": (
        "5d0f165f8f292cd2ec3c326905d5b942df8979a871aaf465c01d69e799d443bf"
    ),
    f"platforms/sky130hd/lib/{SKY130_LIBRARY}": (
        "ec0e1067a35c8bf20b11e58d1e8ac53326067e4dac84a125cc1b917a3518d0d9"
    ),
    "designs/sky130hd/gcd/config.mk": CONFIG_HASHES["gcd"],
    "designs/sky130hd/gcd/constraint.sdc": (
        "7dc2766d530b78c92e12c8e84462f8dade2f0bf2074e85580f7364976252767c"
    ),
}
Strategy = Literal["grid", "random", "guided", "paired"]


@dataclass(frozen=True)
class Parameter:
    name: str
    type: str
    minimum: int
    maximum: int
    baseline: int
    stage: str
    upstream: tuple[str, ...]
    protected_intent: bool
    compatibility: str
    restart: str


def registry(design: str) -> dict[str, Parameter]:
    if design not in CONFIG_HASHES:
        raise ValueError("Unsupported pinned design")
    return {
        "CORE_UTILIZATION": Parameter(
            "CORE_UTILIZATION",
            "integer",
            20,
            65,
            38 if design == "gcd" else 50,
            "floorplan",
            (
                f"{REVISION}/flow/scripts/variables.yaml",
                f"{REVISION}/flow/designs/sky130hd/{design}/config.mk",
            ),
            False,
            "Pinned automatic floorplan with no explicit die/core area override",
            "synth",
        )
    }


@dataclass(frozen=True)
class Budget:
    maximum_candidates: int = 4
    maximum_runtime: int = 3600
    candidate_seconds: int = 900
    maximum_memory: int = 1200
    maximum_concurrency: int = 1

    def __post_init__(self) -> None:
        for value, lower, upper in (
            (self.maximum_candidates, 1, 4),
            (self.maximum_runtime, 1, 3600),
            (self.candidate_seconds, 1, 900),
            (self.maximum_memory, 128, 16384),
            (self.maximum_concurrency, 1, 1),
        ):
            if type(value) is not int or not lower <= value <= upper:
                raise ValueError("Resource budget is outside the local optimization policy")


@dataclass(frozen=True)
class Baseline:
    run_id: str
    digest: str
    design: str
    platform: str
    top: str
    config: str
    cores: int
    clock: float
    corner: str
    sdc_sha256: str
    rtl_identity: str
    toolchain: dict[str, str | None]
    diagnosis_identity: str
    findings: tuple[dict[str, Any], ...]
    evidence: dict[str, Any]


def snapshot(root: Path) -> Experiment:
    with TemporaryDirectory(prefix="aeda-opt-") as temporary:
        return load_evidence(Source.register(root), Path(temporary))


def verified_hash(record: Experiment, path: str) -> str:
    item = record["artifacts"].get(path)
    if not item or item["availability"] != "VERIFIED" or not item["sha256"]:
        raise ValueError("Required input or artifact evidence is unavailable")
    return item["sha256"]


def identify_baseline(root: Path) -> tuple[Baseline, Experiment]:
    evidence = snapshot(root)
    design = evidence["design"]
    if (
        evidence["platform"] != "sky130hd"
        or design not in CONFIG_HASHES
        or evidence["toolchain"]["image"] != IMAGE
        or evidence["source_revision"] != REVISION
        or evidence["corner"] != "tt_025C_1v80"
        or evidence["toolchain"]["library"] != SKY130_LIBRARY
        or any(item["availability"] != "VERIFIED" for item in evidence["artifacts"].values())
    ):
        raise ValueError("Baseline integrity or technology identity could not be established")
    manifest = evidence["input_manifest"]
    config = f"designs/sky130hd/{design}/config.mk"
    top, clock = ("gcd", 1.1) if design == "gcd" else ("ibex_core", 8.0)
    if (
        manifest.get("config") != config
        or manifest.get("top") != top
        or manifest.get("clock_period_ns") != clock
        or manifest.get("cores") != 2
        or manifest.get("core_utilization") is not None
        or evidence["metrics"]["clock_period_ns"]["value"] != clock
    ):
        raise ValueError("Baseline must use the pinned design, clock and upstream configuration")
    if evidence["origin"] == "external":
        index = evidence["provenance"]["recorded_by"]
        if index not in {"controller", "upstream ORFS make"}:
            raise ValueError("Reference baseline has an unsupported provenance")
    sdc = verified_hash(evidence, f"results/sky130hd/{design}/base/1_synth.sdc")
    audited = {
        "gcd": "1da6837b5ccc68d322143f103895c0b77ca89fd3f6f74c13e5c97c3b0dafbf16",
        "ibex": "c2d0ca6ceac789545e23a304265262d22279309d0f83a292ca4e5c63ea854476",
    }
    if sdc != audited[design]:
        raise ValueError("Baseline synthesis constraints differ from the audited reference")
    diagnosis = diagnose_source(design, Source.register(root))
    if diagnosis.integrity_status != "VERIFIED" or diagnosis.run_id != evidence["run_id"]:
        raise ValueError("Baseline changed during diagnosis")
    hashes = {key: item["sha256"] for key, item in evidence["artifacts"].items()}
    rtl = fingerprint([IMAGE, REVISION, config])
    if evidence["rtl_identity"] is not None and evidence["rtl_identity"] != fingerprint(
        [IMAGE, config, REVISION]
    ):
        raise ValueError("Baseline RTL identity is incompatible")
    findings = tuple(
        asdict(item)
        for item in (*diagnosis.observed_findings, *diagnosis.derived_findings)
        if item.id.startswith("path_")
        or item.id
        in {
            "place_worst",
            "cts_worst",
            "grt_worst",
            "finish_worst",
            "finish_aggregate_delta",
        }
    )[:10]
    cited = {ref for item in findings for ref in item["evidence"]}
    return Baseline(
        evidence["run_id"],
        fingerprint([evidence["run_id"], manifest, hashes, evidence["verification_gates"]]),
        design,
        "sky130hd",
        top,
        config,
        2,
        clock,
        evidence["corner"],
        sdc,
        rtl,
        evidence["toolchain"],
        diagnosis.evidence_identity,
        findings,
        {key: asdict(diagnosis.evidence_references[key]) for key in sorted(cited)},
    ), evidence


@dataclass(frozen=True)
class Plan:
    schema_version: int
    optimization_id: str
    baseline: Baseline
    run_root: str
    strategy: Strategy
    seed: int
    grid: dict[str, tuple[int, ...]]
    budget: Budget
    objectives: tuple[str, ...]
    model: str = ""
    endpoint: str = ""

    @property
    def digest(self) -> str:
        return fingerprint(self.body())

    def body(self) -> dict[str, Any]:
        return {
            **asdict(self),
            "authorization_scope": "local-gcd"
            if self.baseline.design == "gcd"
            else "planning-only",
            "technology_identity": self.baseline.toolchain["library"],
            "constraint_identity": self.baseline.sdc_sha256,
            "original_clock_targets": {"core_clock_ns": self.baseline.clock},
            "allowed_parameters": {
                key: asdict(value) for key, value in registry(self.baseline.design).items()
            },
            "required_verification_gates": {"physical": PHYSICAL, "full": FULL},
            "stop_criteria": ["candidate_limit", "finite_space", "deadline", "cancel", "integrity"],
        }


def validate_changes(design: str, changes: Mapping[str, object]) -> dict[str, int]:
    parameters = registry(design)
    if set(changes) != set(parameters):
        raise ValueError("Unsupported parameter or combination")
    values = {}
    for name, value in changes.items():
        spec = parameters[name]
        if type(value) is not int or not spec.minimum <= value <= spec.maximum:
            raise ValueError("Parameter value is outside the approved integer range")
        if value == spec.baseline:
            raise ValueError("Candidate repeats the baseline")
        values[name] = value
    return values


def validate_plan(plan: Plan) -> None:
    from .ai.gateway import Route

    if bool(plan.model) != bool(plan.endpoint):
        raise ValueError("A local model requires a loopback endpoint")
    if plan.model:
        Route("local", plan.model, plan.endpoint)
    candidate_grid(plan)


def candidate_grid(plan: Plan) -> list[dict[str, int]]:
    if (
        plan.strategy not in {"grid", "random", "guided", "paired"}
        or type(plan.seed) is not int
        or not -(2**31) <= plan.seed < 2**31
    ):
        raise ValueError("Unsupported search strategy or seed")
    if (
        not plan.objectives
        or len(set(plan.objectives)) != len(plan.objectives)
        or set(plan.objectives) - OBJECTIVES.keys()
    ):
        raise ValueError("Select unique supported measured objectives")
    if set(plan.grid) != set(registry(plan.baseline.design)):
        raise ValueError("Unsupported grid parameter combination")
    names = sorted(plan.grid)
    choices = [list(dict.fromkeys(plan.grid[name])) for name in names]
    if any(not values or len(values) > 46 for values in choices):
        raise ValueError("Invalid candidate grid size")
    result = [
        validate_changes(plan.baseline.design, dict(zip(names, values, strict=True)))
        for values in itertools.product(*choices)
    ]
    if plan.strategy == "random":
        random.Random(plan.seed).shuffle(result)
    return result


def candidate_id(plan: Plan, changes: dict[str, int]) -> str:
    return fingerprint([plan.digest, changes])[:24]


def candidate_manifest(plan: Plan, changes: dict[str, int]) -> ORFSManifest:
    selected = validate_changes(plan.baseline.design, changes)
    base = plan.baseline
    return ORFSManifest(
        base.top,
        base.design,
        base.platform,
        base.config,
        REVISION,
        base.clock,
        base.cores,
        plan.budget.candidate_seconds,
        core_utilization=selected["CORE_UTILIZATION"],
    )


def load_plan(data: dict[str, Any], base: Baseline, run_root: Path) -> Plan:
    plan = Plan(
        1,
        data["optimization_id"],
        base,
        str(run_root),
        data["strategy"],
        data["seed"],
        {key: tuple(values) for key, values in data["grid"].items()},
        Budget(**data["budget"]),
        tuple(data["objectives"]),
        data["model"],
        data["endpoint"],
    )
    if not isinstance(plan.optimization_id, str) or not re.fullmatch(
        "[0-9a-f]{32}", plan.optimization_id
    ):
        raise ValueError("Invalid campaign ID")
    if fingerprint(data) != plan.digest:
        raise ValueError("Plan or baseline evidence changed")
    validate_plan(plan)
    return plan

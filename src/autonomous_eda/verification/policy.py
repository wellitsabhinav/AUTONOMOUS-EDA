"""Versioned operator policy for the verification sidecar."""

from __future__ import annotations

import math
import re
import tomllib
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Literal

from ..evidence import GATES
from ..manifest import fingerprint, identifier, integer, relative_path
from ..metrics import Number

State = Literal["PASSED", "FAILED", "BLOCKED", "NOT_APPLICABLE", "UNKNOWN"]
PHYSICAL = (
    "synthesis",
    "setup",
    "hold",
    "timing_coverage",
    "gds_generation",
    "gds_reopen",
    "router",
    "platform_drc",
    "lvs",
)
RTL = ("elaboration", "functional_regression", "equivalence")
ALL_GATES = (*GATES, "artifact_integrity", "sequential_equivalence", "formal_properties")
PROFILES = {
    "rtl": ("elaboration", "functional_regression", "artifact_integrity"),
    "rtl-formal": (*RTL, "artifact_integrity"),
    "physical": (*PHYSICAL, "artifact_integrity"),
    "full": (*GATES, "artifact_integrity"),
}
OBJECTIVES = {
    "standard_cell_area_um2": "max",
    "estimated_power_w": "max",
    "routed_wirelength_um": "max",
    "setup_worst_slack_ns": "min",
    "hold_worst_slack_ns": "min",
}


@dataclass(frozen=True)
class Simulation:
    testbench: str
    top: str
    pass_marker: str
    fail_marker: str


@dataclass(frozen=True)
class Equivalence:
    reference: str
    correspondence: Literal["exact_ports"] = "exact_ports"


@dataclass(frozen=True)
class Timing:
    corner: str | None = None
    sdc_sha256: str | None = None
    clocks: tuple[str, ...] = ()


@dataclass(frozen=True)
class Policy:
    profile: str
    require: tuple[str, ...] = ()
    timeout_seconds: int = 60
    simulation: Simulation | None = None
    equivalence: Equivalence | None = None
    timing: Timing = Timing()
    objectives: tuple[tuple[str, Number], ...] = ()

    @property
    def required(self) -> tuple[str, ...]:
        return tuple(dict.fromkeys((*PROFILES[self.profile], *self.require)))

    @property
    def digest(self) -> str:
        return fingerprint({"schema_version": 1, **asdict(self)})


def table(value: object, fields: set[str], label: str) -> dict[str, object]:
    if not isinstance(value, dict) or set(value) - fields:
        raise ValueError(f"invalid {label} fields")
    return value


def marker(value: object) -> str:
    if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z][A-Za-z0-9_-]{2,63}", value):
        raise ValueError("oracle markers must be simple tokens of 3 to 64 characters")
    return value


def load_policy(profile: str, path: Path | None) -> Policy:
    if profile not in PROFILES:
        raise ValueError(f"unknown verification profile: {profile}")
    if path is None:
        return Policy(profile)
    with path.open("rb") as stream:
        raw = stream.read(64 * 1024 + 1)
    if len(raw) > 64 * 1024:
        raise ValueError("verification policy exceeds its size limit")
    data = table(
        tomllib.loads(raw.decode("utf-8")),
        {
            "schema_version",
            "require",
            "timeout_seconds",
            "simulation",
            "equivalence",
            "timing",
            "objectives",
        },
        "verification policy",
    )
    if type(data.get("schema_version")) is not int or data["schema_version"] != 1:
        raise ValueError("verification policy requires schema_version = 1")
    required = data.get("require", [])
    if (
        not isinstance(required, list)
        or any(not isinstance(item, str) or item not in ALL_GATES for item in required)
        or len(set(required)) != len(required)
    ):
        raise ValueError("require must list distinct known gates")
    simulation = None
    if "simulation" in data:
        sim = table(
            data["simulation"], {"testbench", "top", "pass_marker", "fail_marker"}, "simulation"
        )
        testbench = relative_path(sim.get("testbench"), "testbench", (".v", ".sv"))
        passed, failed = marker(sim.get("pass_marker")), marker(sim.get("fail_marker"))
        if passed == failed:
            raise ValueError("oracle pass and fail markers must differ")
        simulation = Simulation(
            str(testbench), identifier(sim.get("top"), "testbench top"), passed, failed
        )
    equivalence = None
    if "equivalence" in data:
        proof = table(data["equivalence"], {"reference", "correspondence"}, "equivalence")
        if proof.get("correspondence") != "exact_ports":
            raise ValueError("equivalence requires an explicit exact_ports correspondence")
        equivalence = Equivalence(
            str(relative_path(proof.get("reference"), "reference", (".toml",)))
        )
    raw_timing = table(data.get("timing", {}), {"corner", "sdc_sha256", "clocks"}, "timing")
    corner, digest = raw_timing.get("corner"), raw_timing.get("sdc_sha256")
    if corner is not None:
        corner = identifier(corner, "timing corner")
    if digest is not None and (
        not isinstance(digest, str) or not re.fullmatch(r"[0-9a-f]{64}", digest)
    ):
        raise ValueError("timing.sdc_sha256 must be a lowercase SHA-256 digest")
    clocks = raw_timing.get("clocks", [])
    if not isinstance(clocks, list):
        raise ValueError("timing.clocks must be an array")
    names = tuple(identifier(name, "clock") for name in clocks)
    if len(set(names)) != len(names):
        raise ValueError("duplicate timing clock")
    objectives: list[tuple[str, Number]] = []
    for name, value in sorted(
        table(data.get("objectives", {}), set(OBJECTIVES), "objectives").items()
    ):
        if (
            not isinstance(value, (float, int))
            or isinstance(value, bool)
            or not math.isfinite(value)
        ):
            raise ValueError(f"invalid objective bound for {name}")
        objectives.append((name, value))
    return Policy(
        profile,
        tuple(required),
        integer(data.get("timeout_seconds", 60), "timeout_seconds", 900),
        simulation,
        equivalence,
        Timing(corner, digest, names),
        tuple(objectives),
    )


def verdict(states: list[State]) -> State:
    if "FAILED" in states:
        return "FAILED"
    if not states or all(state == "NOT_APPLICABLE" for state in states):
        return "NOT_APPLICABLE"
    if all(state in {"PASSED", "NOT_APPLICABLE"} for state in states):
        return "PASSED"
    return "BLOCKED"

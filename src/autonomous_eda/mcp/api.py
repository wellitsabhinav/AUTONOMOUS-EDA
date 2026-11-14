"""Typed, bounded projections of the controller's schema 1 evidence."""

import hashlib
import math
import re
from collections.abc import Sequence
from typing import Annotated, Literal, TypeVar

from pydantic import BaseModel, ConfigDict, Field

from ..evidence import Artifact, Experiment, GateState, Metric
from ..metrics import Number
from .registry import ID_PATTERN, Registry

Identifier = Annotated[str, Field(pattern=ID_PATTERN, max_length=64)]
ArtifactID = Annotated[str, Field(pattern=r"^[a-f0-9]{64}$", max_length=64)]
Offset = Annotated[int, Field(ge=0, le=1024)]
Limit = Annotated[int, Field(ge=1, le=50)]
STAGES = ("synth", "floorplan", "place", "cts", "grt", "route", "finish", "drc", "lvs")
STATES = {"running", "passed", "failed", "interrupted"}
PHYSICAL = (
    "standard_cell_area_um2",
    "instance_count",
    "global_route_total_overflow",
    "routed_wirelength_um",
    "router_drc_errors",
    "estimated_power_w",
    "klayout_drc_report_value_count",
)
TIMING = ("setup_worst_slack_ns", "setup_tns_ns", "hold_worst_slack_ns", "hold_tns_ns")


class Model(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class NoArgs(Model):
    pass


class PageArgs(Model):
    offset: Offset = 0
    limit: Limit = 20


class ExperimentArgs(Model):
    experiment_id: Identifier


class ExperimentPageArgs(ExperimentArgs, PageArgs):
    pass


class TimingArgs(ExperimentArgs):
    stage: Literal["finish"] = "finish"
    corner: Literal["tt_025C_1v80"] | None = None


class ArtifactArgs(ExperimentArgs):
    artifact_id: ArtifactID


class ServerInfo(Model):
    application_version: str
    sdk_version: str
    protocol_version: str
    evidence_schema_version: int
    transport: Literal["stdio"] = "stdio"
    capabilities: list[str]
    configured_roots: list[str]
    read_only: Literal[True] = True


class ExperimentList(Model):
    experiment_ids: list[Identifier]
    next_offset: int | None


class Summary(Model):
    experiment_id: Identifier
    run_id: str
    design: str
    platform: str
    top: str | None
    source_revision: str | None
    toolchain: dict[str, str | None]
    status: str
    stages: dict[str, str]
    acceptance: GateState
    evidence_availability: dict[str, int]


class Attempt(Model):
    id: int
    stage: str
    state: str
    duration_seconds: Number | None
    exit_code: int | None
    failure: str | None


class StageStatus(Model):
    stages: dict[str, str]
    attempts: list[Attempt]
    next_offset: int | None


class EvidenceRef(Model):
    artifact_id: ArtifactID
    sha256: ArtifactID | None
    availability: Literal["VERIFIED", "MISSING", "MODIFIED", "UNKNOWN"]


class ArtifactMetadata(EvidenceRef):
    type: str
    stage: str
    size: int | None


class ArtifactList(Model):
    artifacts: list[ArtifactMetadata]
    next_offset: int | None


class Measurement(Model):
    value: Number | None
    unit: str
    stage: str
    corner: str | None
    source_field: str | None
    evidence: EvidenceRef | None
    status: Literal["MEASURED", "UNKNOWN"]


class TimingSummary(Model):
    stage: Literal["finish"] = "finish"
    corner: str | None
    clock_target: Measurement
    metrics: dict[str, Measurement]


class PhysicalMetrics(Model):
    metrics: dict[str, Measurement]


class Gate(Model):
    state: GateState
    evidence: list[EvidenceRef]


class Gates(Model):
    acceptance: GateState
    gates: dict[str, Gate]


class AcceptanceSummary(Model):
    run_id: str
    profile: str
    decision_sha256: str
    verdicts: dict[str, str]
    gates: dict[str, str]


class Denied(ValueError):
    """An identifier is unavailable under this server's policy."""


class UnsupportedCorner(ValueError):
    """The requested corner does not match the recorded measurement."""


T = TypeVar("T")


def page(values: Sequence[T], args: PageArgs) -> tuple[Sequence[T], int | None]:
    end = args.offset + args.limit
    return values[args.offset : end], end if end < len(values) else None


def list_experiments(registry: Registry, args: PageArgs) -> ExperimentList:
    ids, next_offset = page(list(registry.experiments), args)
    return ExperimentList(experiment_ids=list(ids), next_offset=next_offset)


def known_token(value: object, pattern: str) -> str | None:
    return (
        value
        if isinstance(value, str) and len(value) <= 128 and re.fullmatch(pattern, value)
        else None
    )


def artifact_id(experiment_id: str, relative: str) -> str:
    return hashlib.sha256(f"{experiment_id}\0{relative}".encode()).hexdigest()


def reference(experiment_id: str, relative: str, artifact: Artifact | None) -> EvidenceRef:
    return EvidenceRef(
        artifact_id=artifact_id(experiment_id, relative),
        sha256=known_token(artifact["sha256"], r"[a-f0-9]{64}") if artifact else None,
        availability=artifact["availability"] if artifact else "UNKNOWN",
    )


def metadata(experiment_id: str, relative: str, artifact: Artifact) -> ArtifactMetadata:
    return ArtifactMetadata(
        **reference(experiment_id, relative, artifact).model_dump(),
        type=known_token(artifact["type"], r"\.[a-zA-Z0-9]{1,16}") or "unknown",
        stage=artifact["stage"] if artifact["stage"] in STAGES else "unknown",
        size=artifact["size"],
    )


def duration(value: object) -> Number | None:
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return value if math.isfinite(value) and value >= 0 else None
    return None


class View:
    def __init__(self, experiment_id: str, data: Experiment) -> None:
        self.id = experiment_id
        self.data = data

    def evidence(self, relative: str) -> EvidenceRef:
        return reference(self.id, relative, self.data["artifacts"].get(relative))

    def stages(self) -> dict[str, str]:
        return {
            a["stage"]: a["state"]
            for a in self.data["attempts"]
            if a["stage"] in STAGES and a["state"] in STATES
        }

    def summary(self) -> Summary:
        statuses = {"VERIFIED": 0, "MISSING": 0, "MODIFIED": 0, "UNKNOWN": 0}
        for artifact in self.data["artifacts"].values():
            statuses[artifact["availability"]] += 1
        stages = self.stages()
        if self.data["provenance"].get("input_error"):
            status = "blocked"
        elif self.data["origin"] == "external":
            status = "recorded"
        elif "running" in stages.values():
            status = "running"
        elif "failed" in stages.values():
            status = "failed"
        elif "interrupted" in stages.values():
            status = "interrupted"
        elif stages.get("finish") == "passed":
            status = "completed"
        else:
            status = "incomplete"
        toolchain = self.data["toolchain"]
        return Summary(
            experiment_id=self.id,
            run_id=known_token(self.data["run_id"], r"[a-f0-9]{32}|[a-f0-9]{64}") or self.id,
            design=self.data["design"],
            platform=self.data["platform"],
            top=known_token(self.data["input_manifest"].get("top"), r"[A-Za-z_][A-Za-z0-9_]*"),
            source_revision=known_token(self.data["source_revision"], r"[a-f0-9]{40}"),
            toolchain={
                key: known_token(toolchain.get(key), pattern)
                for key, pattern in {
                    "image": r"openroad/orfs@sha256:[a-f0-9]{64}",
                    "orfs_revision": r"[a-f0-9]{40}",
                    "library": r"sky130_fd_sc_hd__tt_025C_1v80\.lib",
                    "yosys": r"v?[0-9]+\.[0-9]+(?:\.[0-9]+)?(?:\+post)?",
                    "openroad": r"unknown|v?[0-9]+\.[0-9]+(?:-[0-9]+-g[a-f0-9]+)?",
                }.items()
            },
            status=status,
            stages=stages,
            acceptance=self.data["acceptance"],
            evidence_availability=statuses,
        )

    def stage_status(self, args: ExperimentPageArgs) -> StageStatus:
        attempts, next_offset = page(self.data["attempts"], args)
        return StageStatus(
            stages=self.stages(),
            attempts=[
                Attempt(
                    id=a["id"],
                    stage=a["stage"],
                    state=a["state"],
                    duration_seconds=duration(a.get("runtime_seconds")),
                    exit_code=a.get("exit_code"),
                    failure={"failed": "stage_failed", "interrupted": "stage_interrupted"}.get(
                        a["state"]
                    ),
                )
                for a in attempts
                if a["stage"] in STAGES and a["state"] in STATES
            ],
            next_offset=next_offset,
        )

    def measurement(self, metric: Metric) -> Measurement:
        path = metric["artifact"]
        ref = self.evidence(path) if path is not None else None
        measured = (
            metric["status"] == "MEASURED" and ref is not None and ref.availability == "VERIFIED"
        )
        return Measurement(
            value=metric["value"] if measured else None,
            unit=metric["unit"],
            stage=metric["stage"],
            corner="tt_025C_1v80" if metric["corner"] == "tt_025C_1v80" else None,
            source_field=metric["reference"],
            evidence=ref,
            status="MEASURED" if measured else "UNKNOWN",
        )

    def measurements(self, names: tuple[str, ...]) -> dict[str, Measurement]:
        return {name: self.measurement(self.data["metrics"][name]) for name in names}

    def timing(self, args: TimingArgs) -> TimingSummary:
        corner = "tt_025C_1v80" if self.data["corner"] == "tt_025C_1v80" else None
        if args.corner is not None and args.corner != corner:
            raise UnsupportedCorner
        return TimingSummary(
            corner=corner,
            clock_target=self.measurement(self.data["metrics"]["clock_period_ns"]),
            metrics=self.measurements(TIMING),
        )

    def physical(self) -> PhysicalMetrics:
        return PhysicalMetrics(metrics=self.measurements(PHYSICAL))

    def gates(self) -> Gates:
        return Gates(
            acceptance=self.data["acceptance"],
            gates={
                name: Gate(
                    state=gate["state"], evidence=[self.evidence(p) for p in gate["artifacts"]]
                )
                for name, gate in self.data["verification_gates"].items()
            },
        )

    def list_artifacts(self, args: ExperimentPageArgs) -> ArtifactList:
        entries, next_offset = page(sorted(self.data["artifacts"].items()), args)
        return ArtifactList(
            artifacts=[metadata(self.id, path, item) for path, item in entries],
            next_offset=next_offset,
        )

    def artifact(self, args: ArtifactArgs) -> ArtifactMetadata:
        for path, item in self.data["artifacts"].items():
            if artifact_id(self.id, path) == args.artifact_id:
                return metadata(self.id, path, item)
        raise Denied

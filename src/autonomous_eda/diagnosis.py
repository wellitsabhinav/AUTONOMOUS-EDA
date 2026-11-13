"""Read-only timing and physical diagnosis over verified controller and reference evidence."""

from __future__ import annotations

import hashlib
import json
import re
from collections import defaultdict
from dataclasses import dataclass, field
from decimal import Decimal
from enum import StrEnum
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Literal

from .diagnostic_artifacts import STAGE_REPORTS, StageReports
from .evidence import Experiment, GateState
from .knowledge.index import identity
from .knowledge.sources import decoded
from .manifest import identifier
from .mcp.registry import ID_PATTERN, Registry
from .mcp.snapshot import MAX_TEXT_BYTES, Snapshot, Source, collect_evidence, relative_parts
from .metrics import Number, number
from .reference import IMAGE, REVISION
from .timing import CHECKS, NUMBER, SYMBOL, TimingPath, parse_timing_report
from .timing_accounting import Breakdown, breakdown, cumulative_matches, launch_boundary

SCHEMA_VERSION = 1
Check = Literal[
    "setup", "hold", "recovery", "removal", "gating_setup", "gating_hold", "max_delay", "min_delay"
]
PHYSICAL_METRICS = {
    "design__instance__area__stdcell": ("standard_cell_area", "um2"),
    "design__instance__count": ("instance_count", "count"),
    "design__instance__count__stdcell": ("standard_cell_count", "count"),
    "design__instance__utilization": ("utilization", "fraction"),
    "design__instance__count__setup_buffer": ("setup_repair_buffers_reported", "count"),
    "design__instance__count__hold_buffer": ("hold_repair_buffers_reported", "count"),
    "design__instance__count__class:clock_buffer": ("clock_buffer_inventory", "count"),
    "design__instance__count__class:timing_repair_buffer": (
        "timing_repair_buffer_inventory",
        "count",
    ),
    "timing__drv__max_slew": ("max_slew_violations", "count"),
    "timing__drv__max_cap": ("max_capacitance_violations", "count"),
    "timing__drv__max_fanout": ("max_fanout_violations", "count"),
    "clock__skew__setup": ("reported_setup_skew", "ns"),
    "clock__skew__hold": ("reported_hold_skew", "ns"),
    "global_route__wirelength": ("global_route_wirelength", "um"),
    "route__wirelength": ("detailed_route_wirelength", "um"),
    "route__drc_errors": ("router_drc_errors", "count"),
    "flow__errors__count": ("flow_errors", "count"),
}
SDC_CLOCK = re.compile(
    rf"create_clock\s+-name\s+({SYMBOL})\s+-period\s+({NUMBER})(?:\s+\[get_ports \{{{SYMBOL}\}}\])?"
)
OVERFLOW = re.compile(r"Total\s+\d+\s+\d+\s+[\d.]+%\s+\d+\s*/\s*\d+\s*/\s*(\d+)")
EXCEPTION = re.compile(
    r"(?:set_(?:false_path|multicycle_path|min_delay|max_delay|clock_groups|disable_timing|case_analysis))\b"
)


class Category(StrEnum):
    LOGIC_DEPTH = "LOGIC_DEPTH"
    CELL_DELAY = "CELL_DELAY"
    NET_DELAY = "NET_DELAY"
    CLOCK_SKEW = "CLOCK_SKEW"
    CLOCK_UNCERTAINTY = "CLOCK_UNCERTAINTY"
    HIGH_FANOUT = "HIGH_FANOUT"
    SLEW_OR_CAPACITANCE = "SLEW_OR_CAPACITANCE"
    PLACEMENT_CONGESTION = "PLACEMENT_CONGESTION"
    ROUTING_EFFECT = "ROUTING_EFFECT"
    INPUT_OUTPUT_CONSTRAINT = "INPUT_OUTPUT_CONSTRAINT"
    UNCONSTRAINED_PATH = "UNCONSTRAINED_PATH"
    MULTICLOCK_OR_EXCEPTION = "MULTICLOCK_OR_EXCEPTION"
    MISSING_EVIDENCE = "MISSING_EVIDENCE"
    INSUFFICIENT_RESOLUTION = "INSUFFICIENT_RESOLUTION"
    UNKNOWN = "UNKNOWN"


class Strength(StrEnum):
    OBSERVED = "OBSERVED"
    DERIVED = "DERIVED"
    SUPPORTED_HYPOTHESIS = "SUPPORTED_HYPOTHESIS"
    WEAK_HYPOTHESIS = "WEAK_HYPOTHESIS"
    INSUFFICIENT_EVIDENCE = "INSUFFICIENT_EVIDENCE"


@dataclass(frozen=True)
class Citation:
    path: str
    sha256: str
    locator: str
    line_start: int | None = None
    line_end: int | None = None


@dataclass(frozen=True)
class Measurement:
    value: Number
    unit: str
    evidence: str


@dataclass(frozen=True)
class Constraints:
    sha256: str
    evidence: str
    clock_periods: dict[str, Measurement]
    exception_lines: tuple[int, ...]


@dataclass(frozen=True)
class PathDiagnosis:
    id: str
    stage: str
    corner: str | None
    constraint_identity: str | None
    path: TimingPath
    breakdown: Breakdown
    evidence: tuple[str, ...]


@dataclass(frozen=True)
class Gap:
    stage: str
    code: str
    description: str
    evidence: tuple[str, ...] = ()
    lines: tuple[int, int] | None = None


@dataclass(frozen=True)
class Finding:
    id: str
    category: Category
    strength: Strength
    text: str
    evidence: tuple[str, ...]


@dataclass(frozen=True)
class Investigation:
    id: str
    measurement: str
    why: str
    question: str
    tool_capability: str
    coverage: Literal["AVAILABLE", "PARTIAL", "UNAVAILABLE"]
    evidence: tuple[str, ...]


@dataclass
class StageTiming:
    stage: str
    corner: str | None
    constraints: Constraints | None
    worst_slack: Measurement | None
    wns_ns: Number | None
    delta_wns_ns: float | None = None
    delta_from: str | None = None
    delta_evidence: tuple[str, ...] = ()
    worst_path_ids: tuple[str, ...] = ()
    physical: dict[str, Measurement] = field(default_factory=dict)


@dataclass(frozen=True)
class PathTrend:
    earlier_stage: str
    later_stage: str
    earlier_path_id: str
    later_path_id: str
    endpoint_identity: tuple[str, ...]
    corner: str
    constraint_identity: str
    slack_delta_ns: float
    slack_tolerance_ns: float
    component_deltas: dict[str, DelayDelta]
    evidence: tuple[str, ...]
    launch_transitions: tuple[str | None, str | None]
    data_pin_sequence_equal: bool | None


@dataclass(frozen=True)
class DelayDelta:
    ns: float
    tolerance_ns: float
    earlier_lines: tuple[int, ...]
    later_lines: tuple[int, ...]


@dataclass
class DiagnosticReport:
    experiment_id: str
    run_id: str
    design: str
    platform: str
    check_type: Check
    timing_corner: str | None
    verification_summary: dict[str, GateState]
    acceptance: GateState
    schema_version: int = SCHEMA_VERSION
    constraint_identity: str | None = None
    critical_paths: list[PathDiagnosis] = field(default_factory=list)
    delay_breakdowns: dict[str, Breakdown] = field(default_factory=dict)
    stage_trends: list[StageTiming] = field(default_factory=list)
    matched_paths: list[PathTrend] = field(default_factory=list)
    observed_findings: list[Finding] = field(default_factory=list)
    derived_findings: list[Finding] = field(default_factory=list)
    hypotheses: list[Finding] = field(default_factory=list)
    missing_measurements: list[Gap] = field(default_factory=list)
    suggested_investigations: list[Investigation] = field(default_factory=list)
    evidence_references: dict[str, Citation] = field(default_factory=dict)
    verification_evidence: dict[str, tuple[str, ...]] = field(default_factory=dict)
    integrity_status: str = "VERIFIED"
    evidence_identity: str = ""


class Evidence:
    def __init__(self, experiment: Experiment, root: Path, result: DiagnosticReport) -> None:
        self.experiment = experiment
        self.root = root
        self.result = result
        self.latest_attempts = {item["stage"]: item for item in experiment["attempts"]}

    def read(self, path: str, stage: str) -> bytes | None:
        artifact = self.experiment["artifacts"].get(path)
        status: str = artifact["availability"] if artifact else "UNRECORDED"
        latest = self.latest_attempts.get(stage)
        if (
            artifact
            and latest
            and (latest["state"] != "passed" or artifact["attempt"] != str(latest["id"]))
        ):
            status = "STALE_STAGE"
        if status != "VERIFIED":
            self.result.missing_measurements.append(Gap(stage, status, path))
            if status in {"MODIFIED", "MISSING"}:
                self.result.integrity_status = "DEGRADED"
            return None
        assert artifact is not None
        try:
            with self.root.joinpath(*relative_parts(path)).open("rb") as stream:
                raw = stream.read(MAX_TEXT_BYTES + 1)
            if len(raw) > MAX_TEXT_BYTES or hashlib.sha256(raw).hexdigest() != artifact["sha256"]:
                raise ValueError("Text evidence failed integrity validation")
            return raw
        except (OSError, ValueError):
            self.result.missing_measurements.append(Gap(stage, "UNREADABLE", path))
            self.result.integrity_status = "DEGRADED"
            return None

    def text(self, path: str, stage: str) -> str | None:
        raw = self.read(path, stage)
        if raw is None:
            return None
        try:
            return decoded(raw)
        except ValueError:
            self.result.missing_measurements.append(Gap(stage, "UNREADABLE", path))
            return None

    def cite(
        self,
        path: str,
        locator: str,
        *,
        lines: list[str] | None = None,
        start: int | None = None,
        end: int | None = None,
    ) -> str:
        artifact = self.experiment["artifacts"].get(path)
        if not artifact or artifact["availability"] != "VERIFIED" or not artifact["sha256"]:
            raise ValueError("Citation requires a verified artifact")
        if lines is not None and (
            start is None
            or end is None
            or not 1 <= start <= end <= len(lines)
            or not lines[start - 1].strip()
            or not lines[end - 1].strip()
        ):
            raise ValueError("Invalid report-line citation")
        if lines is None and (start is not None or end is not None):
            raise ValueError("Line citation requires source lines")
        citation = Citation(path, artifact["sha256"], locator, start, end)
        ref = identity(citation.path, citation.sha256, locator, start, end)
        self.result.evidence_references[ref] = citation
        return ref


def read_constraints(evidence: Evidence, path: str, stage: str, *, ns: bool) -> Constraints | None:
    text = evidence.text(path, stage)
    if text is None:
        return None
    ref = evidence.cite(path, "recorded stage SDC")
    lines = text.splitlines()
    periods: dict[str, Measurement] = {}
    conflicting_clocks: set[str] = set()
    exceptions = []
    for line, raw in enumerate(lines, 1):
        text = raw.strip()
        if (match := SDC_CLOCK.fullmatch(text)) and ns:
            value = float(match[2])
            if 0 < value <= 10**9:
                name = match[1]
                if name in periods:
                    conflicting_clocks.add(name)
                periods[name] = Measurement(
                    value,
                    "ns",
                    evidence.cite(path, "create_clock", lines=lines, start=line, end=line),
                )
        if EXCEPTION.match(text):
            exceptions.append(line)
    return Constraints(
        evidence.result.evidence_references[ref].sha256,
        ref,
        {key: value for key, value in periods.items() if key not in conflicting_clocks},
        tuple(exceptions),
    )


def metric_pairs(pairs: list[tuple[str, object]]) -> dict[str, object]:
    """Only pinned detailed-route iterative counters use last-value semantics."""
    result: dict[str, object] = {}
    ambiguous: set[str] = set()
    for key, value in pairs:
        if (
            key in result
            and result[key] != value
            and key
            not in {
                "detailedroute__route__wirelength",
                "detailedroute__route__drc_errors",
            }
        ):
            ambiguous.add(key)
        result[key] = value
    return {key: value for key, value in result.items() if key not in ambiguous}


def read_metrics(
    evidence: Evidence,
    spec: StageReports,
    path: str,
    *,
    ns: bool,
) -> tuple[Measurement | None, dict[str, Measurement]]:
    text = evidence.text(path, spec.stage)
    if text is None:
        return None, {}
    try:
        data = json.loads(text, object_pairs_hook=metric_pairs)
        if not isinstance(data, dict):
            raise ValueError("ORFS metrics require an object")
    except (ValueError, RecursionError):
        evidence.result.missing_measurements.append(Gap(spec.stage, "MALFORMED_METRICS", path))
        return None, {}

    def metric(key: str, unit: str) -> Measurement | None:
        full_key = f"{spec.prefix}__{key}"
        raw = data.get(full_key)
        try:
            value = number(raw)
        except OverflowError:
            return None
        if value is None or abs(value) > 10**12 or (unit == "ns" and not ns):
            return None
        if unit == "count" and (value < 0 or int(value) != value):
            return None
        if unit == "fraction" and not 0 <= value <= 1:
            return None
        return Measurement(value, unit, evidence.cite(path, f"JSON key {full_key}"))

    check = evidence.result.check_type
    worst = metric(f"timing__{check}__ws", "ns") if check in {"setup", "hold"} else None
    physical = {
        name: measurement
        for key, (name, unit) in PHYSICAL_METRICS.items()
        if (measurement := metric(key, unit)) is not None
    }
    return worst, physical


def stage_paths(
    evidence: Evidence,
    spec: StageReports,
    path: str,
    stage: StageTiming,
    *,
    ns: bool,
) -> list[PathDiagnosis]:
    report_bytes = evidence.read(path, spec.stage)
    if report_bytes is None:
        return []
    raw = parse_timing_report(report_bytes, default_unit="ns" if ns else None)
    lines = report_bytes.decode("utf-8-sig", errors="replace")
    source_lines = lines.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    for message in raw.limitations:
        ref = evidence.cite(path, "OpenSTA timing report")
        evidence.result.missing_measurements.append(
            Gap(spec.stage, "UNPARSED_REPORT", message, (ref,))
        )
    paths = []
    for parsed in raw.paths:
        ref = evidence.cite(
            path,
            "OpenSTA timing path",
            lines=source_lines,
            start=parsed.line_start,
            end=parsed.line_end,
        )
        if parsed.status == "VALID" and (
            parsed.arrival is None
            or parsed.required is None
            or not cumulative_matches(parsed.launch, parsed.arrival)
            or not cumulative_matches(parsed.capture, parsed.required)
        ):
            parsed.status = "INVALID"
            parsed.limitations.append(
                "Cumulative path times do not reconcile with the printed increments."
            )
        if parsed.status != "VALID":
            evidence.result.missing_measurements.append(
                Gap(
                    spec.stage,
                    parsed.status,
                    " ".join(parsed.limitations),
                    (ref,),
                    (parsed.line_start, parsed.line_end),
                )
            )
            if parsed.status == "UNCONSTRAINED":
                evidence.result.observed_findings.append(
                    Finding(
                        f"unconstrained_{spec.stage}_{parsed.line_start}",
                        Category.UNCONSTRAINED_PATH,
                        Strength.OBSERVED,
                        "The report explicitly lists a path without a timing requirement.",
                        (ref,),
                    )
                )
            continue
        # The -unconstrained section repeats timed paths. The compact reg-to-reg
        # summary also duplicates the full table when present.
        if parsed.check_type != evidence.result.check_type or parsed.section == "-unconstrained":
            continue
        if parsed.corner and stage.corner and parsed.corner != stage.corner:
            evidence.result.missing_measurements.append(
                Gap(
                    spec.stage,
                    "CORNER_CONFLICT",
                    "Report corner differs from the recorded corner.",
                    (ref,),
                )
            )
            continue
        constraint = stage.constraints
        refs = (ref, constraint.evidence) if constraint else (ref,)
        path_id = identity(evidence.experiment["run_id"], spec.stage, ref)[:20]
        paths.append(
            PathDiagnosis(
                path_id,
                spec.stage,
                parsed.corner or stage.corner,
                constraint.sha256 if constraint else None,
                parsed,
                breakdown(parsed),
                refs,
            )
        )
    full = {path_key(item.path) for item in paths if item.path.format == "full"}
    return [item for item in paths if item.path.format == "full" or path_key(item.path) not in full]


def path_key(path: TimingPath) -> tuple[str, ...] | None:
    start, end = path.startpoint, path.endpoint
    if start is None or end is None or not path.group or not path.launch or not path.capture:
        return None
    launch, capture = path.launch[0], path.capture[0]
    if (
        launch.kind != "edge"
        or capture.kind != "edge"
        or not launch.clock_edge
        or not capture.clock_edge
    ):
        return None
    if launch.time is None or capture.time is None:
        return None
    if start.clock and start.clock != launch.name or end.clock and end.clock != capture.name:
        return None
    pins = [row for row in path.launch if row.kind in {"pin", "port"}]
    if not pins or not pins[-1].transition:
        return None
    # Edge separation is part of the timing relationship. The constrained pins
    # and receiving transition must also agree before a path is compared.
    launch_pins = [
        row for row in pins if row.name == start.name or row.name.rpartition("/")[0] == start.name
    ]
    if not launch_pins or not launch_pins[-1].transition:
        return None
    return (
        start.name,
        end.name,
        path.group,
        path.check_type,
        path.classification,
        launch.name,
        launch.clock_edge,
        capture.name,
        capture.clock_edge,
        str((capture.time.exact - launch.time.exact).normalize()),
        launch_pins[-1].name,
        pins[-1].name,
        pins[-1].transition,
    )


def pin_sequence(path: TimingPath) -> tuple[str, ...]:
    boundary = launch_boundary(path)
    return () if boundary is None else tuple(row.name for row in path.launch[boundary:])


def launch_transition(path: TimingPath) -> str | None:
    boundary = launch_boundary(path)
    if boundary is None:
        return None
    index = boundary + (
        path.startpoint is not None and path.startpoint.kind in {"register", "latch"}
    )
    return path.launch[index].transition


def compare_paths(paths: list[PathDiagnosis]) -> list[PathTrend]:
    groups: dict[tuple[str, ...], dict[str, list[PathDiagnosis]]] = defaultdict(
        lambda: defaultdict(list)
    )
    for item in paths:
        key = path_key(item.path)
        if (
            key is not None
            and item.corner
            and item.constraint_identity
            and item.path.status == "VALID"
        ):
            groups[(item.corner, item.constraint_identity, *key)][item.stage].append(item)
    trends = []
    order = {spec.stage: index for index, spec in enumerate(STAGE_REPORTS)}
    for (corner, constraint, *matched_key), stages in groups.items():
        entries = [stages[key] for key in sorted(stages, key=order.__getitem__)]
        for earlier, later in zip(entries, entries[1:]):
            if len(earlier) != 1 or len(later) != 1:
                continue
            left, right = earlier[0], later[0]
            a, b = left.path.slack, right.path.slack
            assert a is not None and b is not None
            launch = launch_transition(left.path), launch_transition(right.path)
            a_pins, b_pins = pin_sequence(left.path), pin_sequence(right.path)
            components = {}
            for name in (
                "data_path_delay",
                "cell_delay",
                "net_delay",
                "launch_clock",
                "capture_clock",
                "clock_effect",
            ):
                one, two = getattr(left.breakdown, name), getattr(right.breakdown, name)
                if one is not None and two is not None:
                    components[name] = DelayDelta(
                        float(Decimal(str(two.ns)) - Decimal(str(one.ns))),
                        one.tolerance_ns + two.tolerance_ns,
                        one.lines,
                        two.lines,
                    )
            trends.append(
                PathTrend(
                    left.stage,
                    right.stage,
                    left.id,
                    right.id,
                    tuple(matched_key),
                    corner,
                    constraint,
                    float(b.exact - a.exact),
                    float(a.error + b.error),
                    components,
                    (*left.evidence, *right.evidence),
                    launch,
                    a_pins == b_pins
                    if a_pins and b_pins and left.path.format == right.path.format
                    else None,
                )
            )
    return trends


def add_stage(evidence: Evidence, variant: str, spec: StageReports, *, ns: bool) -> None:
    result = evidence.result
    constraints = read_constraints(evidence, f"results/{variant}/{spec.sdc}", spec.stage, ns=ns)
    worst, physical = read_metrics(evidence, spec, f"logs/{variant}/{spec.metrics}", ns=ns)
    stage = StageTiming(
        spec.stage,
        result.timing_corner,
        constraints,
        worst,
        min(0, worst.value) if worst else None,
        physical=physical,
    )
    if spec.timing:
        paths = stage_paths(evidence, spec, f"reports/{variant}/{spec.timing}", stage, ns=ns)
        paths.sort(key=lambda item: item.path.slack.ns if item.path.slack else float("inf"))
        result.critical_paths.extend(paths)
        for item in paths:
            result.delay_breakdowns[item.id] = item.breakdown
        if worst and paths:
            best = paths[0].path.slack
            assert best is not None
            stage.worst_path_ids = tuple(
                item.id
                for item in paths
                if item.path.slack
                and abs(Decimal(str(worst.value)) - item.path.slack.exact)
                <= item.path.slack.error + Decimal("0.0000005")
                and item.path.slack.exact == best.exact
            )
    if spec.stage == "finish" and constraints:
        result.constraint_identity = constraints.sha256
    result.stage_trends.append(stage)


def add_flow_context(evidence: Evidence, variant: str) -> None:
    result, experiment = evidence.result, evidence.experiment
    stages = {stage.stage: stage for stage in result.stage_trends}
    for key, metric in experiment["metrics"].items():
        path, value = metric["artifact"], metric["value"]
        if (
            key.startswith("stage_runtime.")
            and path
            and value is not None
            and metric["stage"] in stages
        ):
            ref = evidence.cite(path, "controller attempt runtime")
            stages[metric["stage"]].physical[key] = Measurement(value, "s", ref)
    for key, gate in experiment["verification_gates"].items():
        if gate["state"] in {"PASSED", "FAILED"}:
            result.verification_evidence[key] = tuple(
                evidence.cite(path, f"verification gate {key}") for path in gate["artifacts"]
            )
    drc = experiment["metrics"]["klayout_drc_report_value_count"]
    if drc["artifact"] and drc["value"] is not None:
        ref = evidence.cite(drc["artifact"], "KLayout DRC report count")
        stages["finish"].physical["platform_drc_violations"] = Measurement(
            drc["value"], "count", ref
        )
    route_log = f"logs/{variant}/5_1_grt.log"
    artifact = experiment["artifacts"].get(route_log)
    origin = artifact["stage"] if artifact else "grt"
    if (text := evidence.text(route_log, origin)) is not None:
        lines = text.splitlines()
        rows = [
            (i, m) for i, line in enumerate(lines, 1) if (m := OVERFLOW.fullmatch(line.strip()))
        ]
        if rows and len(rows[-1][1][1]) <= 12:
            line, match = rows[-1]
            ref = evidence.cite(
                route_log, "last congestion Total row", lines=lines, start=line, end=line
            )
            stages["grt"].physical["global_route_total_overflow"] = Measurement(
                int(match[1]), "count", ref
            )
    if "global_route_total_overflow" not in stages["grt"].physical:
        result.missing_measurements.append(
            Gap("grt", "CONGESTION_UNAVAILABLE", "No verified global routing overflow summary.")
        )
    for name, attempt in evidence.latest_attempts.items():
        if attempt["state"] in {"failed", "interrupted"} and name in stages:
            result.missing_measurements.append(
                Gap(
                    name,
                    "STAGE_INCOMPLETE",
                    f"The latest controller attempt was {attempt['state']}.",
                )
            )
    if result.verification_summary.get("timing_coverage") != "PASSED":
        result.missing_measurements.append(
            Gap(
                "finish",
                "COVERAGE_UNVERIFIED",
                "Timing coverage has not been independently established.",
            )
        )


def diagnose_source(
    experiment_id: str, source: Source, check_type: Check = "setup"
) -> DiagnosticReport:
    if not re.fullmatch(ID_PATTERN, experiment_id):
        raise ValueError("Invalid diagnostic experiment ID")
    if check_type not in CHECKS:
        raise ValueError("Unsupported timing check")
    with TemporaryDirectory(prefix="aeda-diagnosis-") as temp:
        snapshot = Snapshot(source, Path(temp))
        try:
            experiment = collect_evidence(snapshot)
        except (OSError, ValueError, KeyError, TypeError, RecursionError, OverflowError) as error:
            raise ValueError("Unable to verify the recorded experiment evidence") from error
        design = identifier(experiment["design"], "design")
        platform = identifier(experiment["platform"], "platform")
        corner = experiment["corner"]
        if corner is not None and (not isinstance(corner, str) or not re.fullmatch(SYMBOL, corner)):
            corner = None
        result = DiagnosticReport(
            experiment_id,
            experiment["run_id"],
            design,
            platform,
            check_type,
            corner,
            {name: gate["state"] for name, gate in experiment["verification_gates"].items()},
            experiment["acceptance"],
        )
        evidence = Evidence(experiment, snapshot.target, result)
        variant = f"{platform}/{design}/base"
        ns = (
            experiment["toolchain"].get("image") == IMAGE
            and experiment["source_revision"] == REVISION
            and platform == "sky130hd"
        )
        for spec in STAGE_REPORTS:
            add_stage(evidence, variant, spec, ns=ns)
        if corner is None:
            result.missing_measurements.append(
                Gap(
                    "all",
                    "CORNER_UNAVAILABLE",
                    "The run inventory has no established timing corner.",
                )
            )
        add_flow_context(evidence, variant)
        if (snapshot.target / "run.sqlite3").is_file():
            snapshot.reject_active_database()
        snapshot.verify_source()
    result.matched_paths = compare_paths(result.critical_paths)
    from .diagnostic_findings import analyze

    analyze(result)
    result.evidence_identity = identity(
        SCHEMA_VERSION,
        result.run_id,
        check_type,
        result.timing_corner,
        result.verification_summary,
        {
            key: (item["sha256"], item["availability"])
            for key, item in experiment["artifacts"].items()
        },
        sorted(result.evidence_references),
    )
    return result


def diagnose_timing(
    registry: Registry, experiment_id: str, check_type: Check = "setup"
) -> DiagnosticReport:
    source = registry.experiments.get(experiment_id)
    if source is None:
        raise ValueError("experiment_not_authorized")
    return diagnose_source(experiment_id, source, check_type)


def diagnose_path(
    registry: Registry, experiment_id: str, path_id: str, check_type: Check = "setup"
) -> PathDiagnosis:
    result = diagnose_timing(registry, experiment_id, check_type)
    for path in result.critical_paths:
        if path.id == path_id:
            return path
    raise ValueError("Path ID is unavailable for this check and current evidence")

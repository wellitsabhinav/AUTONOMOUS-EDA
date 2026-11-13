"""Read the pinned OpenSTA full and compact path tables."""

from __future__ import annotations

import re
from dataclasses import dataclass, field, replace
from decimal import Decimal
from typing import Literal

from .knowledge.sources import MAX_TEXT_BYTES, decoded

MAX_PATHS = 128
MAX_PATH_LINES = 1024
SYMBOL = r"(?!/|\\\\|[A-Za-z]:[/\\])[A-Za-z0-9_.$/\\\[\]:-]{1,256}"
NUMBER = r"[+-]?(?:\d{1,12}(?:\.\d{1,12})?|\.\d{1,12})(?:[eE][+-]?\d{1,2})?"
NUM = re.compile(NUMBER)
VALUE = re.compile(
    rf"({NUMBER})\s+(data arrival time|data required time|slack \((?:MET|VIOLATED)\))"
)
UNITS = {"ps": Decimal("0.001"), "ns": Decimal(1), "us": Decimal(1000)}
HEADER = re.compile(r"(?:Fanout\s+Cap\s+Slew\s+)?Delay\s+Time\s+Description")
CLOCK = re.compile(rf"clock ({SYMBOL}) \((rise|fall) edge\)")
PIN = re.compile(rf"({SYMBOL}) \(({SYMBOL})\)")
NET = re.compile(rf"({SYMBOL}) \(net\)")
TERMS = {
    "clock source latency": "source_latency",
    "clock network delay (ideal)": "ideal_latency",
    "clock network delay (propagated)": "network_latency",
    "clock uncertainty": "uncertainty",
    "clock reconvergence pessimism": "crpr",
    "input external delay": "input_delay",
    "output external delay": "output_delay",
    "library setup time": "setup",
    "library hold time": "hold",
    "library recovery time": "recovery",
    "library removal time": "removal",
    "clock gating setup time": "gating_setup",
    "clock gating hold time": "gating_hold",
    "max delay": "max_delay",
    "min delay": "min_delay",
}
CHECKS = {
    "setup",
    "hold",
    "recovery",
    "removal",
    "gating_setup",
    "gating_hold",
    "max_delay",
    "min_delay",
}


@dataclass(frozen=True)
class Time:
    reported: str
    unit: str
    ns: float
    line: int

    @property
    def exact(self) -> Decimal:
        return Decimal(self.reported) * UNITS[self.unit]

    @property
    def error(self) -> Decimal:
        exponent = Decimal(self.reported).as_tuple().exponent
        assert isinstance(exponent, int)
        return Decimal("0.5").scaleb(exponent) * UNITS[self.unit]


@dataclass(frozen=True)
class Row:
    line: int
    kind: str
    name: str
    increment: Time | None = None
    time: Time | None = None
    transition: str | None = None
    slew: Time | None = None
    capacitance_reported: str | None = None
    fanout: int | None = None
    cell: str | None = None
    net: str | None = None
    net_line: int | None = None
    clock_edge: str | None = None


@dataclass(frozen=True)
class Terminal:
    name: str
    line: int
    kind: str
    clock: str | None


@dataclass
class TimingPath:
    line_start: int
    line_end: int
    section: str
    path_type: str | None = None
    check_type: str = "other"
    group: str | None = None
    corner: str | None = None
    classification: str = "other"
    startpoint: Terminal | None = None
    endpoint: Terminal | None = None
    format: str | None = None
    launch: list[Row] = field(default_factory=list)
    capture: list[Row] = field(default_factory=list)
    arrival: Time | None = None
    required: Time | None = None
    slack: Time | None = None
    status: Literal["VALID", "PARTIAL", "UNCONSTRAINED", "INVALID"] = "INVALID"
    calculated_slack_ns: float | None = None
    slack_tolerance_ns: float | None = None
    limitations: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class TimingReport:
    paths: list[TimingPath]
    limitations: list[str]


def measured(raw: str, unit: str, line: int) -> Time:
    if not NUM.fullmatch(raw):
        raise ValueError("Invalid timing number.")
    value = Decimal(raw) * UNITS[unit]
    if abs(value) > 10**9 or (value and abs(value) < Decimal("1e-15")):
        raise ValueError("Timing number exceeds supported range.")
    result = Time(raw, unit, float(value), line)
    if result.error > UNITS[unit] / 2:
        raise ValueError("Timing number lacks sufficient printed precision.")
    return result


def terminal(value: str, line: int) -> Terminal:
    match = re.fullmatch(rf"({SYMBOL})(?: \(([^()]+)\))?", value)
    if not match:
        raise ValueError("Unsupported terminal.")
    name, description = match.groups()
    description = description or ""
    clock = re.search(
        rf"(?:clocked by|against (?:rising|falling)-edge clock) ({SYMBOL})$", description
    )
    kind = "other"
    if re.match(r"(?:rising|falling) edge-triggered flip-flop\b", description):
        kind = "register"
    elif re.match(r"(?:negative|positive) level-sensitive latch\b", description):
        kind = "latch"
    elif description.startswith(("input port", "output port")):
        kind = description.split()[0]
    return Terminal(name, line, kind, clock[1] if clock else None)


def parse_row(text: str, line: int, unit: str, full: bool) -> Row:
    parts = text.split()
    split = next((i for i, value in enumerate(parts) if not NUM.fullmatch(value)), len(parts))
    values = parts[:split]
    description = " ".join(parts[split:])
    if clock := CLOCK.fullmatch(description):
        if len(values) not in ({2, 3} if full else {2}):
            raise ValueError("Unsupported clock row.")
        return Row(
            line, "edge", clock[1], time=measured(values[-1], unit, line), clock_edge=clock[2]
        )
    transition = None
    if description.startswith(("^ ", "v ")):
        transition, description = description.split(" ", 1)
    if description in TERMS and len(values) == 2:
        return Row(
            line,
            TERMS[description],
            description,
            measured(values[0], unit, line),
            measured(values[1], unit, line),
            transition,
        )
    pin = PIN.fullmatch(description)
    if pin is None or transition is None or len(values) not in ({1, 3, 5} if full else {1, 2}):
        raise ValueError("Unsupported delay row.")
    name, cell = pin.groups()
    fanout = None
    cap = None
    slew = None
    if len(values) == 5:
        if not re.fullmatch(r"\d{1,9}", values[0]) or not 0 <= Decimal(values[1]) <= 10**9:
            raise ValueError("Invalid load columns.")
        fanout, cap = int(values[0]), values[1]
    if len(values) >= 3:
        slew = measured(values[-3], unit, line)
        if slew.ns < 0:
            raise ValueError("Invalid slew.")
    port = cell in {"in", "out"}
    return Row(
        line,
        "port" if port else "pin",
        name,
        measured(values[-2], unit, line) if len(values) > 1 else None,
        measured(values[-1], unit, line),
        transition,
        slew,
        cap,
        fanout,
        None if port else cell,
    )


def bounds(values: list[Time]) -> Decimal:
    return sum((value.error for value in values), Decimal(0))


def validate_totals(path: TimingPath, arrivals: list[Time], requirements: list[Time]) -> None:
    if path.arrival is None or path.required is None or path.slack is None:
        explicit = path.group in {"unconstrained", "(none)"} or (
            path.section == "-unconstrained" and path.arrival is not None and path.required is None
        )
        path.status = "UNCONSTRAINED" if explicit and path.slack is None else "PARTIAL"
        path.limitations.append("Missing arrival, required time or slack.")
        return
    for items, sign in ((arrivals, -1), (requirements, 1)):
        if len(items) > 2 or (
            len(items) == 2 and abs(items[0].exact - sign * items[1].exact) > bounds(items)
        ):
            raise ValueError("Inconsistent repeated timing totals.")
    slack = path.required.exact - path.arrival.exact
    if path.path_type == "min":
        slack = -slack
    tolerance = bounds([path.arrival, path.required, path.slack])
    if abs(path.slack.exact - slack) > tolerance:
        raise ValueError("Slack does not reconcile with timing totals.")
    path.calculated_slack_ns = float(slack)
    path.slack_tolerance_ns = float(tolerance)
    path.status = "VALID"


def identify_check(path: TimingPath) -> str:
    kind = "setup" if path.path_type == "max" else "hold"
    expected = {
        "gated clock": f"gating_{kind}",
        "asynchronous": ("recovery" if kind == "setup" else "removal"),
    }.get(path.group or "", kind)
    checks = {row.kind for row in path.capture if row.kind in CHECKS}
    if len(checks) > 1:
        raise ValueError("Ambiguous path check.")
    check = next(iter(checks), expected)
    compatible = (
        {"setup", "gating_setup", "recovery", "max_delay"}
        if kind == "setup"
        else {"hold", "gating_hold", "removal", "min_delay"}
    )
    if check not in compatible:
        raise ValueError("Check disagrees with path type.")
    return check


def parse_path(lines: list[str], path: TimingPath, unit: str) -> None:
    header: dict[str, tuple[str, int]] = {}
    last_key = ""
    for index, raw in enumerate(lines):
        text = raw.strip()
        line = path.line_start + index
        if HEADER.fullmatch(text):
            path.format = "full" if text.startswith("Fanout") else "compact"
            break
        if not text:
            continue
        match = re.fullmatch(r"(Startpoint|Endpoint|Path Group|Path Type|Corner)\s*:\s*(.+)", text)
        if match and match[1] not in header:
            header[match[1]] = match[2], line
            last_key = match[1]
        elif text.startswith("(") and last_key in {"Startpoint", "Endpoint"}:
            previous, origin = header[last_key]
            header[last_key] = f"{previous} {text}", origin
            last_key = ""
        else:
            raise ValueError("Unsupported path header.")
    else:
        raise ValueError("Missing supported delay table.")
    path.startpoint = terminal(*header["Startpoint"])
    path.endpoint = terminal(*header["Endpoint"])
    path.group = header["Path Group"][0]
    if not re.fullmatch(rf"{SYMBOL}(?: {SYMBOL}){{0,3}}|\(none\)", path.group):
        raise ValueError("Unsupported path group.")
    path.path_type = header["Path Type"][0]
    if path.path_type not in {"min", "max"}:
        raise ValueError("Unsupported path type.")
    if "Corner" in header:
        path.corner = header["Corner"][0]
        if not re.fullmatch(SYMBOL, path.corner):
            raise ValueError("Unsupported timing corner.")
    left, right = path.startpoint.kind, path.endpoint.kind
    if left in {"input", "register"} and right in {"output", "register"}:
        path.classification = f"{left}-to-{right}"

    full = path.format == "full"
    phase = path.launch
    pending_net = None
    arrivals: list[Time] = []
    requirements: list[Time] = []
    for offset in range(index + 1, len(lines)):
        line = path.line_start + offset
        text = lines[offset].strip()
        if not text or re.fullmatch(r"-{3,}", text):
            continue
        if result := VALUE.fullmatch(text):
            if pending_net:
                raise ValueError("Net marker lacks a receiving pin.")
            value = measured(result[1], unit, line)
            if result[2] == "data arrival time":
                arrivals.append(value)
                path.arrival = arrivals[0]
                phase = path.capture
            elif result[2] == "data required time":
                if not arrivals:
                    raise ValueError("Required time precedes arrival section.")
                requirements.append(value)
                path.required = requirements[0]
            else:
                if value.ns and (value.ns < 0) != (result[2] == "slack (VIOLATED)"):
                    raise ValueError("Slack sign and status disagree.")
                path.slack = value
                path.line_end = line
                if any(item.strip() for item in lines[offset + 1 :]):
                    raise ValueError("Unexpected content after path slack.")
                break
        elif net := NET.fullmatch(text):
            if not full or pending_net or not phase or phase[-1].kind not in {"pin", "port"}:
                raise ValueError("Unpaired net marker.")
            pending_net = (net[1], line)
        else:
            if requirements:
                raise ValueError("Unexpected row after required time.")
            row = parse_row(text, line, unit, full)
            if pending_net:
                if row.kind not in {"pin", "port"}:
                    raise ValueError("Net marker lacks a receiving pin.")
                row = replace(row, net=pending_net[0], net_line=pending_net[1])
                pending_net = None
            phase.append(row)
    if pending_net:
        raise ValueError("Net marker lacks a receiving pin.")
    path.check_type = identify_check(path)
    validate_totals(path, arrivals, requirements)


def parse_timing_report(raw: bytes, *, default_unit: str | None = None) -> TimingReport:
    if len(raw) > MAX_TEXT_BYTES:
        return TimingReport([], ["Timing report exceeds the byte limit."])
    try:
        lines = decoded(raw).split("\n")
    except ValueError:
        return TimingReport([], ["Timing report is unreadable or contains sensitive text."])
    units = {
        match[1].strip().casefold()
        for line in lines
        if (match := re.fullmatch(r"\s*(?:time units?|time_unit)\s*[:=]\s*(.+)", line, re.I))
    }
    unit = next(iter(units), default_unit)
    if len(units) > 1 or unit not in UNITS or (units and default_unit and unit != default_unit):
        return TimingReport([], ["Timing units are ambiguous or unsupported."])
    assert unit is not None
    section = "report_checks"
    blocks: list[tuple[int, int, str]] = []
    start = None
    for index, line in enumerate(lines):
        text = line.strip()
        new_path = bool(re.match(r"Startpoint\s*:", text))
        heading = re.fullmatch(r"(?:.+?\s)?report_checks\s+(.+)", text)
        if new_path or heading or re.fullmatch(r"={10,}", text):
            if start is not None:
                blocks.append((start, index, section))
            start = index if new_path else None
            if heading:
                section = heading[1]
    if start is not None:
        blocks.append((start, len(lines), section))
    if len(blocks) > MAX_PATHS:
        return TimingReport([], ["Timing report exceeds the path limit."])
    if not blocks:
        return TimingReport([], ["No supported timing paths found."])
    paths = []
    for start, stop, label in blocks:
        while stop > start + 1 and not lines[stop - 1].strip():
            stop -= 1
        path = TimingPath(start + 1, stop, label)
        paths.append(path)
        block = lines[start:stop]
        if len(block) > MAX_PATH_LINES or any(len(line) > 1024 for line in block):
            path.limitations.append("Path exceeds the line limit.")
            continue
        try:
            parse_path(block, path, unit)
        except (KeyError, ValueError) as error:
            path.limitations.append(
                str(error) if isinstance(error, ValueError) else "Missing header."
            )
    return TimingReport(paths, [])

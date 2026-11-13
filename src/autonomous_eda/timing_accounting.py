"""Reconcile reported timing increments with cumulative path times."""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal

from .timing import CHECKS, Row, Time, TimingPath, bounds

CORRECTIONS = CHECKS | {"uncertainty", "crpr", "input_delay", "output_delay"}


@dataclass(frozen=True)
class Component:
    ns: float
    tolerance_ns: float
    lines: tuple[int, ...]


@dataclass
class Breakdown:
    status: str = "PARTIAL"
    data_path_delay: Component | None = None
    cell_delay: Component | None = None
    net_delay: Component | None = None
    launch_clock: Component | None = None
    capture_clock: Component | None = None
    clock_effect: Component | None = None
    terms: dict[str, list[Time]] = field(default_factory=dict)
    cell_arc_count: int | None = None
    limitations: list[str] = field(default_factory=list)


def component(value: Decimal, values: list[Time]) -> Component:
    return Component(float(value), float(bounds(values)), tuple(item.line for item in values))


def difference(end: Time, start: Time) -> Component:
    return component(end.exact - start.exact, [start, end])


def totals(rows: list[Row]) -> Decimal:
    return sum((row.increment.exact for row in rows if row.increment), Decimal(0))


def cumulative_matches(rows: list[Row], end: Time) -> bool:
    if not rows or rows[0].time is None:
        return False
    start = rows[0].time
    previous = start
    for row in rows[1:]:
        if row.time is None:
            return False
        if row.increment is None and row.kind not in {"pin", "port"}:
            return False
        values = [previous, row.time]
        change = row.time.exact - previous.exact
        if row.increment is not None:
            change -= row.increment.exact
            values.append(row.increment)
        if abs(change) > bounds(values):
            return False
        previous = row.time
    increments = [row.increment for row in rows[1:] if row.increment is not None]
    return abs(end.exact - previous.exact) <= bounds([end, previous]) and abs(
        end.exact - start.exact - totals(rows[1:])
    ) <= bounds([start, end, *increments])


def launch_boundary(path: TimingPath) -> int | None:
    point = path.startpoint
    if point is None:
        return None
    for index, row in enumerate(path.launch):
        if point.kind == "input" and row.kind == "port" and row.name == point.name:
            return index
        if (
            point.kind in {"register", "latch"}
            and row.kind == "pin"
            and row.name.rpartition("/")[0] == point.name
            and row.name.rpartition("/")[2] in {"CLK", "GATE", "GATE_N"}
            and index + 1 < len(path.launch)
            and path.launch[index + 1].name.rpartition("/")[0] == point.name
            and path.launch[index + 1].net is None
        ):
            return index
    return None


def clock_path(rows: list[Row], clock: str | None) -> bool:
    return bool(
        rows
        and rows[0].kind == "edge"
        and (clock is None or rows[0].name == clock)
        and all(
            row.kind in {"pin", "port", "source_latency", "ideal_latency", "network_latency"}
            for row in rows[1:]
        )
    )


def data_arcs(rows: list[Row]) -> tuple[list[Row], list[Row]] | None:
    cell_rows = []
    net_rows = []
    for previous, row in zip(rows, rows[1:]):
        if row.increment is None or previous.kind not in {"pin", "port"}:
            return None
        if row.net and row.kind in {"pin", "port"}:
            net_rows.append(row)
        elif (
            previous.kind == row.kind == "pin"
            and previous.name.rpartition("/")[0] == row.name.rpartition("/")[0]
            and previous.cell == row.cell
        ):
            cell_rows.append(row)
        else:
            return None
    return cell_rows, net_rows


def breakdown(path: TimingPath) -> Breakdown:
    result = Breakdown()
    if path.status != "VALID":
        result.limitations.append("A reconciled timing path is required for decomposition.")
        return result
    assert path.arrival is not None and path.required is not None
    for row in (*path.launch, *path.capture):
        if row.kind in CORRECTIONS and row.increment is not None:
            result.terms.setdefault(row.kind, []).append(row.increment)
    boundary = launch_boundary(path)
    launch_time = path.launch[boundary].time if boundary is not None else None
    if boundary is not None and launch_time is not None:
        data_rows = path.launch[boundary:]
        if path.format == "compact":
            result.limitations.append("Compact path rows can fold net increments into cell rows.")
        if len(data_rows) > 1 and cumulative_matches(data_rows, path.arrival):
            result.data_path_delay = difference(path.arrival, launch_time)
            arcs = data_arcs(data_rows) if path.format == "full" else None
            if arcs is not None:
                cell_rows, net_rows = arcs
                for field, rows in (("cell_delay", cell_rows), ("net_delay", net_rows)):
                    increments = [row.increment for row in rows if row.increment is not None]
                    setattr(result, field, component(totals(rows), increments) if rows else None)
                result.cell_arc_count = len(cell_rows)
        input_terms = result.terms.get("input_delay", [])
        if len(input_terms) <= 1:
            clock_rows = path.launch[: boundary + 1]
            if input_terms:
                clock_rows = [row for row in clock_rows if row.kind != "input_delay"][:-1]
            launch_base = clock_rows[-1].time if clock_rows else None
            launch_edge = clock_rows[0].time if clock_rows else None
            if (
                launch_base is not None
                and launch_edge is not None
                and clock_path(clock_rows, path.startpoint.clock if path.startpoint else None)
                and cumulative_matches(clock_rows, launch_base)
                and (
                    not input_terms
                    or abs(launch_time.exact - launch_base.exact - input_terms[0].exact)
                    <= bounds([launch_time, launch_base, *input_terms])
                )
            ):
                result.launch_clock = difference(launch_base, launch_edge)

    capture_end = next(
        (index for index, row in enumerate(path.capture) if row.kind in CORRECTIONS),
        len(path.capture),
    )
    clock_rows = path.capture[:capture_end]
    if clock_rows and clock_path(clock_rows, path.endpoint.clock if path.endpoint else None):
        edge, end = clock_rows[0].time, clock_rows[-1].time
        if edge is not None and end is not None and cumulative_matches(path.capture, path.required):
            result.capture_clock = difference(end, edge)
    if result.launch_clock and result.capture_clock:
        launch_edge_row = path.launch[0]
        capture_edge_row = path.capture[0]
        if launch_edge_row.name == capture_edge_row.name:
            sign = -1 if path.path_type == "min" else 1
            result.clock_effect = Component(
                float(
                    sign
                    * (Decimal(str(result.capture_clock.ns)) - Decimal(str(result.launch_clock.ns)))
                ),
                result.capture_clock.tolerance_ns + result.launch_clock.tolerance_ns,
                (*result.launch_clock.lines, *result.capture_clock.lines),
            )
    if result.data_path_delay is None:
        result.limitations.append("Data increments or the launch boundary could not be reconciled.")
    if result.cell_delay is None or result.net_delay is None:
        result.limitations.append(
            "Separate cell and net totals are unavailable at this resolution."
        )
    if result.launch_clock is None or result.capture_clock is None:
        result.limitations.append(
            "Clock insertion is only available with a reconciled clock boundary."
        )
    if all((result.cell_delay, result.net_delay, result.launch_clock, result.capture_clock)):
        result.status = "ACCOUNTED"
    return result

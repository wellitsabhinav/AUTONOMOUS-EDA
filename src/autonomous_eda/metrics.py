"""Read metrics emitted by the pinned ORFS reports."""

from __future__ import annotations

import json
import math
import re
from pathlib import Path
from typing import Literal

Gate = Literal["passed", "failed", "blocked", "not checked"]
Number = float | int
FINAL_FIELDS = {
    "standard_cell_area_um2": "finish__design__instance__area__stdcell",
    "instance_count": "finish__design__instance__count",
    "setup_worst_slack_ns": "finish__timing__setup__ws",
    "setup_tns_ns": "finish__timing__setup__tns",
    "hold_worst_slack_ns": "finish__timing__hold__ws",
    "hold_tns_ns": "finish__timing__hold__tns",
    "setup_violations": "finish__timing__drv__setup_violation_count",
    "hold_violations": "finish__timing__drv__hold_violation_count",
    "estimated_power_w": "finish__power__total",
    "flow_warnings": "finish__flow__warnings__count",
    "flow_errors": "finish__flow__errors__count",
}
ROUTE_FIELDS = {
    "router_drc_errors": "detailedroute__route__drc_errors",
    "routed_wirelength_um": "detailedroute__route__wirelength",
}


def read_json(path: Path) -> dict[str, object]:
    if not path.is_file():
        return {}
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError(f"expected an ORFS metrics object: {path}")
    return data


def number(value: object) -> Number | None:
    if isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value):
        return value
    return None


def timing_gate(slack: Number | None, violations: Number | None) -> Gate:
    if (slack is not None and slack < 0) or (violations is not None and violations > 0):
        return "failed"
    if slack is None or violations is None or violations < 0:
        return "not checked"
    return "passed"


def summarize(
    logs: Path,
    reports: Path,
    results: Path,
    final_valid: bool,
    *,
    route_valid: bool = True,
    verified: set[Path] | None = None,
) -> tuple[dict[str, Number | None], dict[str, Gate]]:
    def usable(path: Path) -> bool:
        return path.is_file() and (verified is None or path in verified)

    final_path = logs / "6_report.json"
    route_path = logs / "5_2_route.json"
    final = read_json(final_path) if final_valid and usable(final_path) else {}
    route = read_json(route_path) if route_valid and usable(route_path) else {}
    global_route = logs / "5_1_grt.log"
    metrics = {name: number(final.get(key)) for name, key in FINAL_FIELDS.items()}
    metrics.update({name: number(route.get(key)) for name, key in ROUTE_FIELDS.items()})
    setup = metrics["setup_worst_slack_ns"]
    metrics["setup_wns_ns"] = min(setup, 0) if setup is not None else None
    hold = metrics["hold_worst_slack_ns"]
    metrics["hold_wns_ns"] = min(hold, 0) if hold is not None else None
    metrics["global_route_total_overflow"] = None
    if route_valid and usable(global_route):
        congestion = re.findall(
            r"^Total\s+\d+\s+\d+\s+[\d.]+%\s+\d+\s*/\s*\d+\s*/\s*(\d+)\s*$",
            global_route.read_text(encoding="utf-8", errors="replace"),
            re.MULTILINE,
        )
        if congestion:
            metrics["global_route_total_overflow"] = int(congestion[-1])

    peaks: list[int] = []
    for log in logs.glob("[1-6]_*.log"):
        if not usable(log):
            continue
        peaks.extend(
            int(match)
            for match in re.findall(
                r"Peak memory: (\d+)KB", log.read_text(encoding="utf-8", errors="replace")
            )
        )
    metrics["max_reported_process_rss_kib"] = max(peaks, default=None)

    drc: Gate = "not checked"
    lvs: Gate = "not checked"
    gds = results / "6_final.gds"
    gds_valid = final_valid and usable(gds)
    cutoff = gds.stat().st_mtime_ns if gds_valid and verified is None else 0
    count = reports / "6_drc_count.rpt"
    drc_db = reports / "6_drc.lyrdb"
    metrics["klayout_drc_report_value_count"] = None
    if gds_valid and usable(count):
        value = count.read_text(encoding="utf-8").strip()
        if value.isdecimal() and all(
            usable(path) and path.stat().st_mtime_ns >= cutoff for path in (count, drc_db)
        ):
            metrics["klayout_drc_report_value_count"] = int(value)
            drc = "passed" if int(value) == 0 else "failed"
        else:
            drc = "blocked"
    lvs_log = logs / "6_lvs.log"
    if gds_valid and usable(lvs_log):
        comparison = lvs_log.read_text(encoding="utf-8", errors="replace")
        if lvs_log.stat().st_mtime_ns < cutoff:
            lvs = "blocked"
        elif "ERROR : Netlists don't match" in comparison:
            lvs = "failed"
        elif "INFO : Congratulations! Netlists match." in comparison and all(
            usable(path) and path.stat().st_mtime_ns >= cutoff
            for path in (lvs_log, results / "6_lvs.lvsdb")
        ):
            lvs = "passed"
        else:
            lvs = "blocked"
    return metrics, {
        "setup": timing_gate(setup, metrics["setup_violations"]),
        "hold": timing_gate(metrics["hold_worst_slack_ns"], metrics["hold_violations"]),
        "drc": drc,
        "lvs": lvs,
    }


def summarize_verified(
    root: Path, variant: str, verified: set[str]
) -> tuple[dict[str, Number | None], dict[str, Gate]]:
    return summarize(
        root / "logs" / variant,
        root / "reports" / variant,
        root / "results" / variant,
        True,
        verified={root / path for path in verified},
    )

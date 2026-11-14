"""Render and verify reproducible PNG evidence without changing the physical results."""

from __future__ import annotations

import importlib
import json
import shutil
import struct
import subprocess
from collections.abc import Callable
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any

from .diagnosis import diagnose_source
from .layout_render import VIEWS, render_layout
from .manifest import fingerprint, identifier, sha256
from .mcp.snapshot import Snapshot, Source, collect_evidence, ordinary, relative_parts
from .optimization import Campaign, campaign_report
from .visual_charts import BLUE, GRAY, GREEN, RED, bars, chart, table

SIGNATURE = b"\x89PNG\r\n\x1a\n"
MANIFEST = "visualization_manifest.json"


def png_size(path: Path) -> tuple[int, int]:
    ordinary(path, directory=False)
    with path.open("rb") as stream:
        header = stream.read(24)
    if len(header) != 24 or header[:8] != SIGNATURE or header[12:16] != b"IHDR":
        raise ValueError("Invalid PNG header")
    width, height = struct.unpack(">II", header[16:24])
    if not (1 <= width <= 4096 and 1 <= height <= 4096 and path.stat().st_size <= 25 * 1024**2):
        raise ValueError("Invalid PNG dimensions or size")
    image = importlib.import_module("PIL.Image")
    with image.open(path) as picture:
        picture.verify()
    with image.open(path) as picture:
        if all(low == high for low, high in picture.convert("RGB").getextrema()):
            raise ValueError("Rendered image is empty")
    return width, height


def atomic_json(path: Path, value: object) -> None:
    content = json.dumps(value, indent=2, allow_nan=False) + "\n"
    with TemporaryDirectory(prefix=".render-", dir=path.parent) as temp:
        staged = Path(temp) / path.name
        staged.write_text(content, encoding="utf-8", newline="\n")
        staged.replace(path)


class Export:
    def __init__(self, root: Path, work: Path, run_id: str) -> None:
        self.root = root
        self.work = work
        self.run_id = run_id
        self.images: dict[str, Any] = {}
        self.unavailable: dict[str, str] = {}

    def add(
        self,
        name: str,
        renderer: str,
        version: str,
        sources: dict[str, str],
        config: dict[str, Any],
    ) -> None:
        if not sources:
            self.unavailable[name] = "No verified source measurement"
            return
        source = self.work / f"{name}.png"
        width, height = png_size(source)
        self.images[name] = {
            "schema_version": 1,
            "visualization_type": name,
            "run_id": self.run_id,
            "filename": source.name,
            "source_artifacts": list(sources),
            "source_sha256": list(sources.values()),
            "image_sha256": sha256(source),
            "renderer": renderer,
            "renderer_version": version,
            "render_configuration": config,
            "image_width": width,
            "image_height": height,
            "generated_at": datetime.now(UTC).isoformat(),
            "evidence_status": "VERIFIED",
        }

    def plot(
        self,
        name: str,
        title: str,
        detail: str,
        sources: dict[str, str],
        draw: Callable[[Any], None],
    ) -> None:
        if not sources:
            self.unavailable[name] = "No verified source measurement"
            return
        try:
            version = chart(self.work / f"{name}.png", title, detail, draw)
            self.add(
                name,
                "matplotlib-agg",
                version,
                sources,
                {
                    "dpi": 170,
                    "figure_inches": [10, 6.2],
                    "typography": "DejaVu Sans",
                    "title": title,
                    "detail": detail,
                },
            )
        except ModuleNotFoundError:
            self.unavailable[name] = "Install the visuals extra"

    def publish(self, *, replace_types: set[str] | None = None) -> dict[str, Any]:
        output = self.root / "visualizations"
        output.mkdir(exist_ok=True)
        ordinary(output, directory=True)
        path = output / MANIFEST
        prior = read_manifest(path) if path.is_file() else {}
        images = {
            key: value
            for key, value in prior.get("images", {}).items()
            if replace_types is not None and key not in replace_types
        }
        unavailable = {
            key: value
            for key, value in prior.get("unavailable", {}).items()
            if replace_types is not None and key not in replace_types
        }
        with TemporaryDirectory(prefix=".publish-", dir=output) as stage:
            for entry in self.images.values():
                name = entry["filename"]
                local = Path(stage) / name
                shutil.copyfile(self.work / name, local)
                local.replace(output / name)
        value = {
            "schema_version": 1,
            "run_id": self.run_id,
            "images": {**images, **self.images},
            "unavailable": {**unavailable, **self.unavailable},
        }
        atomic_json(path, value)
        return value


def read_manifest(path: Path) -> dict[str, Any]:
    ordinary(path, directory=False)
    if path.stat().st_size > 2 * 1024**2:
        raise ValueError("Visualization manifest exceeds its size limit")
    value = json.loads(path.read_bytes())
    if not isinstance(value, dict) or value.get("schema_version") != 1:
        raise ValueError("Unrecognized visualization manifest")
    return value


def available_refs(
    evidence: dict[str, Any],
    ids: list[str] | tuple[str, ...],
) -> dict[str, str]:
    return {evidence[key]["path"]: evidence[key]["sha256"] for key in ids if key in evidence}


def timing_figures(export: Export, result: dict[str, Any]) -> None:
    design, corner, check = result["design"].upper(), result["timing_corner"], result["check_type"]
    sources = result["evidence_references"]
    final = result["stage_trends"][-1]
    metric = final["worst_slack"]
    selected = next(
        (row for row in result["critical_paths"] if row["id"] in final["worst_path_ids"]), None
    )
    refs = available_refs(sources, [metric["evidence"]]) if metric else {}
    if selected:
        refs.update(available_refs(sources, selected["evidence"]))

    def summary(ax: Any) -> None:
        rows = [["Final aggregate worst slack", f"{metric['value']:+g} ns"]]
        if selected:
            path = selected["path"]
            rows.extend(
                [
                    ["Startpoint", path["startpoint"]["name"]],
                    ["Endpoint", path["endpoint"]["name"]],
                    ["Path group / classification", f"{path['group']} / {path['classification']}"],
                    ["Printed arrival", f"{path['arrival']['ns']:g} ns"],
                    ["Printed required time", f"{path['required']['ns']:g} ns"],
                    ["Printed path slack", f"{path['slack']['ns']:+g} ns"],
                ]
            )
        table(ax, ["Measurement", "Verified report value"], rows)

    export.plot(
        f"timing_{check}_summary",
        f"{design} final {check} timing",
        f"Sky130HD  |  {corner}  |  OpenSTA retained evidence",
        refs,
        summary,
    )
    if check != "setup":
        return
    stages = result["stage_trends"]
    stage_refs = available_refs(
        sources, [s["worst_slack"]["evidence"] for s in stages if s["worst_slack"]]
    )

    def timing_trend(ax: Any) -> None:
        labels = [item["stage"].upper() for item in stages]
        values = [item["wns_ns"] for item in stages]
        bars(ax, labels, values, "Setup worst negative slack (ns)")

    export.plot(
        "stage_timing_trend",
        f"{design} setup WNS by stage",
        f"{corner}  |  Stage extrema may refer to different paths",
        stage_refs,
        timing_trend,
    )
    if selected:
        b = selected["breakdown"]
        keys = ["cell_delay", "net_delay", "launch_clock", "capture_clock", "clock_effect"]
        if any(b[key] for key in keys):

            def breakdown(ax: Any) -> None:
                rows = [
                    [
                        key.replace("_", " ").capitalize(),
                        f"{b[key]['ns']:+g} ± {b[key]['tolerance_ns']:g} ns"
                        if b[key]
                        else "Unavailable",
                    ]
                    for key in keys
                ]
                table(ax, ["Timing component", "Printed estimate and rounding bound"], rows)

            export.plot(
                "critical_path_breakdown",
                f"{design} critical setup path",
                "OpenSTA incremental accounting  |  Components use different path segments",
                available_refs(sources, selected["evidence"]),
                breakdown,
            )
    if result["matched_paths"]:
        matches = result["matched_paths"]
        comparison_refs = available_refs(
            sources, [ref for item in matches for ref in item["evidence"]]
        )
        export.plot(
            "timing_comparison",
            f"{design} matched endpoint checks",
            "Rounded slack deltas  |  Path topology can change between stages",
            comparison_refs,
            lambda ax: table(
                ax,
                ["Earlier", "Later", "Slack delta", "Same pin sequence"],
                [
                    [
                        item["earlier_stage"],
                        item["later_stage"],
                        f"{item['slack_delta_ns']:+g} ± {item['slack_tolerance_ns']:g} ns",
                        str(item["data_pin_sequence_equal"]),
                    ]
                    for item in matches
                ],
            ),
        )
    physical_figures(export, result)


def physical_figures(export: Export, result: dict[str, Any]) -> None:
    evidence = result["evidence_references"]
    stages = result["stage_trends"]
    design = result["design"].upper()
    for name, label, unit in (
        ("standard_cell_area", "Standard-cell area", "µm²"),
        ("utilization", "Utilization", "fraction"),
        ("standard_cell_count", "Standard-cell count", "cells"),
        ("detailed_route_wirelength", "Detailed-route wirelength", "µm"),
        ("global_route_total_overflow", "Global-route overflow", "tracks"),
        ("flow_errors", "ORFS flow errors", "count"),
    ):
        values = [
            (stage["stage"].upper(), stage["physical"][name])
            for stage in stages
            if name in stage["physical"]
        ]
        refs = available_refs(evidence, [metric["evidence"] for _, metric in values])
        export.plot(
            name,
            f"{design} {label.lower()}",
            "Recorded ORFS stage metrics",
            refs,
            lambda ax: bars(
                ax,
                [stage for stage, _ in values],
                [metric["value"] for _, metric in values],
                f"{label} ({unit})",
            ),
        )
    runtimes = [
        (stage["stage"].upper(), item)
        for stage in stages
        for key, item in stage["physical"].items()
        if key.startswith("stage_runtime.")
    ]
    export.plot(
        "stage_runtime",
        f"{design} controller runtime",
        "Verified controller attempt times",
        available_refs(evidence, [item["evidence"] for _, item in runtimes]),
        lambda ax: bars(
            ax,
            [stage for stage, _ in runtimes],
            [item["value"] for _, item in runtimes],
            "Wall seconds",
        ),
    )
    gates = result["verification_summary"]
    refs = available_refs(
        evidence, [ref for items in result["verification_evidence"].values() for ref in items]
    )
    export.plot(
        "verification_summary",
        f"{design} verification gates",
        f"Acceptance {result['acceptance']}  |  {result['timing_corner']}",
        refs,
        lambda ax: table(
            ax,
            ["Independent gate", "Recorded state"],
            [[key.replace("_", " ").capitalize(), state] for key, state in gates.items()],
        ),
    )


def render_run(root: Path, mode: str = "all") -> dict[str, Any]:
    source = Source.register(root.absolute())
    with TemporaryDirectory(prefix="aeda-visual-") as temp:
        directory = Path(temp)
        work = directory / "output"
        work.mkdir()
        snapshot = Snapshot(source, directory / "snapshot")
        evidence = collect_evidence(snapshot)
        export = Export(source.root, work, evidence["run_id"])
        variant = f"{evidence['platform']}/{evidence['design']}/base"
        if mode != "reports":
            relative = f"results/{variant}/6_final.gds"
            gds = evidence["artifacts"].get(relative)
            try:
                if not gds or gds["availability"] != "VERIFIED" or not gds["sha256"]:
                    raise ValueError("Final GDS is missing or modified")
                staged = snapshot.target / relative
                if sha256(staged) != gds["sha256"]:
                    raise ValueError("Final GDS hash mismatch")
                if evidence["platform"] != "sky130hd":
                    raise ValueError("Layout layer mapping is unavailable")
                shutil.copyfile(staged, work / "source.gds")
                version, config = render_layout(
                    work, identifier(evidence["input_manifest"]["top"], "top")
                )
                for name in VIEWS:
                    export.add(name, "klayout", version, {relative: gds["sha256"]}, config)
            except (OSError, ValueError, subprocess.TimeoutExpired, ModuleNotFoundError) as error:
                for name in VIEWS:
                    if name not in export.images:
                        export.unavailable[name] = (
                            str(error) if isinstance(error, ValueError) else type(error).__name__
                        )
        if mode != "layout":
            for check in ("setup", "hold"):
                report = diagnose_source(evidence["design"], source, check)
                if report.run_id != evidence["run_id"]:
                    raise ValueError("Run identity changed during rendering")
                timing_figures(export, asdict(report))
            power = evidence["metrics"]["estimated_power_w"]
            if power["artifact"] and power["value"] is not None:
                ref = power["artifact"]
                digest = evidence["artifacts"][ref]["sha256"]
                if digest:
                    export.plot(
                        "estimated_power",
                        f"{evidence['design'].upper()} estimated power",
                        f"{evidence['corner']}  |  OpenROAD estimate",
                        {ref: digest},
                        lambda ax: bars(ax, ["Final"], [power["value"]], "Power (W)"),
                    )
        snapshot.verify_source()
        return export.publish(
            replace_types=set(VIEWS)
            if mode == "layout"
            else (set(export.images) | set(export.unavailable) if mode == "reports" else None)
        )


def campaign_figures(export: Export, report: dict[str, Any], sources: dict[str, str]) -> None:
    rows = report["candidates"]
    labels = ["Baseline"] + [
        f"{item['proposal']['parameter_changes']['CORE_UTILIZATION']}% · "
        f"{item['proposal']['strategy']}"
        for item in rows
    ]
    outcomes = [report["baseline"], *(item["outcome"] or {} for item in rows)]
    slack = [outcome.get("measured", {}).get("setup_worst_slack_ns") for outcome in outcomes]
    area = [outcome.get("measured", {}).get("standard_cell_area_um2") for outcome in outcomes]
    title = report["plan"]["baseline"]["design"].upper()
    corner = report["plan"]["baseline"]["corner"]
    status = report["stop_reason"] or report["state"]
    summary_rows = [
        [
            label,
            f"{s:+g} ns" if s is not None else "Unavailable",
            f"{a:g} µm²" if a is not None else "Unavailable",
            outcome.get("physical_feasibility", "UNVERIFIED"),
        ]
        for label, s, a, outcome in zip(labels, slack, area, outcomes, strict=True)
    ]
    export.plot(
        "optimization_summary",
        f"{title} budgeted optimization",
        f"{corner}  |  {status}",
        sources,
        lambda ax: table(
            ax, ["Candidate", "Setup slack", "Cell area", "Physical status"], summary_rows
        ),
    )
    export.plot(
        "candidate_comparison",
        f"{title} setup comparison",
        "Higher slack is better  |  Zero is the setup threshold",
        sources,
        lambda ax: bars(ax, labels, slack, "Final setup worst slack (ns)"),
    )

    def scatter(ax: Any) -> None:
        for index, (label, s, a, outcome) in enumerate(
            zip(labels, slack, area, outcomes, strict=True)
        ):
            if s is None or a is None or not outcome.get("comparable"):
                continue
            color = (
                BLUE
                if label == "Baseline"
                else GREEN
                if outcome["physical_feasibility"] == "FEASIBLE"
                else RED
            )
            ax.scatter(
                a, s, color=color, marker=("D", "o", "s", "^", "P")[index], s=75, label=label
            )
            ax.annotate(
                label.split(" · ")[0],
                (a, s),
                xytext=(0, 9),
                textcoords="offset points",
                ha="center",
                fontsize=9,
            )
        ax.axhline(0, color=GRAY, linestyle="--", linewidth=1)
        ax.set_xlabel("Final standard-cell area (µm²)")
        ax.set_ylabel("Final setup worst slack (ns)")
        ax.legend(loc="best")
        ax.grid(alpha=0.2)

    if any(
        s is not None and a is not None and row.get("comparable")
        for s, a, row in zip(slack, area, outcomes, strict=True)
    ):
        export.plot(
            "area_timing_tradeoff",
            f"{title} area and timing",
            "Comparable measured points  |  Red points fail physical feasibility",
            sources,
            scatter,
        )
    statuses = [
        [
            label,
            "Yes" if item.get("execution_completed") else "Unavailable" if not item else "No",
            item.get("physical_feasibility", "UNVERIFIED"),
            item.get("full_acceptance", "BLOCKED"),
        ]
        for label, item in zip(labels, outcomes, strict=True)
    ]
    export.plot(
        "feasibility_matrix",
        f"{title} feasibility",
        f"Physical frontier: {len(report['feasible_frontiers']['physical'])} candidates",
        sources,
        lambda ax: table(
            ax, ["Candidate", "Flow completed", "Physical", "Full acceptance"], statuses
        ),
    )
    export.plot(
        "search_history",
        f"{title} search history",
        status,
        sources,
        lambda ax: table(
            ax,
            ["Candidate", "State", "Reason"],
            [
                [label, item["state"], item["reason"] or "—"]
                for label, item in zip(labels[1:], rows, strict=True)
            ],
        ),
    )
    rejections = [
        event
        for event in report["events"]
        if event["kind"] == "REJECTED"
        or event["kind"] == "PROPOSER"
        and event["detail"].get("state") == "REJECTED"
    ]
    if rejections:
        export.plot(
            "proposal_rejections",
            f"{title} rejected proposals",
            "Pre-execution policy and model selection events",
            sources,
            lambda ax: table(
                ax,
                ["Event", "Source", "Reason"],
                [
                    [str(event["seq"]), event["kind"], event["detail"].get("reason", "Unavailable")]
                    for event in rejections[:20]
                ],
            ),
        )
    budget = report["budget_consumption"]
    limits = report["plan"]["budget"]
    export.plot(
        "budget_usage",
        f"{title} search budget",
        "One running implementation at a time",
        sources,
        lambda ax: table(
            ax,
            ["Limit", "Consumed", "Authorized"],
            [
                [
                    "Candidate reservations",
                    str(budget["candidates"]),
                    str(limits["maximum_candidates"]),
                ],
                [
                    "Campaign wall time",
                    f"{budget['elapsed_wall_seconds']:.1f} s",
                    f"{limits['maximum_runtime']} s",
                ],
                [
                    "Candidate wall time",
                    f"{budget['candidate_wall_seconds']:.1f} s",
                    "Sum across reservations",
                ],
                ["Memory per container", "Allocation limit", f"{limits['maximum_memory']} MiB"],
            ],
        ),
    )
    actual_strategies = {
        item["proposal"]["strategy"]
        for item in rows
        if item["outcome"] and item["outcome"]["execution_completed"]
    }
    if "guided" in actual_strategies and actual_strategies.intersection({"grid", "random"}):
        export.plot(
            "strategy_comparison",
            f"{title} strategy results",
            "Executed proposals at the same clock and corner",
            sources,
            lambda ax: table(
                ax,
                ["Candidate", "Setup slack", "Cell area", "Physical status"],
                [row for row in summary_rows[1:] if "fallback" not in row[0]],
            ),
        )
    if ids := report["feasible_frontiers"]["physical"]:
        lookup = {item["id"]: row for item, row in zip(rows, summary_rows[1:], strict=True)}
        export.plot(
            "feasible_frontier",
            f"{title} physical Pareto frontier",
            "Candidates with all required gates and declared objective measurements",
            sources,
            lambda ax: table(
                ax,
                ["Candidate", "Setup slack", "Cell area", "Physical status"],
                [lookup[cid] for cid in ids],
            ),
        )


def render_campaign(root: Path, *, include_runs: bool = False) -> dict[str, Any]:
    campaign = Campaign(root)
    report = campaign_report(campaign)
    if not report["candidates"] or report["state"] == "EXECUTING":
        raise ValueError("Render a stopped campaign with recorded candidates")
    report_path = campaign.root / "report.json"
    atomic_json(report_path, report)
    campaign_markdown(campaign.root, report)
    digest = sha256(report_path)
    with TemporaryDirectory(prefix="aeda-campaign-visual-") as temp:
        export = Export(campaign.root, Path(temp), report["optimization_id"])
        campaign_figures(export, report, {"report.json": digest})
        if fingerprint(campaign_report(campaign)) != fingerprint(report):
            raise ValueError("Campaign evidence changed during rendering")
        manifest = export.publish()
    campaign_markdown(campaign.root, report)
    if include_runs:
        manifest["baseline_export"] = render_run(campaign.baseline_root())
        manifest["candidate_exports"] = {
            entry["id"]: render_run(campaign.root / "candidates" / entry["id"])
            for entry in report["candidates"]
            if entry["outcome"] and entry["outcome"]["execution_completed"]
        }
    return manifest


def campaign_markdown(root: Path, report: dict[str, Any]) -> None:
    lines = [
        "# Optimization campaign",
        "",
        f"Campaign `{report['optimization_id']}` ended with "
        f"`{report['stop_reason'] or report['state']}`.",
        "",
        "| Candidate | Core utilization | Strategy | Setup slack (ns) | Cell area (µm²) | Status |",
        "| --- | ---: | --- | ---: | ---: | --- |",
    ]
    for entry in report["candidates"]:
        proposal = entry["proposal"]
        outcome = entry["outcome"] or {}
        values = outcome.get("measured", {})
        util = proposal["parameter_changes"]["CORE_UTILIZATION"]
        slack = values.get("setup_worst_slack_ns")
        area = values.get("standard_cell_area_um2")
        lines.append(
            f"| `{entry['id']}` | {util}% | {proposal['strategy']} | "
            f"{slack if slack is not None else 'Unavailable'} | "
            f"{area if area is not None else 'Unavailable'} | "
            f"{outcome.get('physical_feasibility', 'UNVERIFIED')} |"
        )
    lines.extend(
        [
            "",
            f"Physical feasible frontier size {len(report['feasible_frontiers']['physical'])}.",
            "",
            "[Structured report](report.json)",
        ]
    )
    if (root / "visualizations" / MANIFEST).is_file():
        lines.extend(["", f"[Visual provenance](visualizations/{MANIFEST})"])
    with TemporaryDirectory(prefix=".report-", dir=root) as temp:
        staged = Path(temp) / "report.md"
        staged.write_text("\n".join(lines) + "\n", encoding="utf-8")
        staged.replace(root / staged.name)


def gallery(root: Path) -> dict[str, Any]:
    source = Source.register(root.absolute())
    folder = source.root / "visualizations"
    ordinary(folder, directory=True)
    record = read_manifest(folder / MANIFEST)
    campaign_db = source.root / "campaign.sqlite3"
    approved: dict[str, str | None]
    if campaign_db.is_file():
        # Regenerate from current records and rehash candidate artifacts.
        fresh = campaign_report(Campaign(source.root))
        report = source.root / "report.json"
        ordinary(report, directory=False)
        approved = (
            {"report.json": sha256(report)}
            if fingerprint(fresh) == fingerprint(json.loads(report.read_bytes()))
            else {}
        )
    elif (source.root / "io-report.json").is_file():
        from .io_ring import report as io_report

        path = source.root / "io-report.json"
        ordinary(path, directory=False)
        approved = (
            {path.name: sha256(path)}
            if fingerprint(io_report(source.root)) == fingerprint(json.loads(path.read_bytes()))
            else {}
        )
    else:
        from .verification.report import native_run, read_json, report_run

        approved = {}
        if not native_run(source.root):
            with TemporaryDirectory(prefix="aeda-gallery-") as temp:
                evidence = collect_evidence(Snapshot(source, Path(temp)))
            approved.update(
                (key, item["sha256"])
                for key, item in evidence["artifacts"].items()
                if item["availability"] == "VERIFIED"
            )
        acceptance = source.root / "acceptance.json"
        if acceptance.is_file():
            try:
                saved = read_json(acceptance)
                fresh = report_run(source.root, saved["profile"])
                if fingerprint(saved) == fingerprint(fresh):
                    approved[acceptance.name] = sha256(acceptance)
            except (OSError, ValueError, TypeError, KeyError):
                pass
    for name, image in record["images"].items():
        filename = f"{name}.png"
        if image["filename"] != filename or len(relative_parts(filename)) != 1:
            raise ValueError("Invalid PNG manifest path")
        paths = image["source_artifacts"]
        hashes = image["source_sha256"]
        expected = dict(zip(paths, hashes, strict=True))
        try:
            valid = (
                bool(expected)
                and all(approved.get(path) == digest for path, digest in expected.items())
                and png_size(folder / filename) == (image["image_width"], image["image_height"])
                and (sha256(folder / filename) == image["image_sha256"])
            )
        except (OSError, ValueError, ModuleNotFoundError):
            valid = False
        image["evidence_status"] = "VERIFIED" if valid else "STALE"
    return record

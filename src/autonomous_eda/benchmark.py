"""Generate deterministic benchmark tables and source-bound figures from retained runs."""

from __future__ import annotations

import csv
import json
from functools import partial
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any

from .evidence import Experiment, compare_experiments, get_experiment
from .manifest import fingerprint, sha256
from .optimization import Campaign, campaign_report
from .qualification import qualify
from .structured import resource_page
from .telemetry import encode
from .visual_artifacts import Export
from .visual_charts import bars, table
from .visual_theme import BLUE, RED

VERSION = 1


def compare_input(
    root: Path, label: str, evidence: Experiment, *, trajectory: bool
) -> dict[str, Any]:
    assessment = qualify(root, evidence)
    resources = (
        resource_page(root, limit=32, sample_limit=160 if trajectory else 0)
        if (root / "run.sqlite3").is_file()
        else None
    )
    attempts = resources["attempts"] if resources else []
    latest: dict[str, dict[str, Any]] = {}
    for row in attempts:
        latest.setdefault(row["stage"], row)
    observed_runtime = [
        row["duration_seconds"] for row in attempts if row["duration_seconds"] is not None
    ]
    artifacts = {
        path: artifact
        for path, artifact in evidence["artifacts"].items()
        if artifact["availability"] == "VERIFIED"
    }
    artifact_sources = {name: item["sha256"] for name, item in sorted(artifacts.items())}
    return {
        "label": label,
        "run_id": evidence["run_id"],
        "design": evidence["design"],
        "platform": evidence["platform"],
        "corner": evidence["corner"],
        "manifest": evidence["input_manifest"],
        "source_revision": evidence["source_revision"],
        "toolchain": evidence["toolchain"],
        "assessment": assessment,
        "attempts": attempts,
        "latest": latest,
        "duration_seconds": {
            "value": sum(observed_runtime),
            "source": "sum of verified attempt times including interrupted and inherited attempts",
        }
        if observed_runtime and resources is not None and not resources["next_offset"]
        else None,
        "storage_bytes": {
            "value": sum(item["size"] for item in artifacts.values() if item["size"] is not None),
            "source": "sum of sizes of currently verified artifact paths",
            "evidence_sha256": fingerprint(artifact_sources),
        }
        if artifact_sources
        else None,
        "artifact_sha256": artifact_sources,
        "allocation": {
            key: resources[key]
            for key in ("memory_limit_bytes", "allocated_cpus", "allocation_source")
        }
        if resources
        else None,
    }


def make_report(
    inputs: list[tuple[str, Path]], campaign_root: Path | None = None
) -> dict[str, Any]:
    if not 1 <= len(inputs) <= 12 or len({label for label, _ in inputs}) != len(inputs):
        raise ValueError("benchmark requires one to twelve uniquely named cases")
    evidence = [get_experiment(root) for _, root in inputs]
    cases = [
        compare_input(root, label, run, trajectory=index == len(inputs) - 1)
        for index, ((label, root), run) in enumerate(zip(inputs, evidence, strict=True))
    ]
    comparisons = []
    for index, left in enumerate(evidence):
        for right in evidence[index + 1 :]:
            comparison = compare_experiments(left, right, descriptive=True)
            comparisons.append(
                {
                    "run_ids": [left["run_id"], right["run_id"]],
                    "mode": "descriptive",
                    "controlled_metric_eligible": comparison["compatible"],
                    "identity_differences": sorted(comparison["differences"]),
                    "incompatible_metrics": sorted(comparison["incompatible_metrics"]),
                }
            )
    optimization = None
    if campaign_root:
        campaign = campaign_report(Campaign(campaign_root))
        optimization = {
            "campaign_id": campaign["optimization_id"],
            "plan_sha256": campaign["plan_digest"],
            "approval": campaign["approval"],
            "feasible_frontiers": campaign["feasible_frontiers"],
            "candidates": [
                {key: row.get(key) for key in ("id", "state", "run_id", "reason", "outcome")}
                for row in campaign["candidates"]
            ],
        }
    data = {
        "schema_version": VERSION,
        "cases": cases,
        "comparisons": comparisons,
        "optimization": optimization,
        "notes": [
            "GCD and AES exercise different workloads, clocks, physical sizes, and route states.",
            "Attempt totals can include inherited checkpoints and failed attempts.",
            "Resource peaks are sampled kernel high-water readings. Unavailable metrics are null.",
            "Power is an estimate when present. Global-route timing uses estimated parasitics.",
        ],
    }
    return {**data, "report_sha256": fingerprint(data)}


def rows(report: dict[str, Any]) -> list[list[Any]]:
    return [
        [
            case["label"],
            case["run_id"],
            attempt["id"],
            attempt["stage"],
            attempt["state"],
            attempt["duration_seconds"],
            attempt["exit_code"],
            attempt["log_sha256"],
            attempt["dispatches"]["primary"]["classification"],
            attempt["dispatches"]["primary"]["measurements"]
            .get("memory_kernel_peak_bytes", {})
            .get("value"),
            attempt["dispatches"]["primary"].get("receipt_sha256"),
        ]
        for case in report["cases"]
        for attempt in reversed(case["attempts"])
    ]


def figure_set(root: Path, report: dict[str, Any], command: list[str]) -> dict[str, Any]:
    cases = report["cases"]
    source = {"report.json": sha256(root / "report.json")}
    names = [case["label"] for case in cases]
    latest = next((case for case in reversed(cases) if "route" in case["latest"]), cases[-1])
    route = latest["latest"].get("route", {}).get("dispatches", {}).get("primary", {})
    series = route.get("samples", [])
    with TemporaryDirectory(prefix="aeda-benchmark-figures-") as temp:
        export = Export(root, Path(temp), report["report_sha256"])
        export.plot(
            "benchmark_comparison",
            "Benchmark attempt time",
            "Descriptive comparison · Includes failed and inherited attempts",
            source,
            lambda ax: bars(
                ax,
                names,
                [(case["duration_seconds"] or {}).get("value") for case in cases],
                "Observed attempt time (s)",
            ),
        )
        stage_rows = [
            [case["label"], stage, f"{row['duration_seconds']:.3f}", row["state"]]
            for case in cases
            for stage in ("synth", "grt", "route", "finish")
            if (row := case["latest"].get(stage)) and row["duration_seconds"] is not None
        ]
        export.plot(
            "stage_duration_comparison",
            "Selected stage durations",
            "Latest recorded attempt by stage",
            source if stage_rows else {},
            partial(table, columns=["Case", "Stage", "Seconds", "State"], rows=stage_rows),
        )
        gate_rows = [
            [
                case["label"],
                case["assessment"]["implementation_status"],
                case["assessment"]["physical_acceptance_status"],
                case["assessment"]["design_qualification_status"],
            ]
            for case in cases
        ]
        export.plot(
            "verification_acceptance",
            "Independent acceptance outcomes",
            "Evidence-backed decisions",
            source,
            partial(
                table, columns=["Case", "Implementation", "Physical", "Design"], rows=gate_rows
            ),
        )
        for name, field, title, unit, scale in (
            (
                "routing_resource_timeline",
                "cpu_usage_usec",
                "Detailed-route CPU time",
                "CPU seconds",
                10**6,
            ),
            (
                "routing_memory",
                "memory_current_bytes",
                "Detailed-route memory",
                "Memory (MiB)",
                2**20,
            ),
        ):
            values = [
                (item["elapsed_seconds"], item[field] / scale) for item in series if field in item
            ]

            def draw(
                ax: Any, *, values: list[Any] = values, unit: str = unit, field: str = field
            ) -> None:
                ax.plot(
                    [row[0] for row in values],
                    [row[1] for row in values],
                    color=BLUE,
                    label="Usage",
                )
                if field == "memory_current_bytes":
                    peaks = [
                        (p["elapsed_seconds"], p["memory_kernel_peak_bytes"] / 2**20)
                        for p in series
                        if "memory_kernel_peak_bytes" in p
                    ]
                    if peaks:
                        ax.plot(
                            [p[0] for p in peaks],
                            [p[1] for p in peaks],
                            color=RED,
                            label="Kernel high-water",
                        )
                    ax.legend(loc="upper left")
                ax.set_xlabel("Elapsed seconds")
                ax.set_ylabel(unit)

            export.plot(
                name,
                title,
                f"{latest['label']} · {latest['run_id'][:12]}",
                source if values else {},
                draw,
            )
        for key, receipt in export.images.items():
            receipt["source_run_ids"] = (
                [latest["run_id"]]
                if key.startswith("routing_")
                else [case["run_id"] for case in cases]
            )
            receipt["generation_command"] = command
        return export.publish()


def write_report(root: Path, report: dict[str, Any], command: list[str]) -> None:
    root.mkdir(parents=True, exist_ok=False)
    (root / "report.json").write_text(encode(report), encoding="utf-8", newline="\n")
    with (root / "stages.csv").open("w", encoding="utf-8", newline="") as stream:
        writer = csv.writer(stream, lineterminator="\n")
        writer.writerow(
            [
                "case",
                "run_id",
                "attempt",
                "stage",
                "state",
                "duration_seconds",
                "exit_code",
                "log_sha256",
                "classification",
                "memory_peak_bytes",
                "resource_receipt_sha256",
            ]
        )
        writer.writerows(rows(report))
    with (root / "runs.csv").open("w", encoding="utf-8", newline="") as stream:
        writer = csv.writer(stream, lineterminator="\n")
        writer.writerow(
            [
                "case",
                "run_id",
                "design",
                "duration_seconds",
                "storage_bytes",
                "memory_limit_bytes",
                "cpu_cores",
                "implementation",
                "physical",
                "qualification",
                "readiness_level",
            ]
        )
        for case in report["cases"]:
            assessment = case["assessment"]
            writer.writerow(
                [
                    case["label"],
                    case["run_id"],
                    case["design"],
                    (case["duration_seconds"] or {}).get("value"),
                    (case["storage_bytes"] or {}).get("value"),
                    (case["allocation"] or {}).get("memory_limit_bytes"),
                    (case["allocation"] or {}).get("allocated_cpus"),
                    assessment["implementation_status"],
                    assessment["physical_acceptance_status"],
                    assessment["design_qualification_status"],
                    assessment["readiness_level"],
                ]
            )
    lines = [
        "# Benchmark report",
        "",
        f"Evidence SHA-256 `{report['report_sha256']}`",
        "",
        "| Case | Implementation | Physical | Design |",
        "| --- | --- | --- | --- |",
    ]
    for case in report["cases"]:
        state = case["assessment"]
        label = case["label"].replace("|", "\\|")
        lines.append(
            f"| {label} | {state['implementation_status']} | "
            f"{state['physical_acceptance_status']} | {state['design_qualification_status']} |"
        )
    lines.extend(["", *(f"- {note}" for note in report["notes"]), ""])
    (root / "report.md").write_text("\n".join(lines), encoding="utf-8", newline="\n")
    figure_set(root, report, command)


def rebuild(root: Path, command: list[str]) -> dict[str, Any]:
    with (root / "report.json").open("rb") as stream:
        report = json.loads(stream.read(16 * 2**20 + 1))
    source = {key: value for key, value in report.items() if key != "report_sha256"}
    if report.get("schema_version") != VERSION or fingerprint(source) != report.get(
        "report_sha256"
    ):
        raise ValueError("benchmark report digest or schema changed")
    return figure_set(root, report, command)

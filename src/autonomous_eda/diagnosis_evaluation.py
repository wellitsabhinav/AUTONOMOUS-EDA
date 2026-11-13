"""Evaluate the retained timing cases and fail-closed behavior on disposable copies."""

from __future__ import annotations

import argparse
import hashlib
import json
from collections.abc import Callable
from dataclasses import asdict, dataclass, replace
from pathlib import Path
from tempfile import TemporaryDirectory
from time import perf_counter
from typing import Any

from .ai.cli import load_settings
from .ai.journal import Journal, State
from .ai.supervisor import Settings, Supervisor
from .diagnosis import DiagnosticReport, diagnose_timing
from .knowledge.sources import decoded, read_file
from .mcp.registry import Registry
from .mcp.snapshot import MAX_TEXT_BYTES, Source


@dataclass(frozen=True)
class Baseline:
    slack: float
    hold: float
    clock: float
    start: str
    end: str
    arrival: float
    required: float
    cell_ns: float
    net_ns: float
    earlier_stage: str
    matched_delta: float


BASELINES = {
    "gcd": Baseline(
        -1.20488,
        0.429847,
        1.1,
        "dpath.b_reg.out[2]$_DFFE_PP_",
        "dpath.a_reg.out[10]$_DFFE_PP_",
        2.35,
        1.14,
        2.04,
        0.0,
        "grt",
        0.03,
    ),
    "ibex": Baseline(
        -0.377013,
        0.426791,
        8.0,
        "if_stage_i.instr_rdata_id_o[20]$_DFFE_PP_",
        "gen_regfile_ff.register_file_i.rf_reg[58]$_DFFE_PN0P_",
        9.37,
        8.99,
        8.09,
        0.03,
        "cts",
        -0.26,
    ),
}


def score(report: dict[str, Any], design: str, case: str) -> dict[str, bool]:
    expected = BASELINES[design]
    final = report["stage_trends"][-1]
    ids = final["worst_path_ids"]
    selected = [p for p in report["critical_paths"] if p["id"] in ids]
    base = {
        "factual_correctness": final["worst_slack"]["value"] == expected.slack
        and final["constraints"]["clock_periods"]["core_clock"]["value"] == expected.clock
        and report["verification_summary"]["setup"] == "FAILED"
        and all(
            report["verification_summary"][g] == "PASSED" for g in ("hold", "platform_drc", "lvs")
        ),
        "citation_references": all(
            ref in report["evidence_references"]
            for key in (
                "observed_findings",
                "derived_findings",
                "hypotheses",
                "suggested_investigations",
            )
            for item in report[key]
            for ref in item["evidence"]
        ),
        "actionability": all(
            item["why"]
            and item["question"]
            and item["tool_capability"]
            and item["evidence"]
            and item["coverage"] in {"AVAILABLE", "PARTIAL", "UNAVAILABLE"}
            for item in report["suggested_investigations"]
        ),
    }
    if case != "baseline":
        gap = {
            "malformed": "INVALID",
            "ambiguous": "WORST_PATH_AMBIGUOUS",
            "missing": "WORST_PATH_UNRESOLVED",
        }[case]
        base["correct_abstention"] = (
            len(ids) != 1
            and not report["hypotheses"]
            and any(item["code"] == gap for item in report["missing_measurements"])
        )
        return base
    path = selected[0]["path"] if len(selected) == 1 else {}
    delay = selected[0]["breakdown"] if len(selected) == 1 else {}
    base["path_extraction"] = (
        path.get("classification") == "register-to-register"
        and path.get("startpoint", {}).get("name") == expected.start
        and path.get("endpoint", {}).get("name") == expected.end
        and path["group"] == "core_clock"
    )
    base["delay_accounting"] = bool(
        path
        and delay.get("cell_delay")
        and delay.get("net_delay")
        and path["arrival"]["ns"] == expected.arrival
        and path["required"]["ns"] == expected.required
        and delay["cell_delay"]["ns"] == expected.cell_ns
        and delay["net_delay"]["ns"] == expected.net_ns
        and abs(path["calculated_slack_ns"] - path["slack"]["ns"]) <= path["slack_tolerance_ns"]
    )
    base["stage_correlation"] = any(
        trend["earlier_stage"] == expected.earlier_stage
        and trend["later_stage"] == "finish"
        and trend["later_path_id"] in ids
        and trend["slack_delta_ns"] == expected.matched_delta
        for trend in report["matched_paths"]
    )
    base["actionability"] &= len(report["suggested_investigations"]) >= 2
    base["hypothesis_labels"] = bool(report["hypotheses"]) and all(
        item["strength"] in {"SUPPORTED_HYPOTHESIS", "WEAK_HYPOTHESIS"}
        for item in report["hypotheses"]
    )
    return base


def line_citations(report: DiagnosticReport, source: Source) -> tuple[int, int]:
    cached: dict[str, tuple[str, list[str]]] = {}
    total = valid = 0
    for cite in report.evidence_references.values():
        if cite.line_start is None or cite.line_end is None:
            continue
        if cite.path not in cached:
            raw = read_file(source, cite.path, MAX_TEXT_BYTES)
            cached[cite.path] = hashlib.sha256(raw).hexdigest(), decoded(raw).split("\n")
        digest, lines = cached[cite.path]
        total += 1
        if (
            digest == cite.sha256
            and 1 <= cite.line_start <= cite.line_end <= len(lines)
            and lines[cite.line_start - 1].strip()
            and lines[cite.line_end - 1].strip()
        ):
            valid += 1
    return valid, total


def modified_case(source: Source, target: Path, case: str) -> Source:
    # Use the snapshot implementation to copy only approved ordinary files.
    from .mcp.snapshot import Snapshot

    snapshot = Snapshot(source, target)
    snapshot.collect()
    snapshot.verify_source()
    index = target / "reference.json"
    if not index.is_file():
        raise ValueError("Evaluation mutations require a reference archive")
    name = "reports/sky130hd/gcd/base/6_finish.rpt"
    path = target / name
    if case == "missing":
        path.unlink()
    else:
        content = path.read_bytes()
        if case == "malformed":
            content = content.replace(b"Delay    Time   Description", b"Broken delay table")
        else:
            start = content.index(b"Startpoint: dpath.b_reg.out[2]")
            end = content.index(b"slack (VIOLATED)", start) + len(b"slack (VIOLATED)")
            content += b"\n==========\nreport_checks -path_delay max\n" + content[start:end] + b"\n"
        path.write_bytes(content)
        record = json.loads(index.read_bytes())
        record["sha256"][name] = hashlib.sha256(content).hexdigest()
        index.write_text(json.dumps(record), encoding="utf-8")
    return Source.register(target)


def measured(call: Callable[[], Any]) -> tuple[Any, float]:
    start = perf_counter()
    value = call()
    return value, round((perf_counter() - start) * 1000, 2)


def evaluate(settings: Settings, workspace: Path) -> list[dict[str, Any]]:
    results = []
    cases = [(design, "baseline") for design in BASELINES]
    cases.extend(("gcd", kind) for kind in ("malformed", "ambiguous", "missing"))
    for design, case in cases:
        registry = settings.registry
        if case != "baseline":
            source = modified_case(registry.experiments[design], workspace / case, case)
            registry = Registry(registry.roots, {**registry.experiments, design: source})
        report, elapsed = measured(lambda: diagnose_timing(registry, design))
        offline = asdict(report)
        valid, citations = line_citations(report, registry.experiments[design])
        checks = score(offline, design, case)
        deterministic = {
            "checks": checks,
            "latency_ms": elapsed,
            "model_cost_microdollars": 0,
            "unsupported_causal_claims": 0,
            "line_citations_valid": valid,
            "line_citations_total": citations,
        }
        if case == "baseline":
            hold = diagnose_timing(registry, design, "hold").stage_trends[-1].worst_slack
            checks["hold_worst_slack"] = hold is not None and hold.value == BASELINES[design].hold
        checks["line_citations"] = valid == citations and citations > 0
        journal = Journal(workspace / f"{design}-{case}.sqlite3")
        supervisor = Supervisor(journal, replace(settings, registry=registry))
        model, latency = measured(
            lambda: supervisor.start(
                design,
                "Diagnose setup timing and physical implementation.",
                diagnosis_check="setup",
            )
        )
        attempts = model["model_attempts"]
        replay, replay_ms = measured(lambda: supervisor.details(model["workflow_id"]))
        result = model["output"]
        selected = (
            [
                *result["hypotheses"],
                *result["recommended_next_measurements"],
                *(
                    [result["engineering_interpretation"]]
                    if result["engineering_interpretation"]
                    else []
                ),
            ]
            if result
            else []
        )
        supplied = {item["id"] for item in offline["hypotheses"]}
        measurements = {item["id"] for item in offline["suggested_investigations"]}
        factual = result is not None and result["observation"]["diagnosis"] == json.loads(
            json.dumps(offline)
        )
        assisted = {
            "state": model["state"],
            "reason": model["reason"],
            "latency_ms": latency,
            "model_cost_microdollars": sum(a["cost_charged"] for a in attempts),
            "attempts": attempts,
            "selected_ids": [item["id"] for item in selected],
            "selected_measurements": [
                item["id"] for item in (result["recommended_next_measurements"] if result else [])
            ],
            "unsupported_causal_claims": sum(
                item["id"] not in supplied for item in (result["hypotheses"] if result else [])
            ),
            "checks": {
                "shared_facts_preserved": factual,
                "selection_citations": all(
                    set(item["evidence"]) <= result["supporting_evidence"].keys()
                    for item in selected
                ),
                "correct_workflow_outcome": model["state"]
                == (State.COMPLETED if case == "baseline" else State.INSUFFICIENT_EVIDENCE)
                and (case == "baseline" or not attempts),
                "replay_consistent": replay["historical"] and replay["output"] == result,
                "selected_measurements_actionable": all(
                    item["id"] in measurements
                    for item in (result["recommended_next_measurements"] if result else [])
                ),
            },
            "replay_ms": replay_ms,
        }
        results.append(
            {"design": design, "case": case, "deterministic": deterministic, "assisted": assisted}
        )
    return results


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("config", type=Path)
    args = parser.parse_args()
    settings = load_settings(args.config)
    with TemporaryDirectory(prefix="aeda-diagnosis-eval-") as workspace:
        results = evaluate(settings, Path(workspace))
    print(json.dumps({"schema_version": 1, "cases": results}, indent=2, allow_nan=False))
    return int(
        not all(
            all(mode["checks"].values())
            for result in results
            for mode in (result["deterministic"], result["assisted"])
        )
    )


if __name__ == "__main__":
    raise SystemExit(main())

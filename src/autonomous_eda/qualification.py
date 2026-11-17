"""Evaluate independent implementation, physical and design qualification evidence."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any, cast

from .evidence import Experiment, checked_artifact_bytes, get_experiment, verified_artifact
from .manifest import fingerprint
from .opentitan_physical import RTL_REVISION
from .verification.policy import PHYSICAL, State, verdict
from .verification.report import report_run

FINAL = {
    "database": "6_final.odb",
    "netlist": "6_final.v",
    "constraints": "6_final.sdc",
    "parasitics": "6_final.spef",
    "streamout": "6_final.gds",
}
PHYSICAL_CHECKS = ("router", "platform_drc", "lvs", "gds_reopen", "artifact_integrity")
DESIGN_CHECKS = ("elaboration", "functional_regression", "equivalence")


def levels(
    mapped: bool,
    native_implementation: str,
    extraction: str,
    timing: str,
    gate_states: dict[str, State],
    interface: str,
) -> tuple[int | None, str, str, str]:
    evaluated = timing in {"PASSED", "FAILED"}
    measured = all(gate_states.get(name) == "PASSED" for name in PHYSICAL_CHECKS)
    level = 2 if mapped else None
    if not mapped and gate_states.get("elaboration") == "PASSED":
        level = 1
    physically_verified = (
        native_implementation == extraction == "PASSED"
        and measured
        and evaluated
        and gate_states.get("timing_coverage") in {"PASSED", "FAILED"}
    )
    if physically_verified:
        level = 3
    if native_implementation != "PASSED":
        physical = "BLOCKED"
    else:
        mandatory = [gate_states.get(name, "BLOCKED") for name in (*PHYSICAL, "artifact_integrity")]
        physical = verdict(
            [
                cast(State, extraction),
                *(state if state in {"PASSED", "FAILED"} else "BLOCKED" for state in mandatory),
            ]
        )
    checks = [physical, interface, *(gate_states.get(name, "UNKNOWN") for name in DESIGN_CHECKS)]
    qualification = "PASSED" if all(item == "PASSED" for item in checks) else "BLOCKED"
    if any(gate_states.get(name) == "FAILED" for name in DESIGN_CHECKS) or physical == "FAILED":
        qualification = "FAILED"
    if qualification == "PASSED" and level == 3:
        level = 4
    return level, native_implementation, physical, qualification


def qualify(
    root: Path,
    evidence: Experiment | None = None,
    matrix: dict[str, Any] | None = None,
) -> dict[str, Any]:
    root = root.resolve()
    evidence = evidence if evidence is not None else get_experiment(root)
    matrix = matrix if matrix is not None else report_run(root)
    variants = [
        path
        for path in evidence["artifacts"]
        if path.startswith("results/") and path.endswith("/6_final.spef")
    ]
    artifacts = evidence["artifacts"]
    paths = {}
    log = None
    if len(variants) == 1:
        variant = str(Path(variants[0]).parent).replace("\\", "/")
        paths = {name: f"{variant}/{filename}" for name, filename in FINAL.items()}
        log = "logs/" + variant.removeprefix("results/") + "/6_report.log"
    products = {
        name: artifacts[path] if path and verified_artifact(artifacts, path) else None
        for name in FINAL
        for path in [paths.get(name)]
    }
    rcx = None
    if log and verified_artifact(artifacts, log):
        text = checked_artifact_bytes(root, log, artifacts[log])
        if text is not None:
            match = re.search(rb"\[INFO RCX-0443\] ([1-9][0-9]*) nets finished\b", text)
            if match:
                rcx = {
                    "value": int(match[1]),
                    "unit": "net",
                    "artifact": log,
                    "sha256": artifacts[log]["sha256"],
                    "source": "OpenRCX RCX-0443",
                }
    extraction = "PASSED" if products["parasitics"] and rcx is not None else "BLOCKED"
    implementation = matrix["verdicts"]["implementation_completion"]
    attempts = {row["stage"]: row["state"] for row in evidence["attempts"]}
    if (
        all(products.values())
        and attempts.get("route") == "passed"
        and not evidence["provenance"].get("input_error")
    ):
        implementation = "PASSED"
    elif implementation == "PASSED":
        implementation = "BLOCKED"
    states = {name: cast(State, row["status"]) for name, row in matrix["gates"].items()}
    timing = [states.get("setup", "UNKNOWN"), states.get("hold", "UNKNOWN")]
    extracted_sta = (
        verdict(timing)
        if extraction == "PASSED" and all(state in {"PASSED", "FAILED"} for state in timing)
        else "BLOCKED"
    )
    is_aes = evidence["input_manifest"].get("design") == "opentitan_aes"
    aes_evidence = None
    if is_aes:
        from .aes_qualification import read

        aes_evidence = read(root, evidence)
    interface_status = "BLOCKED" if is_aes else "UNKNOWN"
    interface = {
        "status": interface_status,
        "bound_rtl_revision": RTL_REVISION if is_aes and evidence["rtl_identity"] else None,
        "clock_ports": ["clk_i", "clk_edn_i"] if is_aes else [],
        "reset_ports": ["rst_ni", "rst_shadowed_ni", "rst_edn_ni"] if is_aes else [],
        "unqualified": [
            "EDN clock relationship",
            "clock uncertainty",
            "reset and CDC assumptions",
            "input and output delays for the TL-UL, key manager, alerts and EDN interfaces",
        ]
        if is_aes
        else [],
    }
    if aes_evidence and "clock_reset_structure" in aes_evidence["gates"]:
        interface["unqualified"] = list(aes_evidence["obligations"].values())
        interface["evidence_sha256"] = aes_evidence["qualification_receipt_sha256"]
    level, implementation, physical, qualification = levels(
        states.get("synthesis") == "PASSED",
        implementation,
        extraction,
        extracted_sta,
        states,
        interface_status,
    )
    report = {
        "schema_version": 1,
        "run_id": evidence["run_id"],
        "verification_sha256": matrix["decision_sha256"],
        "implementation_status": implementation,
        "physical_acceptance_status": physical,
        "design_qualification_status": qualification,
        "readiness_level": level,
        "final_products": products,
        "extraction_status": extraction,
        "extracted_nets": rcx,
        "extracted_timing_status": extracted_sta,
        "timing_contract": interface,
        "verification_gates": states,
        "timing_context": (
            matrix["gates"].get("timing_coverage", {}).get("context") or matrix["timing_context"]
        ),
        "measurements": {
            name: metric
            for name, metric in evidence["metrics"].items()
            if name
            in {
                "setup_worst_slack_ns",
                "hold_worst_slack_ns",
                "setup_tns_ns",
                "hold_tns_ns",
                "routed_wirelength_um",
                "standard_cell_area_um2",
                "estimated_power_w",
                "router_drc_errors",
                "stage.grt.setup_worst_slack",
                "stage.grt.hold_worst_slack",
                "stage.finish.utilization",
            }
        },
    }
    if aes_evidence is not None:
        report["aes_qualification"] = aes_evidence
    return {**report, "qualification_sha256": fingerprint(report)}

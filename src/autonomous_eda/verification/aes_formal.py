"""Bounded correspondence for the AES key and IV register-status partitions."""

from __future__ import annotations

import shutil
import time
from dataclasses import asdict
from pathlib import Path
from typing import Any

from ..manifest import RTLManifest, sha256
from .process import Execution, execute, log_text

WIDTHS = (8, 16)
DEPTH = 8
SOLVER_SECONDS = 20


def proof_status(run: Execution, output: str) -> str:
    if run.stop == "timeout" or "SAT solver timed out" in output:
        return "TIMEOUT"
    if run.stop == "cancelled":
        return "BLOCKED"
    if run.stop is None and "SAT proof finished - model found: FAIL!" in output:
        return "FAIL"
    if (
        run.stop is None
        and run.return_code == 0
        and output.count("SAT proof finished - no model found: SUCCESS!") == 1
    ):
        return "PASS"
    return "INFRASTRUCTURE_ERROR"


def partition_proof(
    rtl: RTLManifest, translation: Path, work: Path, *, timeout: int
) -> dict[str, Any]:
    module = "aes_reg_status"
    (source,) = (path for path in rtl.sources if path.name == f"{module}.sv")
    report: dict[str, Any] = {
        "status": "BLOCKED",
        "diagnostic": "Yosys is unavailable",
        "module": module,
        "widths": list(WIDTHS),
        "depth": DEPTH,
        "solver_seconds": SOLVER_SECONDS,
        "inputs": {source.name: sha256(source), "translation.v": sha256(translation)},
        "assumptions": {
            "state": "No assumed initial match; compare all outputs after an observed reset step",
            "reset": "Both models see active-low reset in step 1, then unconstrained reset",
            "clock": "One common abstract clock step with async2sync reset semantics",
            "entropy": "No entropy input on this partition",
            "logic": "Defined binary inputs, no four-state X/Z or physical reset timing proof",
            "outputs": "new_o, new_pulse_o and clean_o",
        },
    }
    tool = shutil.which("yosys")
    if tool is None:
        return report
    binary = Path(tool).resolve()
    work.mkdir()
    for path, name in ((source, source.name), (translation, "translation.v")):
        shutil.copyfile(path, work / name)
        if sha256(work / name) != report["inputs"][name]:
            raise ValueError("AES partition proof input changed during staging")
    cases = {}
    for width in WIDTHS:
        counterexample = f"width-{width}-counterexample.json"
        script = work / f"width-{width}.ys"
        top = f"hierarchy -check -top {module} -chparam Width {width}"
        script.write_text(
            f"read_verilog -sv {source.name}\n{top}\nproc\nflatten\n"
            f"rename {module} original\ndesign -stash original\n"
            f"read_verilog translation.v\n{top}\nproc\nflatten\n"
            f"rename {module} translated\ndesign -stash translated\n"
            "design -copy-from original original\ndesign -copy-from translated translated\n"
            "miter -equiv -flatten original translated miter\nhierarchy -check -top miter\n"
            "async2sync\nopt_clean\n"
            f"sat -seq {DEPTH} -timeout {SOLVER_SECONDS} -set-def-inputs -set-at 1 in_rst_ni 0 "
            "-prove-skip 1 -prove trigger 0 -show-inputs -show-outputs -verify "
            f"-dump_json {counterexample} miter\n",
            encoding="utf-8",
        )
        run = execute(
            [str(binary), "-Q", "-s", script.name],
            work,
            f"proof-{width}",
            time.monotonic() + timeout,
            lambda: False,
        )
        names = [script.name, run.stdout, run.stderr]
        if (work / counterexample).is_file():
            names.append(counterexample)
        cases[str(width)] = {
            "status": proof_status(run, log_text(work, run.stdout) + log_text(work, run.stderr)),
            "execution": asdict(run),
            "outputs": {name: sha256(work / name) for name in names},
        }
    status = next((case["status"] for case in cases.values() if case["status"] != "PASS"), "PASS")
    report.update(
        status=status,
        diagnostic="Bounded SAT correspondence of the key and IV register-status instances",
        yosys_sha256=sha256(binary),
        cases=cases,
    )
    return report

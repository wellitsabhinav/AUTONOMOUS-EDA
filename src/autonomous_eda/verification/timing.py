"""Coverage checks on the recorded final OpenSTA constraint context."""

from __future__ import annotations

import re
import shutil
import subprocess
import sys
import uuid
from collections.abc import Callable
from dataclasses import asdict
from pathlib import Path
from typing import Any

from ..evidence import SKY130_LIBRARY, Experiment
from ..manifest import identifier, sha256
from ..orfs import (
    DispatchStopped,
    execute,
    record_interrupted_dispatch,
    remove_interrupted_container,
)
from ..reference import IMAGE, ORFS_ROOT, REVISION
from ..telemetry import ResourceContext, owned_attempt, receipt_path, timestamp
from ..timing import parse_timing_report
from .policy import Policy, State
from .process import Execution, log_text
from .rtl import stage

CHECKS = (
    "no_clock",
    "multiple_clock",
    "unconstrained_endpoints",
    "no_input_delay",
    "no_output_delay",
    "generated_clocks",
    "loops",
)
ENGINE = f"{ORFS_ROOT}/tools/install/OpenROAD/bin/openroad"
DISPATCH = "dispatch.txt"
SDC_COMMANDS = {
    "current_design",
    "create_clock",
    "set_propagated_clock",
    "set_clock_latency",
    "set_clock_uncertainty",
    "set_clock_transition",
    "set_input_delay",
    "set_output_delay",
    "set_load",
    "set_driving_cell",
    "set_input_transition",
    "set_max_transition",
    "set_max_capacitance",
    "set_max_fanout",
}
MAX_TEXT = 4 * 1024**2


def variant(evidence: Experiment) -> str:
    return f"{evidence['platform']}/{evidence['design']}/base"


def paths(evidence: Experiment) -> tuple[str, str, str, str]:
    base = variant(evidence)
    results = f"results/{base}"
    return (
        f"{results}/1_synth.sdc",
        f"{results}/6_final.sdc",
        f"{results}/6_final.odb",
        f"{results}/6_final.spef",
    )


def sdc_clocks(text: str) -> tuple[dict[str, float], list[str]]:
    clocks: dict[str, float] = {}
    unsupported: set[str] = set()
    for line in text.replace("\\\n", " ").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        command = line.split()[0]
        if command not in SDC_COMMANDS:
            unsupported.add(command[:80])
        if re.search(r"[;$]|\x00|\\(?![\[\]])|\[(?!get_(?:clocks|ports|pins)\s|\d+\])", line):
            unsupported.add("unsupported SDC substitution or statement syntax")
        if command == "create_clock":
            name = re.search(r"(?:^|\s)-name ([A-Za-z_][A-Za-z0-9_]*)\b", line)
            period = re.search(r"(?:^|\s)-period ([0-9]+(?:\.[0-9]+)?)(?:\s|$)", line)
            if not name or not period or name[1] in clocks or float(period[1]) <= 0:
                unsupported.add("unrecognized or duplicate create_clock")
            else:
                clocks[name[1]] = float(period[1])
    return clocks, sorted(unsupported)


def inspect_context(
    root: Path,
    evidence: Experiment,
    policy: Policy,
) -> tuple[dict[str, Any], list[str], str | None]:
    synth, final, _, _ = paths(evidence)
    artifacts = evidence["artifacts"]
    context: dict[str, Any] = {
        "corner": evidence["corner"],
        "clock_period_ns": evidence["metrics"]["clock_period_ns"]["value"],
        "sdc_sha256": artifacts[final]["sha256"] if final in artifacts else None,
        "synthesis_sdc_sha256": artifacts[synth]["sha256"] if synth in artifacts else None,
        "clocks": {},
        "unsupported_sdc_commands": [],
        "coverage_checks": {name: "UNKNOWN" for name in CHECKS},
    }
    refs = [synth, final]
    if (
        evidence["toolchain"]["image"] != IMAGE
        or evidence["source_revision"] != REVISION
        or evidence["platform"] != "sky130hd"
        or evidence["corner"] != "tt_025C_1v80"
        or evidence["toolchain"]["library"] != SKY130_LIBRARY
    ):
        return context, refs, "Final corner or timing toolchain is unavailable or unsupported"
    if policy.timing.corner is not None and policy.timing.corner != evidence["corner"]:
        return context, refs, "Configured timing corner differs from the retained final corner"
    for rel in refs:
        entry = artifacts.get(rel)
        if not entry or entry["availability"] != "VERIFIED":
            return context, refs, f"Constraint artifact is missing or modified: {rel}"
    if policy.timing.sdc_sha256 and policy.timing.sdc_sha256 != context["sdc_sha256"]:
        return context, refs, "Configured final SDC SHA-256 differs from the retained SDC"
    clocks_by_stage = []
    unsupported = set()
    for rel in refs:
        with (root / rel).open("rb") as stream:
            raw = stream.read(MAX_TEXT + 1)
        if len(raw) > MAX_TEXT:
            return context, refs, "SDC exceeds the timing inspection size limit"
        clocks, unknown = sdc_clocks(raw.decode("utf-8", errors="replace"))
        clocks_by_stage.append(clocks)
        unsupported.update(unknown)
    context["clocks"] = clocks_by_stage[1]
    context["unsupported_sdc_commands"] = sorted(unsupported)
    expected = policy.timing.clocks or tuple(clocks_by_stage[0])
    context["expected_clocks"] = expected
    context["missing_clocks"] = sorted(set(expected) - context["clocks"].keys())
    context["unaccounted_clocks"] = sorted(context["clocks"].keys() - set(expected))
    if not clocks_by_stage[0]:
        return context, refs, "Original clock inventory could not be established"
    if unsupported:
        return context, refs, "Unsupported SDC commands or timing exceptions require review"
    if (
        clocks_by_stage[0] != clocks_by_stage[1]
        or context["missing_clocks"]
        or context["unaccounted_clocks"]
        or evidence["input_manifest"].get("clock_period_ns") != context["clock_period_ns"]
    ):
        return context, refs, "Final clock inventory differs from the recorded constraint context"
    return context, refs, None


def retained_failure(root: Path, evidence: Experiment) -> tuple[str, str] | None:
    rel = f"reports/{variant(evidence)}/6_finish.rpt"
    artifact = evidence["artifacts"].get(rel)
    if not artifact or artifact["availability"] != "VERIFIED":
        return None
    with (root / rel).open("rb") as stream:
        raw = stream.read(MAX_TEXT + 1)
    if len(raw) > MAX_TEXT:
        return None
    parsed = parse_timing_report(raw, default_unit="ns")
    for path in parsed.paths:
        if path.status == "UNCONSTRAINED" and path.endpoint:
            return rel, f"OpenSTA reported an unconstrained endpoint: {path.endpoint.name}"
    return None


def parse_coverage(text: str) -> tuple[dict[str, State], list[str]]:
    checks: dict[str, State] = {}
    observed: list[str] = []
    completed = text.splitlines().count("AEDA_COVERAGE_COMPLETE") == 1
    for name in CHECKS:
        matches = re.findall(rf"^AEDA_CHECK {name} ([01])\s*$", text, re.M)
        checks[name] = (
            ("PASSED" if matches[0] == "1" else "FAILED")
            if completed and len(matches) == 1
            else "UNKNOWN"
        )
    if completed:
        observed = re.findall(r"^AEDA_CLOCK ([A-Za-z_][A-Za-z0-9_]*)\s*$", text, re.M)
    return checks, observed


def run_coverage(
    root: Path,
    work: Path,
    evidence: Experiment,
    policy: Policy,
    cancelled: Callable[[], bool],
) -> dict[str, Any]:
    files: dict[str, str] = {}
    runs: list[Execution] = []
    tools: dict[str, dict[str, str]] = {}
    state: State = "BLOCKED"
    try:
        _, refs, problem = inspect_context(root, evidence, policy)
        if problem:
            raise ValueError(problem)
        docker = shutil.which("docker") if sys.platform == "linux" else None
        if not docker:
            raise ValueError("Pinned OpenSTA dispatch requires Linux with the cached ORFS image")
        _, final, odb, spef = paths(evidence)
        for name in (*refs, odb, spef):
            artifact = evidence["artifacts"].get(name)
            if not artifact or artifact["availability"] != "VERIFIED" or not artifact["sha256"]:
                raise ValueError(f"Final timing input is unavailable or unbound: {name}")
            files[name] = artifact["sha256"]
        stage(root, work, files)
        binary = Path(docker).resolve()
        tools["docker"] = {"path": str(binary), "sha256": sha256(binary)}
        tools["OpenSTA"] = {"path": ENGINE, "version": IMAGE}
        top = identifier(evidence["input_manifest"]["top"], "timing top")
        lib = f"{ORFS_ROOT}/flow/platforms/sky130hd/lib/{SKY130_LIBRARY}"
        script = [
            "set sta_continue_on_error 0",
            f"read_liberty {lib}",
            f"read_db /work/inputs/{odb}",
            f'if {{[current_design] ne "{top}"}} {{ error "unexpected design" }}',
            f"read_sdc /work/inputs/{final}",
            f"read_spef /work/inputs/{spef}",
            'foreach clock [all_clocks] { puts "AEDA_CLOCK [get_name $clock]" }',
            *(f'puts "AEDA_CHECK {name} [check_setup -verbose -{name}]"' for name in CHECKS),
            'puts "AEDA_COVERAGE_COMPLETE"',
        ]
        (work / "coverage.tcl").write_text("\n".join(script) + "\n", encoding="utf-8")
        container = f"aeda-{uuid.uuid4().hex}"
        (work / DISPATCH).write_text(container, encoding="ascii")
        command = [
            str(binary),
            "run",
            "--rm",
            "--pull",
            "never",
            "--network",
            "none",
            "--name",
            container,
            "--cpus",
            "1",
            "--memory",
            "1200m",
            "--memory-swap",
            "1200m",
            "--pids-limit",
            "128",
            "-v",
            f"{work}:/work:ro",
            "--entrypoint",
            ENGINE,
            IMAGE,
            "-no_init",
            "-threads",
            "1",
            "-exit",
            "/work/coverage.tcl",
        ]
        stdout, stderr = work / "coverage.stdout.log", work / "coverage.stderr.log"

        def guard() -> float:
            if cancelled():
                raise DispatchStopped("Verification execution cancelled")
            if max(stdout.stat().st_size, stderr.stat().st_size) > MAX_TEXT:
                raise DispatchStopped("Verification output limit exceeded")
            return float("inf")

        started = timestamp()
        context = ResourceContext(
            work, "timing_coverage", evidence["run_id"], container, IMAGE, 1, 1200 * 2**20, 128, 0
        )
        code, elapsed = execute(
            command,
            policy.timeout_seconds,
            stdout,
            container,
            resources=context,
            guard=guard,
            stderr_path=stderr,
        )
        runs.append(Execution(command, code, started, elapsed, None, stdout.name, stderr.name))
        state, reason = coverage_outcome(work, runs[0], evidence, root, policy)
    except DispatchStopped as error:
        state, reason = "BLOCKED", str(error)
    except (OSError, ValueError, subprocess.TimeoutExpired) as error:
        state, reason = "BLOCKED", str(error)
    outputs = {path.name: sha256(path) for path in sorted(work.iterdir()) if path.is_file()}
    return {
        "gate": "timing_coverage",
        "status": state,
        "diagnostic": reason,
        "inputs": files,
        "outputs": outputs,
        "tools": tools,
        "execution": [asdict(item) for item in runs],
    }


def cleanup_interrupted(work: Path) -> None:
    marker = work / DISPATCH
    log = work / "coverage.stdout.log"
    if not marker.is_file() or receipt_path(log).is_file():
        return
    docker = shutil.which("docker") if sys.platform == "linux" else None
    if docker is None:
        return
    with marker.open(encoding="ascii") as stream:
        name = stream.read(39)
    if owned_attempt(name):
        record_interrupted_dispatch(docker, name, log)
        remove_interrupted_container(docker, name)


def coverage_outcome(
    work: Path,
    run: Execution,
    evidence: Experiment,
    root: Path,
    policy: Policy,
) -> tuple[State, str]:
    context, _, problem = inspect_context(root, evidence, policy)
    if problem:
        return "BLOCKED", problem
    if run.stop or run.return_code != 0:
        return "BLOCKED", run.stop or "OpenSTA did not complete the coverage probe"
    report = log_text(work, run.stdout)
    errors = log_text(work, run.stderr)
    if re.search(r"(?im)^(?:error\b|\[ERROR)", report + "\n" + errors):
        return "BLOCKED", "OpenSTA reported a load or coverage command error"
    checks, observed = parse_coverage(report)
    if "UNKNOWN" in checks.values():
        return "BLOCKED", "Coverage report is incomplete or uses an unsupported format"
    if sorted(observed) != sorted(context["clocks"]):
        return "FAILED", "OpenSTA clock inventory does not match the final SDC"
    failed = [name for name in CHECKS if checks[name] == "FAILED"]
    if failed:
        return "FAILED", f"OpenSTA coverage violations: {', '.join(failed)}"
    warnings = re.findall(r"(?im)^(?:warning\b|\[WARNING)[^\r\n]*", report + "\n" + errors)
    # ORD-0012 describes the pinned binary's default mode. The probe reads an
    # already implemented flat Sky130HD ODB and never runs hierarchical flow.
    if any(not warning.startswith("[WARNING ORD-0012] Hierarchical flow") for warning in warnings):
        return "BLOCKED", "OpenSTA emitted warnings outside the successful coverage checks"
    return "PASSED", "OpenSTA completed all configured final coverage checks"

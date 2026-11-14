"""Native RTL backends operating on staged authoritative source lists."""

from __future__ import annotations

import json
import re
import shutil
import time
from collections.abc import Callable
from dataclasses import asdict
from pathlib import Path
from typing import Any

from ..manifest import RTLManifest, load_manifest, sha256
from .policy import RTL, Policy, State
from .process import Execution, execute, log_text

TOOLS = {
    "elaboration": ("yosys",),
    "functional_regression": ("iverilog", "vvp"),
    "equivalence": ("yosys",),
}


def local_file(root: Path, name: str) -> Path:
    path = (root / name).resolve()
    if not path.is_relative_to(root) or not path.is_file():
        raise ValueError(f"missing or out-of-scope input {name}")
    return path


def source_set(root: Path, manifest_name: str) -> tuple[RTLManifest, dict[str, str]]:
    path = local_file(root, manifest_name)
    digest = sha256(path)
    manifest = load_manifest(path)
    if not isinstance(manifest, RTLManifest):
        raise ValueError("native RTL checks require an explicit RTL source manifest")
    assert manifest.root is not None
    files = {
        (manifest.root / rel).relative_to(root).as_posix(): value
        for rel, value in manifest.inputs().items()
    }
    return manifest, {**files, manifest_name: digest}


def inputs(root: Path, name: str, policy: Policy) -> tuple[RTLManifest, dict[str, str]]:
    manifest, files = source_set(root, "manifest.toml")
    if name == "functional_regression":
        if not policy.simulation:
            raise ValueError("No explicit testbench and oracle configured")
        testbench = policy.simulation.testbench
        files[testbench] = sha256(local_file(root, testbench))
    if name == "equivalence":
        if not policy.equivalence:
            raise ValueError("No reference RTL and port correspondence configured")
        ref = policy.equivalence.reference
        if local_file(root, ref) == local_file(root, "manifest.toml"):
            raise ValueError("Reference must be a separate source manifest")
        reference, ref_files = source_set(root, ref)
        if set(reference.sources) & set(manifest.sources):
            raise ValueError("Reference and candidate source lists overlap")
        files.update(ref_files)
    return manifest, files


def stage(root: Path, work: Path, files: dict[str, str]) -> None:
    for name, digest in files.items():
        target = work / "inputs" / name
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(local_file(root, name), target)
        if sha256(target) != digest:
            raise ValueError(f"input changed during staging: {name}")


def source_args(manifest: RTLManifest, root: Path) -> list[str]:
    return [
        *(f"-Iinputs/{path.relative_to(root).as_posix()}" for path in manifest.include_dirs),
        *(f"-D{name}={value}" if value else f"-D{name}" for name, value in manifest.defines),
        *(f"inputs/{path.relative_to(root).as_posix()}" for path in manifest.sources),
    ]


def yosys_commands(manifest: RTLManifest, root: Path) -> list[str]:
    args = source_args(manifest, root)
    if manifest.language == "systemverilog":
        args.insert(0, "-sv")
    params = "".join(f" -chparam {name} {value}" for name, value in manifest.parameters)
    return [
        f"read_verilog {' '.join(args)}",
        f"hierarchy -check -top {manifest.top}{params}",
        "proc",
        "flatten",
        "opt_clean",
        "check -assert",
    ]


def assess(name: str, work: Path, runs: list[Execution], policy: Policy) -> tuple[State, str]:
    if not runs:
        return "BLOCKED", "Execution never completed"
    final = runs[-1]
    if any(item.stop for item in runs):
        return "BLOCKED", next(item.stop for item in runs if item.stop) or "Execution interrupted"
    out = log_text(work, final.stdout)
    err = log_text(work, final.stderr)
    if name == "functional_regression":
        assert policy.simulation
        if len(runs) != 2:
            return "BLOCKED", "Icarus could not compile the design and testbench"
        lines = [line.strip() for line in out.splitlines()]
        if policy.simulation.fail_marker in lines:
            return "FAILED", f"Regression oracle emitted {policy.simulation.fail_marker}"
        if final.return_code == 0 and lines.count(policy.simulation.pass_marker) == 1:
            return "PASSED", f"Regression oracle emitted {policy.simulation.pass_marker}"
        return "BLOCKED", "Simulation did not produce a valid oracle outcome"
    if name == "equivalence":
        if "SAT proof finished - model found: FAIL!" in out:
            return "FAILED", "SAT found a two-state output counterexample"
        if final.return_code == 0 and "SAT proof finished - no model found: SUCCESS!" in out:
            return "PASSED", "SAT proved all matching outputs for defined two-state inputs"
        return "BLOCKED", "Yosys could not establish the configured combinational proof"
    if final.return_code == 0 and (work / "design.json").is_file():
        return "PASSED", "Yosys elaboration and structural check completed"
    if re.search(r"^ERROR:", err, re.M) and final.return_code == 1:
        return "FAILED", "Yosys reported an RTL elaboration or structural error"
    return "BLOCKED", "Yosys did not complete elaboration"


def exact_combinational_ports(work: Path, top: str, reference_top: str) -> None:
    # Limit the obligation to completely flattened, defined combinational logic.
    models = []
    for file, module in (("candidate.json", top), ("reference.json", reference_top)):
        model = json.loads((work / file).read_bytes())["modules"][module]
        allowed = {
            "$not",
            "$pos",
            "$neg",
            "$and",
            "$or",
            "$xor",
            "$xnor",
            "$logic_not",
            "$logic_and",
            "$logic_or",
            "$reduce_and",
            "$reduce_or",
            "$reduce_xor",
            "$reduce_xnor",
            "$reduce_bool",
            "$mux",
            "$add",
            "$sub",
            "$mul",
            "$lt",
            "$le",
            "$ge",
            "$gt",
            "$eq",
            "$ne",
            "$shl",
            "$shr",
            "$sshl",
            "$sshr",
        }
        if any(cell["type"] not in allowed for cell in model.get("cells", {}).values()):
            raise ValueError("combinational equivalence model has unsupported cells")
        ports = model["ports"]
        if not any(port["direction"] == "output" for port in ports.values()) or any(
            port["direction"] not in {"input", "output"} for port in ports.values()
        ):
            raise ValueError("equivalence requires an output and only input/output ports")
        bits = [bit for net in model["netnames"].values() for bit in net["bits"]]
        if "x" in bits or "z" in bits:
            raise ValueError("equivalence model contains undefined or high impedance bits")
        models.append(
            {
                name: (
                    data["direction"],
                    len(data["bits"]),
                    data.get("offset", 0),
                    data.get("upto", 0),
                    data.get("signed", 0),
                )
                for name, data in ports.items()
            }
        )
    if models[0] != models[1]:
        raise ValueError("reference and candidate ports do not match exactly")


def run_gate(
    root: Path,
    work: Path,
    name: str,
    policy: Policy,
    cancelled: Callable[[], bool],
) -> dict[str, Any]:
    runs: list[Execution] = []
    tools: dict[str, dict[str, str]] = {}
    files: dict[str, str] = {}
    deadline = time.monotonic() + policy.timeout_seconds

    def invoke(tool: str, label: str, *args: str) -> Execution:
        run = execute([tools[tool]["path"], *args], work, label, deadline, cancelled)
        runs.append(run)
        return run

    def script(label: str, commands: list[str]) -> Execution:
        (work / f"{label}.ys").write_text("\n".join(commands) + "\n", encoding="utf-8")
        return invoke("yosys", label, "-Q", "-T", "-s", f"{label}.ys")

    try:
        manifest, files = inputs(root, name, policy)
        stage(root, work, files)
        staged_root = work / "inputs"
        manifest, _ = source_set(staged_root, "manifest.toml")
        for tool in TOOLS[name]:
            found = shutil.which(tool)
            if not found:
                raise ValueError(f"Required executable unavailable: {tool}")
            binary = Path(found).resolve()
            tools[tool] = {"path": str(binary), "sha256": sha256(binary)}
            probe = invoke(tool, f"{tool}-version", "-V")
            lines = (log_text(work, probe.stdout) + log_text(work, probe.stderr)).splitlines()
            prefix = {
                "yosys": "Yosys ",
                "iverilog": "Icarus Verilog version ",
                "vvp": "Icarus Verilog runtime version ",
            }[tool]
            version = next((line.strip() for line in lines if line.startswith(prefix)), "")
            # Icarus vvp 12 prints a valid version and exits 1 for -V.
            valid_exit = probe.return_code == 0 or tool == "vvp" and probe.return_code == 1
            if probe.stop or not valid_exit or not version:
                raise ValueError(
                    f"{tool} version probe did not complete: {probe.stop or 'invalid version'}"
                )
            tools[tool]["version"] = version[:256]
        # Version probes have independent receipts. Only design commands enter the oracle.
        probes = len(runs)
        if name == "elaboration":
            commands = yosys_commands(manifest, staged_root)
            script("elaborate", [*commands, "write_json design.json"])
        elif name == "functional_regression":
            assert policy.simulation
            sim = policy.simulation
            if manifest.parameters:
                raise ValueError("simulation parameter overrides require an explicit wrapper")
            flags = "-g2012" if manifest.language == "systemverilog" else "-g2005"
            compile_run = invoke(
                "iverilog",
                "compile",
                flags,
                "-s",
                sim.top,
                "-o",
                "test.vvp",
                *source_args(manifest, staged_root),
                f"inputs/{sim.testbench}",
            )
            if compile_run.return_code == 0 and not compile_run.stop:
                invoke("vvp", "simulate", "test.vvp")
        else:
            assert policy.equivalence
            reference, _ = source_set(staged_root, policy.equivalence.reference)
            for label, model in (("candidate", manifest), ("reference", reference)):
                check = script(
                    label, [*yosys_commands(model, staged_root), f"write_json {label}.json"]
                )
                if check.return_code != 0 or check.stop:
                    raise ValueError(
                        f"{label} could not be elaborated: {check.stop or 'see tool log'}"
                    )
            exact_combinational_ports(work, manifest.top, reference.top)
            script(
                "prove",
                [
                    "read_json reference.json",
                    f"rename {reference.top} aeda_gold",
                    "read_json candidate.json",
                    f"rename {manifest.top} aeda_gate",
                    "miter -equiv -flatten aeda_gold aeda_gate aeda_miter",
                    "hierarchy -top aeda_miter",
                    "sat -set-def-inputs -prove trigger 0 -show-inputs -show-outputs",
                ],
            )
        state, reason = assess(name, work, runs[probes:], policy)
    except (OSError, ValueError, KeyError) as error:
        state, reason = "BLOCKED", str(error)
    if cancelled() or any(item.stop == "cancelled" for item in runs):
        state, reason = "BLOCKED", "cancelled"
    outputs = {path.name: sha256(path) for path in sorted(work.iterdir()) if path.is_file()}
    return {
        "gate": name,
        "status": state,
        "diagnostic": reason,
        "inputs": files,
        "outputs": outputs,
        "tools": tools,
        "execution": [asdict(item) for item in runs],
    }


def selected(policy: Policy) -> list[str]:
    return [
        name
        for name in RTL
        if name in policy.required
        or name == "functional_regression"
        and policy.simulation
        or name == "equivalence"
        and policy.equivalence
    ]

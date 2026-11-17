"""Bounded Verilator regressions for the pinned AES configurations."""

from __future__ import annotations

import re
import shutil
import time
from collections.abc import Callable
from dataclasses import asdict
from pathlib import Path
from typing import Any

from ..manifest import RTLManifest, fingerprint, integer, sha256
from .process import Execution, execute, log_text

CORE_MARKER = "SUCCESS: All AES ciphertexts correctly decrypted."
PERIPHERAL_MARKER = "AEDA_AES_PASS"
FAIL_MARKER = "AEDA_AES_FAIL "
TRACE = re.compile(r"AEDA_AES_TRACE ([0-9]+) ([0-9a-f]+)")
SEED = 41


def disposition(run: Execution) -> tuple[str, str] | None:
    if run.stop == "timeout":
        return "TIMEOUT", "Execution exceeded its deadline"
    if run.stop == "cancelled":
        return "BLOCKED", "Execution was interrupted"
    if run.stop:
        return "INFRASTRUCTURE_ERROR", run.stop
    return None


def simulation_result(work: Path, run: Execution, scope: str) -> tuple[str, str, dict[str, Any]]:
    stopped = disposition(run)
    if stopped is not None:
        return stopped[0], stopped[1], {}
    lines = log_text(work, run.stdout).splitlines()
    if scope == "core":
        lines += log_text(work, run.stderr).splitlines()
        cycles = [
            int(m[1]) for line in lines if (m := re.fullmatch(r"Executed cycles:\s+(\d+)", line))
        ]
        passes = lines.count(CORE_MARKER)
        facts = {"cycles": cycles, "success_markers": passes}
        if any(line.startswith(("ERROR: ", "FAILURE: ")) for line in lines):
            return "FAIL", "Upstream cipher-core oracle reported an error", facts
        if run.return_code == 0 and passes == 2 and len(cycles) == 1 and cycles[0] > 0:
            return "PASS", "Upstream cipher-core oracle completed both round trips", facts
    else:
        trace = [m.groups() for line in lines if (m := TRACE.fullmatch(line))]
        facts = {"observed_cycles": len(trace), "trace_sha256": fingerprint(trace)}
        if any(line.startswith(FAIL_MARKER) for line in lines):
            return "FAIL", "The peripheral testbench oracle reported a failure", facts
        if (
            run.return_code == 0
            and lines.count(PERIPHERAL_MARKER) == 1
            and trace
            and all(int(cycle) == index for index, (cycle, _) in enumerate(trace))
        ):
            return "PASS", "The bounded TL-UL peripheral testbench completed", facts
    return "INFRASTRUCTURE_ERROR", "Simulation exited without a complete oracle outcome", facts


def differential(original: dict[str, Any], translated: dict[str, Any]) -> dict[str, str]:
    if original["status"] != "PASS" or translated["status"] != "PASS":
        return {"status": "BLOCKED", "diagnostic": "Both peripheral simulations must pass"}
    if original["observations"] != translated["observations"]:
        return {"status": "FAIL", "diagnostic": "Cycle-level peripheral observations differ"}
    return {"status": "PASS", "diagnostic": "Observed cycles agree under the recorded stimulus"}


def regression(
    work: Path,
    *,
    scope: str,
    sources: dict[str, str],
    build_args: list[str],
    simulation: str,
    tool_name: str = "verilator",
    timeout: int,
    cancelled: Callable[[], bool] = lambda: False,
) -> dict[str, Any]:
    timeout = integer(timeout, "AES regression deadline", 3600)
    if timeout < 1:
        raise ValueError("AES regression deadline must be positive")
    deadline = time.monotonic() + timeout
    tools: dict[str, Any] = {}
    runs: list[Execution] = []
    facts: dict[str, Any] = {}
    status = "BLOCKED"
    reason = f"Required executable unavailable: {tool_name}"
    found = shutil.which(tool_name)
    product = work / simulation
    if product.exists() or product.is_symlink():
        raise ValueError("simulation product already exists, choose a fresh work directory")
    if found:
        tool = Path(found).resolve()
        tools = {"verilator": {"path": str(tool), "sha256": sha256(tool)}}
        engine = tool.with_name("verilator_bin")
        if engine.is_file():
            tools["verilator_bin"] = {"path": str(engine), "sha256": sha256(engine)}
        probe = execute([str(tool), "--version"], work, "version", deadline, cancelled)
        runs.append(probe)
        version = log_text(work, probe.stdout).strip()
        failure = disposition(probe)
        if failure:
            status, reason = failure
        elif probe.return_code or not version.startswith("Verilator "):
            status, reason = "INFRASTRUCTURE_ERROR", "Verilator version probe failed"
        else:
            tools["verilator"]["version"] = version
            command = [arg.replace("{verilator}", str(tool)) for arg in build_args]
            build = execute(command, work, "compile", deadline, cancelled)
            runs.append(build)
            failure = disposition(build)
            if failure:
                status, reason = failure
            elif build.return_code != 0 or not product.is_file():
                status, reason = "INFRASTRUCTURE_ERROR", "Compilation did not produce a simulator"
            else:
                run = execute(
                    [str(product.resolve()), f"+verilator+seed+{SEED}"],
                    work,
                    "simulate",
                    deadline,
                    cancelled,
                )
                runs.append(run)
                status, reason, facts = simulation_result(work, run, scope)
    outputs = {
        name: sha256(work / name)
        for name in sorted(
            {name for run in runs for name in (run.stdout, run.stderr)}
            | ({simulation} if product.is_file() else set())
        )
    }
    report = {
        "schema_version": 1,
        "scope": scope,
        "status": status,
        "diagnostic": reason,
        "seed": SEED,
        "timeout_seconds": timeout,
        "inputs": sources,
        "tools": tools,
        "observations": facts,
        "execution": [asdict(run) for run in runs],
        "outputs": outputs,
    }
    return {**report, "receipt_sha256": fingerprint(report)}


def cipher_core(build: Path, *, timeout: int) -> dict[str, Any]:
    build = build.resolve()
    work = build / "default-verilator"
    edam = work / "lowrisc_dv_verilator_aes_cipher_core_tb_0.eda.yml"
    if not edam.is_file() or not (build / "src").is_dir():
        raise ValueError("expected fresh FuseSoC lowrisc:dv_verilator:aes_cipher_core_tb output")
    files = [
        *sorted((build / "src").rglob("*")),
        edam,
        work / "Makefile",
        work / "lowrisc_dv_verilator_aes_cipher_core_tb_0.vc",
    ]
    return regression(
        work,
        scope="core",
        sources={
            path.relative_to(build).as_posix(): sha256(path) for path in files if path.is_file()
        },
        build_args=["make", "-j2", "VERILATOR={verilator}"],
        simulation="Vaes_cipher_core_tb",
        timeout=timeout,
    )


def interface_ports(header: str) -> dict[str, dict[str, int | str]]:
    ports: dict[str, dict[str, int | str]] = {}
    for line in header.splitlines():
        match = re.fullmatch(
            r"\s*VL_(INOUT|IN|OUT)(?:8|16|64|W)?\(&?(\w+),(\d+),(\d+)(?:,\d+)?\);",
            line,
        )
        if match:
            direction, name, high, low = match.groups()
            ports[name] = {
                "direction": {"IN": "input", "OUT": "output", "INOUT": "inout"}[direction],
                "width": int(high) - int(low) + 1,
            }
    return ports


def structural(
    work: Path, args: list[str], inventory: dict[str, Any], *, timeout: int
) -> dict[str, Any]:
    found = shutil.which("verilator")
    if not found:
        return {"status": "BLOCKED", "diagnostic": "Verilator is unavailable"}
    run = execute(
        [
            found,
            "--cc",
            "--top-module",
            "aes",
            "--prefix",
            "VAesInterface",
            "--Mdir",
            "interface",
            "-Wno-fatal",
            *args,
        ],
        work,
        "interface",
        time.monotonic() + timeout,
        lambda: False,
    )
    header = work / "interface/VAesInterface.h"
    stopped = disposition(run)
    ports = {}
    if stopped:
        status, reason = stopped
    elif run.return_code or not header.is_file():
        status, reason = "INFRASTRUCTURE_ERROR", "Native interface elaboration failed"
    else:
        ports = interface_ports(header.read_text(encoding="utf-8"))
        if not ports:
            status, reason = "BLOCKED", "No supported native interface port declarations"
        elif ports != inventory:
            status, reason = "FAIL", "Native interface differs from the pinned Yosys inventory"
        else:
            status, reason = "PASS", "Native elaboration matched the pinned Yosys interface"
    files = [work / run.stdout, work / run.stderr]
    if header.is_file():
        files.append(header)
    return {
        "status": status,
        "diagnostic": reason,
        "ports": ports,
        "execution": asdict(run),
        "outputs": {path.relative_to(work).as_posix(): sha256(path) for path in files},
    }


def simulation_model(translation: Path, work: Path, *, timeout: int) -> dict[str, Any]:
    """Elaborate in Yosys when the simulator cannot read the raw sv2v syntax."""
    found = shutil.which("yosys")
    if not found:
        return {"status": "BLOCKED", "diagnostic": "Yosys is unavailable"}
    shutil.copyfile(translation, work / "translation.v")
    expected = sha256(translation)
    if sha256(work / "translation.v") != expected:
        raise ValueError("translation changed during Yosys staging")
    binary = Path(found).resolve()
    commands = (
        "read_verilog translation.v; hierarchy -check -top aes; "
        "proc; check -assert; write_verilog -noattr translated_model.v"
    )
    run = execute(
        [str(binary), "-Q", "-p", commands],
        work,
        "yosys-model",
        time.monotonic() + timeout,
        lambda: False,
    )
    product = work / "translated_model.v"
    stopped = disposition(run)
    if stopped:
        status, reason = stopped
    elif run.return_code != 0 or not product.is_file():
        status, reason = "INFRASTRUCTURE_ERROR", "Yosys could not elaborate a simulation model"
    else:
        status, reason = "PASS", "Yosys elaborated the byte-identical sv2v translation"
    outputs = [run.stdout, run.stderr]
    if product.is_file():
        outputs.append(product.name)
    return {
        "status": status,
        "diagnostic": reason,
        "translation_sha256": expected,
        "yosys_sha256": sha256(binary),
        "execution": asdict(run),
        "outputs": {name: sha256(work / name) for name in outputs},
    }


def peripheral(
    rtl: RTLManifest,
    translation: Path,
    testbench: Path,
    work: Path,
    inventory: dict[str, Any],
    *,
    timeout: int,
) -> dict[str, Any]:
    timeout = integer(timeout, "AES regression deadline", 3600)
    if timeout < 1:
        raise ValueError("AES regression deadline must be positive")
    work = work.resolve()
    work.mkdir(parents=True)
    flags = [
        *(f"-I{path}" for path in rtl.include_dirs),
        *(f"-D{name}={value}" if value else f"-D{name}" for name, value in rtl.defines),
        "-DDISABLE_PRIM_CDC_RAND_DELAY",
    ]
    args = [*flags, *map(str, rtl.sources)]
    packages = [str(path) for path in rtl.sources if path.name.endswith("_pkg.sv")]
    interface = {}
    regressions = {}
    normalized = None
    direct_interface = None
    binding = {
        "source_manifest_sha256": rtl.fingerprint(rtl.inputs()),
        "translation_sha256": sha256(translation),
        "testbench_sha256": sha256(testbench),
    }
    for name, files, wrapper_files in (
        ("original", args, []),
        ("translated", [str(translation)], [*flags, *packages]),
    ):
        output = work / name
        output.mkdir()
        interface[name] = structural(output, files, inventory, timeout=min(timeout, 120))
        if name == "translated" and interface[name]["status"] == "INFRASTRUCTURE_ERROR":
            direct_interface = interface[name]
            normalized = simulation_model(translation, output, timeout=min(timeout, 120))
            if normalized["status"] == "PASS":
                model_directory = output / "normalized-interface"
                model_directory.mkdir()
                files = [str(output / "translated_model.v")]
                interface[name] = structural(
                    model_directory, files, inventory, timeout=min(timeout, 120)
                )
        regressions[name] = regression(
            output,
            scope="peripheral",
            sources=binding,
            build_args=[
                "{verilator}",
                "--binary",
                "--timing",
                "-j",
                "2",
                "--top-module",
                "peripheral_tb",
                "--Mdir",
                "model",
                "-Wno-fatal",
                *wrapper_files,
                *files,
                str(testbench),
            ],
            simulation="model/Vperipheral_tb",
            timeout=timeout,
        )
    observed = differential(regressions["original"], regressions["translated"])
    if normalized and normalized["status"] == "PASS":
        observed["implementation"] = "Yosys-elaborated model of the pinned sv2v translation"
    stages = {
        "structural": {
            "status": (
                "PASS"
                if all(item["status"] == "PASS" for item in interface.values())
                else "FAIL"
                if any(item["status"] == "FAIL" for item in interface.values())
                else "BLOCKED"
            ),
            "implementations": interface,
        },
        "differential": observed,
        "formal": {
            "status": "BLOCKED",
            "diagnostic": (
                "No qualified full sequential proof setup for dual clocks, resets, EDN, "
                "masking and X semantics. The existing Yosys SAT profile is combinational."
            ),
        },
    }
    report = {
        "schema_version": 1,
        "identity": binding,
        "semantic_scope": (
            "Finite Verilator two-state simulation. Both ports share a test clock and reset. "
            "CDC random delays are disabled. Deterministic EDN transfers and valid TL-UL "
            "response fields are compared. X/Z and unobserved internal state are outside scope."
        ),
        "functional": regressions,
        "correspondence": stages,
        "translation_model": normalized,
        "direct_translation_interface": direct_interface,
    }
    return {**report, "receipt_sha256": fingerprint(report)}

"""Interface facts and independent pre-route checks for pinned Earl Grey AES."""

from __future__ import annotations

import re
import shutil
import time
import uuid
from dataclasses import asdict
from pathlib import Path
from typing import Any

from .evidence import SKY130_LIBRARY
from .manifest import RTLManifest, fingerprint, integer, load_manifest, sha256
from .opentitan_physical import INPUTS, RTL_DIGEST, RTL_REVISION
from .partition_readiness import metadata
from .reference import IMAGE, ORFS_ROOT
from .verification.aes_native import disposition
from .verification.process import execute, log_text
from .verification.rtl import stage
from .verification.timing import CHECKS, DISPATCH, ENGINE, cleanup_interrupted, parse_coverage

TOPGEN_DIGEST = "acb246657d975a40fa1250648d03686f66003a9b0a2e9f33fb65c660cde06e0d"
RESOLUTION_DIGEST = "acdbf45488cc71751c23bcaae016b133bcbc0080f6d3785523c711b14fd9283a"
INSPECTION_DIGEST = "e81837fbc65eba1775958837963946e7f972dac719eb38428603d05cb4dab41d"
BLOCK_SDC_DIGEST = "c9469d7e34cb05c457c8951806a15754f7c785dda43bdd6f80aa1525ddb020cc"
CHECKPOINT = "results/sky130hd/opentitan_aes/base/1_synth.odb"
CHECKPOINT_DIGEST = "18ad20ce95f6b1be9dea55e83b70fb7886958752f8d852d0b27a5b59d36c1e67"
UPSTREAM_PATHS = {
    "topgen_hjson": (
        "hw/top_earlgrey/data/top_earlgrey.hjson",
        "8e61ec4ee1cc3b85136be5195b81ecf061d279fd9d96507fdb9485379090e9d5",
    ),
    "top_sdc": (
        "hw/top_earlgrey/syn/chip_earlgrey_asic.sdc",
        "891277a47732ea52a5f0c6c60a1d3f96f4d2d1d136cec407b7bba459af16a47c",
    ),
    "ip_hjson": (
        "hw/ip/aes/data/aes.hjson",
        "c24582db98a2cf823657ae2c304da03fb921a4f70aa519ff7a7819f26a1e795a",
    ),
}
PORT_ROLES = {
    "clk_i": ("clock", "clk_i", None),
    "clk_edn_i": ("clock", "clk_edn_i", None),
    "rst_ni": ("reset", "clk_i", "rst_ni"),
    "rst_shadowed_ni": ("reset", "clk_i", "rst_shadowed_ni"),
    "rst_edn_ni": ("reset", "clk_edn_i", "rst_edn_ni"),
    "tl_i": ("TL-UL request", "clk_i", "rst_ni"),
    "tl_o": ("TL-UL response", "clk_i", "rst_ni"),
    "keymgr_key_i": ("key manager sideload", "clk_i", "rst_ni"),
    "edn_i": ("EDN response", "clk_edn_i", "rst_edn_ni"),
    "edn_o": ("EDN request", "clk_edn_i", "rst_edn_ni"),
    "alert_rx_i": ("alert receive", "clk_i", "rst_ni"),
    "alert_tx_o": ("alert transmit", "clk_i", "rst_ni"),
    "lc_escalate_en_i": ("life cycle escalation", "clk_i", "rst_ni"),
    "idle_o": ("idle indication", "clk_i", "rst_ni"),
}


def require_digest(path: Path, expected: str, label: str) -> None:
    if not path.is_file() or sha256(path) != expected:
        raise ValueError(f"Missing or modified pinned AES input: {label}")


def block_clock(text: str, ports: dict[str, Any]) -> dict[str, Any]:
    """Validate the supported literal clock-only SDC before any native Tcl read."""
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    if len(lines) != 2 or lines[0] != "current_design aes":
        raise ValueError("unsupported experimental SDC command or timing exception")
    match = re.fullmatch(
        r"create_clock -name (\w+) -period ([0-9]+(?:\.[0-9]+)?) "
        r"\[get_ports \{([\w ]+)\}\]",
        lines[1],
        flags=re.ASCII,
    )
    if not match:
        raise ValueError("unsupported experimental SDC clock syntax")
    clock, raw_period, raw_targets = match.groups()
    targets = raw_targets.split()
    for target in targets:
        if target not in ports or ports[target] != {"direction": "input", "width": 1}:
            raise ValueError(f"unknown or invalid SDC clock port: {target}")
    if set(targets) != {"clk_i", "clk_edn_i"} or len(targets) != 2:
        raise ValueError("experimental SDC must account for both AES clock inputs")
    period = float(raw_period)
    if period != 8.5:
        raise ValueError("experimental AES clock period differs from the pinned physical run")
    return {"name": clock, "period_ns": period, "ports": targets, "source": INPUTS[1]}


def source_identity(
    manifest: Path, resolved: Path, edam: Path
) -> tuple[RTLManifest, dict[str, Any]]:
    resolution, resolution_sha = metadata(resolved)
    require_digest(resolved, RESOLUTION_DIGEST, "resolved source receipt")
    require_digest(edam, resolution["edam_sha256"], "native EDAM JSON")
    rtl = load_manifest(manifest)
    if not isinstance(rtl, RTLManifest) or rtl.top != "aes" or rtl.revision != RTL_REVISION:
        raise ValueError("expected the pinned AES source manifest")
    inputs = rtl.inputs()
    if rtl.parameters or rtl.fingerprint(inputs) != resolution["manifest_sha256"]:
        raise ValueError(
            "AES manifest configuration or source fingerprint differs from the receipt"
        )
    if inputs != resolution["inputs_sha256"]:
        raise ValueError("AES resolved input hashes differ from the receipt")
    return rtl, {
        "rtl_revision": rtl.revision,
        "source_manifest_sha256": resolution["manifest_sha256"],
        "resolution_sha256": resolution_sha,
        "source_count": len(rtl.sources),
        "generated_source_count": len(rtl.generated_sources),
        "edam_sha256": resolution["edam_sha256"],
    }


def timing_contract(
    run: Path,
    manifest: Path,
    resolved: Path,
    edam: Path,
    topgen: Path,
    upstream: Path,
    inspection: Path,
) -> dict[str, Any]:
    _, identity = source_identity(manifest, resolved, edam)
    for label, (filename, digest) in UPSTREAM_PATHS.items():
        require_digest(upstream / filename, digest, label)
    require_digest(topgen, TOPGEN_DIGEST, "normalized TopGen")
    top, _ = metadata(topgen)
    (aes,) = [row for row in top["module"] if row["name"] == "aes"]
    require_digest(inspection, INSPECTION_DIGEST, "elaborated interface inventory")
    inventory, inventory_digest = metadata(inspection)
    if (
        inventory["top"] != "aes"
        or inventory["manifest_sha256"] != identity["source_manifest_sha256"]
        or inventory["source_revision"] != RTL_REVISION
        or inventory["parameters"]
        or inventory["conversion"]["output_sha256"] != RTL_DIGEST
    ):
        raise ValueError("AES elaboration inventory does not match the pinned configuration")
    ports = inventory["modules"]["aes"]["ports"]
    if set(ports) != set(PORT_ROLES):
        raise ValueError("unrecognized elaborated AES port inventory")
    require_digest(run / INPUTS[0], RTL_DIGEST, "staged translated RTL")
    clock = block_clock((run / INPUTS[1]).read_text(encoding="utf-8"), ports)
    require_digest(run / INPUTS[1], BLOCK_SDC_DIGEST, "experimental SDC")
    (main,) = [row for row in top["clocks"]["srcs"] if row["name"] == "main"]
    obligations = {
        "clock_relationship": "Qualify block clock relationships, phase/skew and generated clocks",
        "clock_uncertainty": "Supply block setup and hold uncertainty at the selected corners",
        "reset_cdc": "Qualify shadow reset mapping, reset release, CDC and alert synchronizers",
        "input_delays": "Supply min/max arrivals and reference clocks for each non-clock input",
        "output_delays": "Supply min/max required times and reference clocks for each output",
        "electrical": "Supply input transitions or driving cells, and output loads",
        "exceptions": "Review and substantiate asynchronous, false-path and multicycle exceptions",
        "path_groups": "Bind endpoint groups to clock and interface obligations",
    }
    report = {
        "schema_version": 1,
        "status": "BLOCKED",
        "identity": {**identity, "translation_sha256": RTL_DIGEST},
        "inputs_sha256": {
            **{name: digest for name, (_, digest) in UPSTREAM_PATHS.items()},
            "topgen_json": TOPGEN_DIGEST,
            "elaborated_inventory": inventory_digest,
            "block_sdc": BLOCK_SDC_DIGEST,
        },
        "upstream": {
            "clock_sources": aes["clock_srcs"],
            "main_frequency_hz": int(main["freq"]),
            "main_nominal_period_ns": 10**9 / int(main["freq"]),
            "reset_connections": aes["reset_connections"],
            "topgen_parameters": aes["param_decl"],
            "rtl_defaults": {"AES192Enable": 1, "SecMasking": 1, "SecSBoxImpl": "SBoxImplDom"},
            "interrupt_ports": [],
            "timing_scope": "Top-level ASIC SDC and logical TopGen connectivity",
            "port_provenance": "Elaborated inventory and hw/ip/aes/rtl/aes.sv interface wiring",
        },
        "ports": {
            name: {
                **ports[name],
                "interface": role,
                "peripheral_clock_port": domain,
                "peripheral_reset_port": reset,
                "port_timing": "EXPERIMENTAL_CLOCK" if role == "clock" else "BLOCKED",
            }
            for name, (role, domain, reset) in sorted(PORT_ROLES.items())
        },
        "experimental": {
            "clock": clock,
            "io_delays": {},
            "exceptions": [],
            "path_groups": [],
        },
        "obligations": {
            name: {"status": "BLOCKED", "requirement": detail}
            for name, detail in obligations.items()
        },
    }
    return {**report, "contract_sha256": fingerprint(report)}


def pre_route(run: Path, work: Path, *, timeout: int) -> dict[str, Any]:
    """Read the clock constraint and endpoint coverage on the mapped synthesis checkpoint."""
    require_digest(run / INPUTS[1], BLOCK_SDC_DIGEST, "experimental SDC")
    require_digest(run / CHECKPOINT, CHECKPOINT_DIGEST, "mapped synthesis database")
    timeout = integer(timeout, "STA timeout", 600)
    if timeout < 1:
        raise ValueError("STA timeout must be positive")
    work = work.resolve()
    work.mkdir(parents=True)
    files = {CHECKPOINT: CHECKPOINT_DIGEST, INPUTS[1]: BLOCK_SDC_DIGEST}
    report: dict[str, Any] = {
        "schema_version": 1,
        "scope": "mapped synthesis checkpoint, no extracted parasitics",
        "inputs": files,
        "image": IMAGE,
        "sta_parse": "BLOCKED",
        "status": "BLOCKED",
        "diagnostic": "Pinned OpenSTA requires cached ORFS Docker on Linux",
    }
    docker = shutil.which("docker")
    if not docker:
        return report
    stage(run.resolve(), work, files)
    script = work / "aes-preroute.tcl"
    script.write_text(
        "\n".join(
            [
                "set sta_continue_on_error 0",
                f"read_liberty {ORFS_ROOT}/flow/platforms/sky130hd/lib/{SKY130_LIBRARY}",
                f"read_db /work/inputs/{CHECKPOINT}",
                f"read_sdc /work/inputs/{INPUTS[1]}",
                'foreach clock [all_clocks] { puts "AEDA_CLOCK [get_name $clock]" }',
                *(f'puts "AEDA_CHECK {name} [check_setup -verbose -{name}]"' for name in CHECKS),
                'puts "AEDA_COVERAGE_COMPLETE"',
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    container = f"aeda-{uuid.uuid4().hex}"
    (work / DISPATCH).write_text(container, encoding="ascii")
    result = execute(
        [
            docker,
            "run",
            "--rm",
            "--pull=never",
            "--network=none",
            "--name",
            container,
            "--cpus=1",
            "--memory=1200m",
            "--memory-swap=1200m",
            "--pids-limit=128",
            "-v",
            f"{work}:/work:ro",
            "--entrypoint",
            ENGINE,
            IMAGE,
            "-exit",
            "/work/aes-preroute.tcl",
        ],
        work,
        "sta",
        time.monotonic() + timeout,
        lambda: False,
    )
    if result.stop:
        cleanup_interrupted(work)
    stdout = log_text(work, result.stdout)
    checks, clocks = parse_coverage(stdout)
    report.update(
        checks=checks,
        clocks=clocks,
        outputs={name: sha256(work / name) for name in (result.stdout, result.stderr, script.name)},
        execution=asdict(result),
        docker_sha256=sha256(Path(docker).resolve()),
    )
    stopped = disposition(result)
    if stopped:
        report["status"], report["diagnostic"] = stopped
    elif result.return_code == 0 and "AEDA_COVERAGE_COMPLETE" in stdout and clocks == ["main"]:
        report.update(sta_parse="PASS", diagnostic="SDC parsed; interface coverage remains blocked")
    else:
        report.update(status="INFRASTRUCTURE_ERROR", diagnostic="OpenSTA probe did not complete")
    return report

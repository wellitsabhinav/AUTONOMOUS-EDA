"""Interface facts and independent pre-route checks for pinned Earl Grey AES."""

from __future__ import annotations

import math
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
LIBERTY_DIGEST = "ec0e1067a35c8bf20b11e58d1e8ac53326067e4dac84a125cc1b917a3518d0d9"
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
    "asic_setup": (
        "hw/top_earlgrey/syn/ot.sdc_setup.tcl",
        "aeb9ec60d2e1ec41d7a71f901d32fd7d6ae116850e5c67457f9c26d46a4ab591",
    ),
    "integration_rtl": (
        "hw/top_earlgrey/rtl/autogen/top_earlgrey.sv",
        "392bb28af6e913941f422b7745de4f3dd06514cdfbe66e3753d82dadd21867a3",
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


def integration_connections(text: str, clock_sources: dict[str, str]) -> dict[str, str]:
    _, marker, body = text.partition(") u_aes (")
    if not marker:
        raise ValueError("Pinned Earl Grey AES instance missing")
    body = body.split("\n  );", 1)[0]
    ports = dict(re.findall(r"\.(\w+)\s*\(\s*([^()\n]+?)\s*\)", body))
    if set(ports) != set(PORT_ROLES):
        raise ValueError("Earl Grey AES instance interface differs from the elaborated inventory")
    if (
        clock_sources != {"clk_i": "main", "clk_edn_i": "main"}
        or ports["clk_i"] != ports["clk_edn_i"]
    ):
        raise ValueError("Conflicting TopGen and generated AES clock assumptions")
    return ports


def crossing_inventory(modules: dict[str, Any], connections: dict[str, str]) -> dict[str, Any]:
    aes = modules["aes"]["submodules"]
    bridge = modules[aes["u_prim_sync_reqack_data"]]["submodules"]
    sync = modules[bridge["u_prim_sync_reqack"]]["submodules"]
    controls = [name for name in sorted(sync) if name.endswith((".ack_sync", ".req_sync"))]
    alerts = [
        module for name, module in sorted(aes.items()) if name.endswith(".u_prim_alert_sender")
    ]
    decoders = [
        child
        for module in alerts
        for name, child in modules[module]["submodules"].items()
        if name in {"u_decode_ping", "u_decode_ack"}
    ]
    if len(controls) != 2 or len(decoders) != 4 or "u_prim_lc_sync" not in aes:
        raise ValueError("Unrecognized elaborated AES control synchronizer inventory")
    return {
        "status": "PASS",
        "scope": "Pinned RTL and elaborated synchronizer inventory",
        "clock_relationship": "Both AES inputs attach to one generated net in Earl Grey",
        "reset_nets": {name: net for name, net in connections.items() if name.startswith("rst_")},
        "edn": {
            "source": "hw/ip/aes/rtl/aes.sv:131–170",
            "bridge": aes["u_prim_sync_reqack_data"],
            "control_synchronizers": {name: sync[name] for name in controls},
            "data": "Destination-to-source bundled entropy bus, DataReg=0; hold through handshake",
            "reset": "The selected NRZ handshake assumes coordinated source and destination resets",
        },
        "alerts": {"senders": alerts, "ping_ack_decoders": decoders},
        "life_cycle": aes["u_prim_lc_sync"],
        "qualification": "BLOCKED",
        "unverified": [
            "Reset deassertion and recovery/removal across the physical clock trees",
            "Reconvergence, differential skew, bundled-data settling and metastability",
            "Unilateral reset during an outstanding asynchronous EDN handshake",
        ],
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
    connections = integration_connections(
        (upstream / UPSTREAM_PATHS["integration_rtl"][0]).read_text(encoding="utf-8"),
        aes["clock_srcs"],
    )
    target_ns = 10**9 / int(main["freq"])
    # These indexes refer to the hashed Tcl programs. The top SDC consumes the setup value.
    setup_text = (upstream / UPSTREAM_PATHS["asic_setup"][0]).read_text(encoding="utf-8")
    factor = float(re.findall(r"^set MAIN_TCK_FACTOR\s+(\S+)$", setup_text, re.M)[0])
    if abs(target_ns * factor - clock["period_ns"]) > 1e-9:
        raise ValueError("Pinned block experiment disagrees with the upstream period derivation")
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
        "coverage_matrix": {
            "clock_source_and_period": {
                "classification": "UPSTREAM_DERIVED",
                "source": "hw/top_earlgrey/rtl/autogen/top_earlgrey.sv:2254–2259; TopGen clocks",
                "clock_net": connections["clk_i"],
                "nominal_period_ns": target_ns,
            },
            "test_synthesis_period": {
                "classification": "UPSTREAM_DERIVED",
                "source": "chip_earlgrey_asic.sdc:40–47; ot.sdc_setup.tcl:8–9",
                "derivation": "Nominal period multiplied by MAIN_TCK_FACTOR",
                "factor": factor,
                "period_ns": target_ns * factor,
            },
            "top_clock_uncertainty": {
                "classification": "UPSTREAM_DERIVED",
                "source": "chip_earlgrey_asic.sdc:7–12,47–48",
                "scope": "Top-level test synthesis MAIN_CLK",
                "pt_shell_ns": 0.05,
                "other_tool_ns": 0.5,
            },
            "block_clock": {"classification": "EXPERIMENTAL", **clock},
            "liberty": {
                "classification": "TECHNOLOGY_DERIVED",
                "source": SKY130_LIBRARY,
                "sha256": LIBERTY_DIGEST,
                "corner": "tt_025C_1v80",
                "unit": "ns",
                "scope": "Cell timing arcs and pin capacitance at the mapped corner",
            },
            "port_budgets": {
                name: {
                    "classification": "UNRESOLVED",
                    "direction": ports[name]["direction"],
                    "width": ports[name]["width"],
                    "integration_net": connections[name],
                    "block_reference_clock": domain,
                    "interface": role,
                    "min_delay_ns": None,
                    "max_delay_ns": None,
                    "transition_ns": None,
                    "load_pf": None,
                    "reason": "Generated connectivity identifies the peer only",
                }
                for name, (role, domain, _) in sorted(PORT_ROLES.items())
                if role != "clock"
            },
            "outstanding": {
                name: {"classification": "UNRESOLVED", "reason": detail}
                for name, detail in obligations.items()
            },
        },
        "clock_reset_review": crossing_inventory(inventory["modules"], connections),
        "obligations": {
            name: {"status": "BLOCKED", "requirement": detail}
            for name, detail in obligations.items()
        },
    }
    return {**report, "contract_sha256": fingerprint(report)}


def diagnostic_constraints(path: Path) -> tuple[dict[str, Any], list[str]]:
    budget, digest = metadata(path)
    if set(budget) - {"inputs", "outputs", "uncertainty_ns"} or not budget:
        raise ValueError("Invalid diagnostic budget fields")
    commands = []
    for direction, roles in (("input", "_i"), ("output", "_o")):
        budgets = budget.get(f"{direction}s", {})
        if not isinstance(budgets, dict):
            raise ValueError("Expected a diagnostic delay map")
        for port, bounds in sorted(budgets.items()):
            if (
                port not in PORT_ROLES
                or not port.endswith(roles)
                or port.startswith(("rst_", "clk_"))
            ):
                raise ValueError(f"Invalid diagnostic {direction} reference: {port}")
            if not isinstance(bounds, dict) or set(bounds) != {"min", "max"}:
                raise ValueError(f"Both min and max diagnostic delays are required for {port}")
            low, high = (timing_number(bounds[key], signed=True) for key in ("min", "max"))
            if low > high:
                raise ValueError(f"Conflicting min/max diagnostic delays for {port}")
            for bound, value in (("min", low), ("max", high)):
                commands.append(
                    f"set_{direction}_delay -clock main -{bound} {value} $aeda_ports({port})"
                )
    uncertainty = budget.get("uncertainty_ns")
    if uncertainty is not None:
        if not isinstance(uncertainty, dict) or set(uncertainty) != {"setup", "hold"}:
            raise ValueError("Both setup and hold diagnostic uncertainty are required")
        for check in ("setup", "hold"):
            value = timing_number(uncertainty[check], signed=False)
            commands.append(f"set_clock_uncertainty -{check} {value} [get_clocks main]")
    if not commands:
        raise ValueError("The diagnostic budget contains no constraints")
    return {"classification": "EXPERIMENTAL", "sha256": digest, "budget": budget}, commands


def timing_number(value: Any, *, signed: bool) -> float:
    if type(value) not in (float, int) or abs(value) > 1000 or not math.isfinite(value):
        raise ValueError("Diagnostic timing values must be numbers in a bounded ns range")
    if not signed and value < 0:
        raise ValueError("Diagnostic clock uncertainty must be nonnegative")
    return float(value)


def pre_route(
    run: Path,
    work: Path,
    *,
    timeout: int,
    ports: dict[str, Any],
    budget: Path | None = None,
) -> dict[str, Any]:
    """Read the clock constraint and endpoint coverage on the mapped synthesis checkpoint."""
    require_digest(run / INPUTS[1], BLOCK_SDC_DIGEST, "experimental SDC")
    require_digest(run / CHECKPOINT, CHECKPOINT_DIGEST, "mapped synthesis database")
    timeout = integer(timeout, "STA timeout", 600)
    if timeout < 1:
        raise ValueError("STA timeout must be positive")
    work = work.resolve()
    work.mkdir(parents=True)
    files = {CHECKPOINT: CHECKPOINT_DIGEST, INPUTS[1]: BLOCK_SDC_DIGEST}
    experimental, commands = diagnostic_constraints(budget) if budget else (None, [])
    report: dict[str, Any] = {
        "schema_version": 1,
        "scope": "mapped synthesis checkpoint, no extracted parasitics",
        "inputs": files,
        "image": IMAGE,
        "corner": "tt_025C_1v80",
        "diagnostic_budget": experimental,
        "production_constraints": {
            "interface_min_max": "BLOCKED",
            "clock_uncertainty": "BLOCKED",
            "exception_and_path_group_review": "BLOCKED",
            "extracted_setup_hold_and_electrical": "BLOCKED",
        },
        "sta_parse": "BLOCKED",
        "status": "BLOCKED",
        "diagnostic": "Pinned OpenSTA requires cached ORFS Docker on Linux",
    }
    docker = shutil.which("docker")
    if not docker:
        return report
    stage(run.resolve(), work, files)
    script = work / "aes-preroute.tcl"
    library = f"{ORFS_ROOT}/flow/platforms/sky130hd/lib/{SKY130_LIBRARY}"
    script.write_text(
        "\n".join(
            [
                "set sta_continue_on_error 0",
                f'puts "AEDA_LIB [lindex [exec sha256sum {library}] 0]"',
                f"read_liberty {library}",
                f"read_db /work/inputs/{CHECKPOINT}",
                f"read_sdc /work/inputs/{INPUTS[1]}",
                "foreach port [get_ports *] {",
                "  set name [get_full_name $port]",
                r"  regsub {\[[0-9]+\]$} $name {} base",
                "  lappend aeda_ports($base) $port",
                "}",
                *(
                    f"if {{[llength $aeda_ports({name})] != {entry['width']}}} "
                    f'{{error "Invalid mapped AES port width: {name}"}}'
                    for name, entry in sorted(ports.items())
                ),
                "foreach name [lsort [array names aeda_ports]] {",
                '  puts "AEDA_PORT $name [llength $aeda_ports($name)]"',
                "}",
                *commands,
                'foreach clock [all_clocks] { puts "AEDA_CLOCK [get_name $clock]" }',
                "report_clock_properties [all_clocks]",
                "report_units",
                *(f'puts "AEDA_CHECK {name} [check_setup -verbose -{name}]"' for name in CHECKS),
                'puts "AEDA_CELL_DELAY_HOLD"',
                "report_worst_slack -min -digits 6",
                'puts "AEDA_CELL_DELAY_SETUP"',
                "report_worst_slack -max -digits 6",
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
    native_ports = {
        name: int(width) for name, width in re.findall(r"^AEDA_PORT (\w+) (\d+)$", stdout, re.M)
    }
    liberty = re.findall(r"^AEDA_LIB ([0-9a-f]{64})$", stdout, re.M)
    period = re.findall(r"^main\s+(\d+\.\d+)\s+\d+\.\d+\s+\d+\.\d+\s*$", stdout, re.M)
    missing = {}
    for check, label in (
        ("unconstrained_endpoints", "unconstrained endpoints"),
        ("no_input_delay", "input ports missing set_input_delay"),
        ("no_output_delay", "output ports missing set_output_delay"),
    ):
        match = re.search(rf"^Warning: There (?:are|is) (\d+) {label}\.$", stdout, re.M)
        missing[check] = int(match[1]) if match else 0 if checks[check] == "PASSED" else None
    measured_slack = dict(
        (kind.lower(), float(value))
        for kind, value in re.findall(
            r"^AEDA_CELL_DELAY_(SETUP|HOLD)\nworst slack (?:min|max) (-?\d+\.\d+)\s*$",
            stdout,
            re.M,
        )
    )
    units = dict(re.findall(r"^\s+(time|capacitance) (\S+)$", stdout, re.M))
    report.update(
        checks=checks,
        missing_counts=missing,
        clocks=clocks,
        clock_period_ns=float(period[0]) if len(period) == 1 else None,
        mapped_slack_ns=measured_slack,
        units=units,
        ports=native_ports,
        liberty_sha256=liberty[0] if len(liberty) == 1 else None,
        outputs={name: sha256(work / name) for name in (result.stdout, result.stderr, script.name)},
        execution=asdict(result),
        docker_sha256=sha256(Path(docker).resolve()),
    )
    stopped = disposition(result)
    if stopped:
        report["status"], report["diagnostic"] = stopped
    elif (
        result.return_code == 0
        and all(value != "UNKNOWN" for value in checks.values())
        and clocks == ["main"]
        and period == ["8.50"]
        and units == {"time": "1ns", "capacitance": "1pF"}
        and set(measured_slack) == {"setup", "hold"}
        and native_ports == {name: entry["width"] for name, entry in ports.items()}
        and liberty == [LIBERTY_DIGEST]
    ):
        report.update(sta_parse="PASS", diagnostic="Native probe completed at mapped synthesis")
    else:
        report.update(status="INFRASTRUCTURE_ERROR", diagnostic="OpenSTA probe did not complete")
    return report

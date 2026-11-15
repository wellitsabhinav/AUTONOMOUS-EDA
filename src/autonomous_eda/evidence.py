"""Versioned evidence for controller runs and integrity-checked reference imports."""

from __future__ import annotations

import hashlib
import json
import re
import sqlite3
import stat
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal, TypedDict

from .diagnostic_artifacts import STAGE_REPORTS
from .experiment import report
from .manifest import fingerprint, sha256
from .metrics import FINAL_FIELDS, ROUTE_FIELDS, Number, number, summarize_verified
from .opentitan_physical import CONFIG as AES_CONFIG
from .opentitan_physical import RTL_DIGEST as AES_RTL_DIGEST
from .opentitan_physical import RTL_REVISION as AES_RTL_REVISION
from .opentitan_physical import SDC as AES_SDC
from .opentitan_physical import selected as opentitan_aes
from .reference import IMAGE, REVISION

VERSION = 1
GateState = Literal["PASSED", "FAILED", "BLOCKED", "NOT_CHECKED"]
GATES = (
    "elaboration",
    "functional_regression",
    "equivalence",
    "synthesis",
    "setup",
    "hold",
    "timing_coverage",
    "gds_generation",
    "gds_reopen",
    "router",
    "platform_drc",
    "lvs",
    "connectivity",
)
SKY130_LIBRARY = "sky130_fd_sc_hd__tt_025C_1v80.lib"
NATIVE_RSS = re.compile(rb"^Elapsed time: [^\r\n]+ Peak memory: ([0-9]+)KB\.$", re.M)
STAGE_METRICS = {
    "standard_cell_count": ("design__instance__count__stdcell", "count"),
    "standard_cell_area": ("design__instance__area__stdcell", "um2"),
    "die_area": ("design__die__area", "um2"),
    "core_area": ("design__core__area", "um2"),
    "utilization": ("design__instance__utilization__stdcell", "ratio"),
    "setup_worst_slack": ("timing__setup__ws", "ns"),
    "setup_tns": ("timing__setup__tns", "ns"),
    "hold_worst_slack": ("timing__hold__ws", "ns"),
    "hold_tns": ("timing__hold__tns", "ns"),
    "setup_clock_skew": ("clock__skew__setup", "ns"),
    "wirelength": ("route__wirelength", "um"),
}


def checked_artifact_bytes(root: Path, path: str, artifact: Artifact) -> bytes | None:
    size = artifact["size"]
    if size is None or size > 16 * 2**20:
        return None
    try:
        with (root / path).open("rb") as stream:
            content = stream.read(size + 1)
    except OSError:
        return None
    if hashlib.sha256(content).hexdigest() != artifact["sha256"]:
        return None
    return content


def add_stage_metrics(
    root: Path, variant: str, artifacts: dict[str, Artifact], metrics: dict[str, Metric]
) -> None:
    for spec in STAGE_REPORTS:
        path = f"logs/{variant}/{spec.metrics}"
        if not verified_artifact(artifacts, path):
            continue
        raw = checked_artifact_bytes(root, path, artifacts[path])
        if raw is None:
            continue
        try:
            data = json.loads(raw)
        except (ValueError, UnicodeDecodeError):
            continue
        if not isinstance(data, dict):
            continue
        for field, (reference, units) in STAGE_METRICS.items():
            native_key = f"{spec.prefix}__{reference}"
            value = number(data.get(native_key))
            if value is None:
                continue
            name = f"stage.{spec.stage}.{field}"
            metrics[name] = {
                "name": name,
                "value": value,
                "unit": units,
                "stage": spec.stage,
                "corner": None,
                "artifact": path,
                "reference": native_key,
                "method": "orfs-native-stage-metrics-v1",
                "status": "MEASURED",
            }


class Artifact(TypedDict):
    type: str
    location: str
    sha256: str | None
    size: int | None
    stage: str
    producer_version: str | None
    run_id: str
    attempt: str | None
    observed_at: str | None
    availability: Literal["VERIFIED", "MISSING", "MODIFIED", "UNKNOWN"]


class Metric(TypedDict):
    name: str
    value: Number | None
    unit: str
    stage: str
    corner: str | None
    artifact: str | None
    method: str
    reference: str | None
    status: Literal["MEASURED", "UNKNOWN"]


class Verification(TypedDict):
    state: GateState
    artifacts: list[str]


class Experiment(TypedDict):
    schema_version: int
    run_id: str
    origin: Literal["controller", "legacy_controller", "external"]
    design: str
    platform: str
    input_manifest: dict[str, Any]
    rtl_identity: str | None
    source_revision: str
    constraints: list[str]
    configuration_sha256: str | None
    toolchain: dict[str, str | None]
    corner: str | None
    attempts: list[dict[str, Any]]
    artifacts: dict[str, Artifact]
    metrics: dict[str, Metric]
    verification_gates: dict[str, Verification]
    required_gates: list[str]
    acceptance: GateState
    provenance: dict[str, Any]


def verified_artifact(artifacts: dict[str, Artifact], path: str) -> bool:
    return path in artifacts and artifacts[path]["availability"] == "VERIFIED"


def artifact_record(
    root: Path,
    run_id: str,
    relative: str,
    label: str,
    stage: str,
    expected: str | None,
    attempt: str | None = None,
) -> Artifact:
    path = root / relative
    if not path.resolve().is_relative_to(root):
        raise ValueError(f"artifact escapes run directory: {relative}")
    state: Literal["VERIFIED", "MISSING", "MODIFIED", "UNKNOWN"] = "UNKNOWN"
    size = None
    try:
        info = path.stat()
        if not stat.S_ISREG(info.st_mode):
            raise ValueError(f"artifact is not a regular file: {relative}")
        size = info.st_size
        if expected:
            state = "VERIFIED" if sha256(path) == expected else "MODIFIED"
    except FileNotFoundError:
        state = "MISSING"
    except OSError:
        pass
    return {
        "type": label,
        "location": relative,
        "sha256": expected,
        "size": size,
        "stage": stage,
        "producer_version": None,
        "run_id": run_id,
        "attempt": attempt,
        "observed_at": datetime.fromtimestamp(info.st_mtime, UTC).isoformat()
        if size is not None
        else None,
        "availability": state,
    }


def acceptance(gates: dict[str, Verification], required: list[str]) -> GateState:
    if not required or len(set(required)) != len(required) or not set(required).issubset(gates):
        raise ValueError("acceptance requires a nonempty set of known gates")
    states = [gates[name]["state"] for name in required]
    if "FAILED" in states:
        return "FAILED"
    return "PASSED" if all(state == "PASSED" for state in states) else "BLOCKED"


def evidence_gate(state: str, artifacts: dict[str, Artifact], refs: list[str]) -> Verification:
    mapped: dict[str, GateState] = {
        "passed": "PASSED",
        "failed": "FAILED",
        "blocked": "BLOCKED",
        "not checked": "NOT_CHECKED",
    }
    if state in {"passed", "failed"} and (
        not refs or any(not verified_artifact(artifacts, ref) for ref in refs)
    ):
        return {"state": "BLOCKED", "artifacts": refs}
    return {"state": mapped[state], "artifacts": refs}


def evidence_metrics(
    values: dict[str, Number | None],
    artifacts: dict[str, Artifact],
    variant: str,
    corner: str | None,
) -> dict[str, Metric]:
    specifications = {
        name: ("finish", f"logs/{variant}/6_report.json", key) for name, key in FINAL_FIELDS.items()
    }
    specifications.update(
        {
            name: ("route", f"logs/{variant}/5_2_route.json", key)
            for name, key in ROUTE_FIELDS.items()
        }
    )
    specifications.update(
        {
            "setup_wns_ns": (
                "finish",
                f"logs/{variant}/6_report.json",
                "min(finish__timing__setup__ws,0)",
            ),
            "hold_wns_ns": (
                "finish",
                f"logs/{variant}/6_report.json",
                "min(finish__timing__hold__ws,0)",
            ),
            "clock_period_ns": ("synth", f"results/{variant}/1_synth.sdc", "create_clock period"),
            "global_route_total_overflow": ("grt", f"logs/{variant}/5_1_grt.log", "last Total row"),
            "klayout_drc_report_value_count": (
                "drc",
                f"reports/{variant}/6_drc_count.rpt",
                "reported count",
            ),
        }
    )
    metrics: dict[str, Metric] = {}
    for name, (stage, path, reference) in specifications.items():
        value = values.get(name)
        if not verified_artifact(artifacts, path):
            value = None
        unit = (
            "ns"
            if name.endswith("_ns")
            else "um2"
            if name.endswith("_um2")
            else "um"
            if name.endswith("_um")
            else "W"
            if name.endswith("_w")
            else "count"
        )
        metrics[name] = {
            "name": name,
            "value": value,
            "unit": unit,
            "stage": stage,
            "corner": corner if name.startswith(("setup", "hold", "estimated_power")) else None,
            "artifact": path if path in artifacts else None,
            "reference": reference,
            "method": "orfs-normalization-v1",
            "status": "MEASURED" if value is not None else "UNKNOWN",
        }
    return metrics


def get_experiment(root: Path, *, legacy_root: Path | None = None) -> Experiment:
    root = root.resolve()
    if not (root / "run.sqlite3").is_file() and (root / "reference.json").is_file():
        return import_reference(root, root / "reference.json")
    data = report(root)
    manifest = data["manifest"]
    run_id = data.get("run_id") or fingerprint(["legacy", str(legacy_root or root)])[:32]
    artifacts: dict[str, Artifact] = {}
    attempts = data["attempts"]
    for attempt in attempts:
        for relative, digest in (attempt["artifacts"] or {}).items():
            artifacts[relative] = artifact_record(
                root,
                run_id,
                relative,
                Path(relative).suffix,
                attempt["stage"],
                digest,
                str(attempt["id"]),
            )
    for name, verification in data["verification"].items():
        for relative, digest in verification.get("artifacts", {}).items():
            artifacts[relative] = artifact_record(
                root,
                run_id,
                relative,
                Path(relative).suffix,
                name,
                digest,
                verification.get("attempt"),
            )
    if data["database_version"] == 2 and not data["input_error"]:
        rtl_inputs = [data["image"], manifest["config"], manifest["revision"]]
        if opentitan_aes(manifest["platform"], manifest["design"]):
            inputs = {
                "aes-input/aes.v": AES_RTL_DIGEST,
                "aes-input/config.mk": hashlib.sha256(AES_CONFIG.encode()).hexdigest(),
                "aes-input/block.sdc": hashlib.sha256(AES_SDC.encode()).hexdigest(),
            }
            for relative, digest in inputs.items():
                artifacts[relative] = artifact_record(
                    root, run_id, relative, Path(relative).suffix, "inputs", digest, None
                )
            data["rtl_source_revision"] = AES_RTL_REVISION
            rtl_inputs.append(AES_RTL_DIGEST)
        data.update(
            rtl_identity=fingerprint(rtl_inputs),
            library=SKY130_LIBRARY,
            corner="tt_025C_1v80",
        )
    data["tool_versions"] = tool_versions(root, artifacts)
    return assemble(root, run_id, manifest, attempts, artifacts, data)


def tool_versions(root: Path, artifacts: dict[str, Artifact]) -> dict[str, str]:
    versions = {}
    for relative, artifact in artifacts.items():
        if artifact["availability"] != "VERIFIED" or artifact["type"] != ".log":
            continue
        if Path(relative).name in {"1_2_yosys.log", "6_report.log"}:
            with (root / relative).open(encoding="utf-8", errors="replace") as text:
                header = text.readline()
            match = re.match(r"(Yosys|OpenROAD)\s+(\S+)", header)
            if match:
                versions[match[1].lower()] = match[2]
                artifact["producer_version"] = match[2]
    return versions


def assemble(
    root: Path,
    run_id: str,
    manifest: dict[str, Any],
    attempts: list[dict[str, Any]],
    artifacts: dict[str, Artifact],
    data: dict[str, Any],
    *,
    external: bool = False,
) -> Experiment:
    variant = f"{manifest['platform']}/{manifest['design']}/base"
    final = f"logs/{variant}/6_report.json"
    gds = f"results/{variant}/6_final.gds"
    sdc = f"results/{variant}/1_synth.sdc"

    def retained(path: str) -> bool:
        return verified_artifact(artifacts, path)

    raw_gates = data["gates"]
    gates = {name: evidence_gate("not checked", artifacts, []) for name in GATES}
    gates["setup"] = evidence_gate(raw_gates["setup"], artifacts, [final])
    gates["hold"] = evidence_gate(raw_gates["hold"], artifacts, [final])
    gates["gds_generation"] = evidence_gate(
        "passed" if retained(gds) else "not checked", artifacts, [gds]
    )
    reopen = next(
        (path for path in reversed(artifacts) if path.endswith("gds-validation.log")), None
    )
    gates["gds_reopen"] = evidence_gate(
        raw_gates["gds_integrity"],
        artifacts,
        [gds, reopen] if reopen else [gds, "gds-validation.log"],
    )
    drc_refs = [gds, f"reports/{variant}/6_drc_count.rpt", f"reports/{variant}/6_drc.lyrdb"]
    lvs_refs = [gds, f"logs/{variant}/6_lvs.log", f"results/{variant}/6_lvs.lvsdb"]
    gates["platform_drc"] = evidence_gate(raw_gates["drc"], artifacts, drc_refs)
    gates["lvs"] = evidence_gate(raw_gates["lvs"], artifacts, lvs_refs)
    sdc_text = (root / sdc).read_text(encoding="utf-8", errors="replace") if retained(sdc) else ""
    periods = set(re.findall(r"^create_clock\b[^\r\n]*\s-period\s+([0-9.]+)", sdc_text, re.M))
    values = dict(data["metrics"])
    values["clock_period_ns"] = float(next(iter(periods))) if len(periods) == 1 else None
    corner = data.get("corner")
    metrics = evidence_metrics(values, artifacts, variant, corner)
    if not data.get("input_error"):
        add_stage_metrics(root, variant, artifacts, metrics)
    count = metrics["router_drc_errors"]["value"]
    if count is not None:
        gates["router"] = evidence_gate(
            "passed" if count == 0 else "failed", artifacts, [f"logs/{variant}/5_2_route.json"]
        )
    latest_synth = next((a for a in reversed(attempts) if a["stage"] == "synth"), None)
    if latest_synth and not data.get("input_error"):
        gates["synthesis"] = evidence_gate(
            latest_synth["state"] if latest_synth["state"] in {"passed", "failed"} else "blocked",
            artifacts,
            list(latest_synth["artifacts"] or {}),
        )
    for item in attempts:
        duration = item.get("runtime_seconds")
        path = item.get("log")
        name = f"stage_runtime.{item['id']}"
        if isinstance(path, str) and retained(path) and duration is not None:
            metrics[name] = {
                "name": name,
                "value": duration,
                "unit": "s",
                "stage": item["stage"],
                "corner": None,
                "artifact": path,
                "reference": "attempt runtime",
                "method": "monotonic-wall-v1",
                "status": "MEASURED",
            }
            raw = checked_artifact_bytes(root, path, artifacts[path])
            rss = max((int(value) for value in NATIVE_RSS.findall(raw or b"")), default=None)
            if rss is not None:
                name = f"stage_peak_process_rss.{item['id']}"
                metrics[name] = {
                    "name": name,
                    "value": rss,
                    "unit": "KiB",
                    "stage": item["stage"],
                    "corner": None,
                    "artifact": path,
                    "reference": "maximum native timed subprocess RSS in the attempt log",
                    "method": "orfs-native-time-v1",
                    "status": "MEASURED",
                }
    for target, verification in data.get("verification", {}).items():
        path = f"attempts/{verification.get('attempt')}/{target}.log"
        duration = verification.get("runtime_seconds")
        if duration is not None and retained(path):
            name = f"verification_runtime.{target}"
            metrics[name] = {
                "name": name,
                "value": duration,
                "unit": "s",
                "stage": target,
                "corner": None,
                "artifact": path,
                "reference": "verification runtime",
                "method": "monotonic-wall-v1",
                "status": "MEASURED",
            }
    return {
        "schema_version": VERSION,
        "run_id": run_id,
        "design": manifest["design"],
        "platform": manifest["platform"],
        "origin": "external"
        if external
        else "controller"
        if data["database_version"]
        else "legacy_controller",
        "input_manifest": manifest,
        "rtl_identity": data.get("rtl_identity"),
        "source_revision": data.get("rtl_source_revision", manifest["revision"]),
        "constraints": [name for name in (sdc, "aes-input/block.sdc") if name in artifacts],
        "configuration_sha256": data.get("configuration_sha256"),
        "toolchain": {
            "image": data["image"],
            "orfs_revision": manifest["revision"],
            "library": data.get("library"),
            **data.get("tool_versions", {}),
        },
        "corner": corner,
        "attempts": attempts,
        "artifacts": artifacts,
        "metrics": metrics,
        "verification_gates": gates,
        "required_gates": list(GATES),
        "acceptance": acceptance(gates, list(GATES)),
        "provenance": {
            key: data.get(key)
            for key in (
                "database_version",
                "created_at",
                "manifest_sha256",
                "input_error",
                "verification",
                "memory_mib",
                "recorded_by",
            )
        },
    }


def import_reference(root: Path, index: Path) -> Experiment:
    root = root.resolve()
    record = json.loads(index.read_text(encoding="utf-8"))
    if record.get("schema_version") != VERSION or record.get("image") != IMAGE:
        raise ValueError("unsupported reference index or tool image")
    manifest = record["manifest"]
    if (
        manifest["revision"] != REVISION
        or manifest["platform"] != "sky130hd"
        or manifest["design"] not in {"gcd", "ibex"}
    ):
        raise ValueError("unsupported reference design or revision")
    run_id = fingerprint(record)
    artifacts: dict[str, Artifact] = {}
    stage_reports = {
        name: spec.stage
        for spec in STAGE_REPORTS
        for name in (spec.metrics, spec.timing, spec.sdc)
        if name is not None
    }
    for relative, digest in record["sha256"].items():
        if not isinstance(digest, str) or not re.fullmatch(r"[a-f0-9]{64}", digest):
            raise ValueError(f"invalid reference hash: {relative}")
        name = Path(relative).name
        stage = stage_reports.get(name) or (
            "drc"
            if "drc" in name
            else "lvs"
            if "lvs" in name
            else "synth"
            if name.startswith("1_")
            else "grt"
            if name.startswith("5_1")
            else "route"
            if name.startswith("5_")
            else "finish"
        )
        artifacts[relative] = artifact_record(
            root, run_id, relative, Path(relative).suffix, stage, digest
        )
    variant = f"sky130hd/{manifest['design']}/base"
    gds = f"results/{variant}/6_final.gds"
    final = f"logs/{variant}/6_report.json"

    def checked(path: str) -> bool:
        return verified_artifact(artifacts, path)

    # Pass the parser only files whose audited hashes still match.
    metrics, gates = summarize_verified(
        root, variant, {path for path in artifacts if checked(path)}
    )
    gates["gds_integrity"] = (
        "passed"
        if checked(gds)
        and any(
            path.endswith("gds-validation.log")
            and checked(path)
            and f"Validated GDS top {manifest['top']},"
            in (root / path).read_text(encoding="utf-8", errors="replace")
            for path in artifacts
        )
        else "blocked"
    )
    if not checked(final):
        gates.update(setup="blocked", hold="blocked")
    for name, paths in (
        ("drc", (gds, f"reports/{variant}/6_drc_count.rpt", f"reports/{variant}/6_drc.lyrdb")),
        ("lvs", (gds, f"logs/{variant}/6_lvs.log", f"results/{variant}/6_lvs.lvsdb")),
    ):
        if not all(checked(path) for path in paths):
            gates[name] = "blocked"
    data = {
        **record,
        "gates": gates,
        "metrics": metrics,
        "tool_versions": tool_versions(root, artifacts),
    }
    return assemble(root, run_id, manifest, [], artifacts, data, external=True)


def encode_evidence(experiment: Experiment) -> str:
    return json.dumps(experiment, sort_keys=True, separators=(",", ":"), allow_nan=False)


def decode_evidence(serialized: str) -> Experiment:
    from typing import cast

    data = json.loads(serialized)
    if not isinstance(data, dict) or data.get("schema_version") != VERSION:
        raise ValueError("unsupported evidence schema")
    if set(data) != set(Experiment.__required_keys__):
        raise ValueError("missing or unexpected experiment fields")
    for key in (
        "toolchain",
        "input_manifest",
        "artifacts",
        "metrics",
        "verification_gates",
        "provenance",
    ):
        if not isinstance(data[key], dict):
            raise ValueError(f"expected evidence object: {key}")
    for path, artifact in data["artifacts"].items():
        if not isinstance(artifact, dict) or set(artifact) != Artifact.__required_keys__:
            raise ValueError(f"invalid artifact record: {path}")
        if artifact["location"] != path or artifact["run_id"] != data["run_id"]:
            raise ValueError(f"artifact identity mismatch: {path}")
        if artifact["availability"] not in {"VERIFIED", "MISSING", "MODIFIED", "UNKNOWN"}:
            raise ValueError(f"invalid artifact availability: {path}")
        if artifact["availability"] == "VERIFIED" and (
            not isinstance(artifact["sha256"], str)
            or not re.fullmatch(r"[a-f0-9]{64}", artifact["sha256"])
            or type(artifact["size"]) is not int
            or artifact["size"] < 0
        ):
            raise ValueError(f"invalid verified artifact: {path}")
    if set(data["verification_gates"]) != set(GATES):
        raise ValueError("invalid verification gate set")
    for name, gate in data["verification_gates"].items():
        if (
            not isinstance(gate, dict)
            or set(gate) != Verification.__required_keys__
            or not isinstance(gate["artifacts"], list)
        ):
            raise ValueError(f"invalid gate record: {name}")
        if gate["state"] not in {"PASSED", "FAILED", "BLOCKED", "NOT_CHECKED"}:
            raise ValueError(f"invalid gate state: {name}")
        if gate["state"] in {"PASSED", "FAILED"} and (
            not gate["artifacts"]
            or any(
                data["artifacts"].get(ref, {}).get("availability") != "VERIFIED"
                for ref in gate["artifacts"]
            )
        ):
            raise ValueError(f"unverified gate references: {name}")
    for name, metric in data["metrics"].items():
        if (
            not isinstance(metric, dict)
            or set(metric) != Metric.__required_keys__
            or metric["name"] != name
        ):
            raise ValueError(f"invalid metric record: {name}")
        if metric["status"] == "MEASURED":
            from .metrics import number

            if (
                number(metric["value"]) is None
                or data["artifacts"].get(metric["artifact"], {}).get("availability") != "VERIFIED"
            ):
                raise ValueError(f"invalid measurement evidence: {name}")
        elif metric["status"] != "UNKNOWN" or metric["value"] is not None:
            raise ValueError(f"invalid measurement state: {name}")
    if not isinstance(data["required_gates"], list) or data["acceptance"] != acceptance(
        data["verification_gates"], data["required_gates"]
    ):
        raise ValueError("acceptance does not match gate verdict")
    return cast(Experiment, data)


def compare_experiments(
    baseline: Experiment, candidate: Experiment, *, descriptive: bool = False
) -> dict[str, Any]:
    differences: dict[str, object] = {}
    originals = (baseline, candidate)
    for field in (
        "design",
        "platform",
        "rtl_identity",
        "source_revision",
        "toolchain",
        "corner",
        "configuration_sha256",
        "required_gates",
    ):
        a, b = (dict(record).get(field) for record in originals)
        if a is None or b is None or a != b:
            differences[field] = [a, b]
    for field in ("parameters", "cores", "core_utilization"):
        a, b = (record["input_manifest"].get(field) for record in originals)
        if a != b:
            differences[field] = [a, b]
    constraints = [
        [
            record["artifacts"][ref]["sha256"]
            if verified_artifact(record["artifacts"], ref)
            else None
            for ref in record["constraints"]
        ]
        for record in originals
    ]
    if not constraints[0] or None in constraints[0] or constraints[0] != constraints[1]:
        differences["constraints"] = constraints
    runtimes = [
        sum(
            item["value"]
            for item in record["metrics"].values()
            if item["name"].startswith("stage_runtime.") and item["value"] is not None
        )
        if any(name.startswith("stage_runtime.") for name in record["metrics"])
        else None
        for record in originals
    ]
    deltas = {}
    incompatible_metrics = []
    for name, metric in baseline["metrics"].items():
        other = candidate["metrics"].get(name)
        if not other or metric["value"] is None or other["value"] is None:
            continue
        if any(
            metric.get(key) != other.get(key)
            for key in ("unit", "stage", "corner", "method", "reference")
        ):
            incompatible_metrics.append(name)
        elif not differences and not descriptive:
            deltas[name] = other["value"] - metric["value"]
    return {
        "mode": "descriptive" if descriptive else "controlled",
        "compatible": not differences,
        "differences": differences,
        "incompatible_metrics": incompatible_metrics,
        "deltas": deltas,
        "records": [
            {
                "run_id": item["run_id"],
                "metrics": item["metrics"] if descriptive or not differences else {},
                "verification": item["verification_gates"],
                "acceptance": item["acceptance"],
                "observed_attempt_seconds": runtime,
            }
            for item, runtime in zip(originals, runtimes, strict=True)
        ],
    }


def list_experiments(parent: Path) -> list[dict[str, Any]]:
    runs = []
    for root in sorted(parent.iterdir()):
        if not root.is_dir() or not (
            (root / "run.sqlite3").is_file() or (root / "reference.json").is_file()
        ):
            continue
        try:
            entry = get_experiment(root)
            runs.append(
                {
                    "directory": str(root),
                    "run_id": entry["run_id"],
                    "design": entry["design"],
                    "origin": entry["origin"],
                    "acceptance": entry["acceptance"],
                }
            )
        except (ValueError, OSError, sqlite3.Error) as error:
            runs.append({"directory": str(root), "error": str(error)})
    return runs

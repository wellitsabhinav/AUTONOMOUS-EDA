"""Bind AES qualification reports to a retained controller run."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from .aes_contract import BLOCK_SDC_DIGEST
from .evidence import Experiment, verified_artifact
from .manifest import fingerprint
from .opentitan_physical import INPUTS, RTL_DIGEST, RTL_REVISION
from .partition_readiness import metadata

RECEIPT = "verification/aes-qualification.json"
STATES = {"PASS", "FAIL", "BLOCKED", "UNKNOWN", "TIMEOUT", "INFRASTRUCTURE_ERROR"}
GATES = (
    "interface_timing",
    "pre_route_sta",
    "cipher_core",
    "peripheral",
    "structural",
    "differential",
    "formal",
    "resource_admission",
)


def digest_payload(data: dict[str, Any], key: str, *, exclude: tuple[str, ...] = ()) -> None:
    if data.get("schema_version") != 1 or fingerprint(
        {name: value for name, value in data.items() if name not in (key, *exclude)}
    ) != data.get(key):
        raise ValueError(f"AES report failed {key} integrity validation")


def identity(evidence: Experiment) -> dict[str, str]:
    artifacts = evidence["artifacts"]
    if evidence["input_manifest"].get("design") != "opentitan_aes" or any(
        not verified_artifact(artifacts, name) or artifacts[name]["sha256"] != digest
        for name, digest in zip(INPUTS[:2], (RTL_DIGEST, BLOCK_SDC_DIGEST), strict=True)
    ):
        raise ValueError("AES qualification source or constraint identity is unavailable")
    return {
        "run_id": evidence["run_id"],
        "rtl_revision": RTL_REVISION,
        "translation_sha256": RTL_DIGEST,
        "block_sdc_sha256": BLOCK_SDC_DIGEST,
    }


def record(
    evidence: Experiment,
    contract: Path,
    core: Path | None,
    peripheral: Path | None,
    resource: Path | None,
) -> dict[str, Any]:
    binding = identity(evidence)
    reports = {}
    sources = {}
    for name, path, key in (
        ("contract", contract, "contract_sha256"),
        ("core", core, "receipt_sha256"),
        ("peripheral", peripheral, "receipt_sha256"),
        ("resource", resource, "plan_sha256"),
    ):
        if path is None:
            continue
        data, digest = metadata(path)
        digest_payload(data, key, exclude=("pre_route",) if name == "contract" else ())
        reports[name] = data
        sources[name] = {"file": path.name, "sha256": digest}
    timing = reports["contract"]
    if "pre_route" in timing:
        digest_payload(timing["pre_route"], "native_receipt_sha256")
    if timing["identity"]["translation_sha256"] != RTL_DIGEST or (
        timing["inputs_sha256"]["block_sdc"] != BLOCK_SDC_DIGEST
    ):
        raise ValueError("Timing contract is for a different AES configuration")
    design = reports.get("peripheral")
    if design and (
        design["identity"]["translation_sha256"] != RTL_DIGEST
        or design["identity"]["source_manifest_sha256"]
        != timing["identity"]["source_manifest_sha256"]
    ):
        raise ValueError("Peripheral check is for a different source configuration")
    if core and reports["core"]["scope"] != "core":
        raise ValueError("Expected a cipher-core regression receipt")
    plan = reports.get("resource")
    if plan and (plan["source_run_id"] != binding["run_id"] or plan["stage"] != "route"):
        raise ValueError("Resource preflight does not belong to this AES route")

    states = dict.fromkeys(GATES, "UNKNOWN")
    states["interface_timing"] = timing["status"]
    states["pre_route_sta"] = timing.get("pre_route", {}).get("sta_parse", "UNKNOWN")
    states["cipher_core"] = reports.get("core", {}).get("status", "UNKNOWN")
    if design:
        states["peripheral"] = design["functional"]["original"]["status"]
        for name, stage in design["correspondence"].items():
            if name not in ("structural", "differential", "formal"):
                raise ValueError("Unsupported AES correspondence stage")
            states[name] = stage["status"]
    if plan:
        states["resource_admission"] = "PASS" if plan["execution_allowed"] else "BLOCKED"
    if not set(states.values()) <= STATES:
        raise ValueError("Unsupported AES qualification status")
    summary = {
        "schema_version": 1,
        **binding,
        "source_manifest_sha256": timing["identity"]["source_manifest_sha256"],
        "gates": states,
        "obligations": {
            key: entry["requirement"]
            for key, entry in timing["obligations"].items()
            if entry["status"] == "BLOCKED"
        },
        "evidence_references": sources,
    }
    return {**summary, "qualification_receipt_sha256": fingerprint(summary)}


def read(root: Path, evidence: Experiment) -> dict[str, Any] | None:
    path = root / RECEIPT
    if not path.exists():
        return None
    if path.is_symlink() or not path.resolve().is_relative_to(root.resolve()):
        raise ValueError("AES qualification receipt is outside the recorded run")
    data, _ = metadata(path)
    digest_payload(data, "qualification_receipt_sha256")
    if any(data.get(key) != value for key, value in identity(evidence).items()):
        raise ValueError("AES qualification receipt belongs to a different run")
    if set(data["gates"]) != set(GATES) or not set(data["gates"].values()) <= STATES:
        raise ValueError("Invalid AES qualification gate inventory")
    for reference in data["evidence_references"].values():
        if not re.fullmatch(r"[a-zA-Z0-9._-]{1,80}", reference["file"]) or not re.fullmatch(
            r"[0-9a-f]{64}", reference["sha256"]
        ):
            raise ValueError("Invalid AES qualification evidence reference")
    return data

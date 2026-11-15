"""Extract planning boundaries from normalized TopGen and native EDAM metadata."""

import hashlib
import json
import re
from collections import Counter
from pathlib import Path
from typing import Any

from .manifest import identifier


def metadata(path: Path) -> tuple[dict[str, Any], str]:
    with path.open("rb") as stream:
        raw = stream.read(8 * 1024**2 + 1)
    if len(raw) > 8 * 1024**2:
        raise ValueError(f"metadata exceeds 8 MiB: {path.name}")
    data = json.loads(raw)
    if not isinstance(data, dict):
        raise ValueError(f"expected a JSON object: {path.name}")
    return data, hashlib.sha256(raw).hexdigest()


def plan_partitions(topgen: Path, edam: Path, revision: str) -> dict[str, object]:
    if not re.fullmatch(r"[0-9a-f]{40}", revision):
        raise ValueError("pin the upstream source revision with a full commit SHA")
    top, topgen_sha = metadata(topgen)
    build, edam_sha = metadata(edam)
    if build.get("version") != "0.2.1" or not isinstance(build.get("files"), list):
        raise ValueError("expected the native EDAM 0.2.1 inventory")
    designs = top.get("module")
    if not isinstance(designs, list) or not designs:
        raise ValueError("TopGen metadata must contain a module list")
    partitions: dict[str, object] = {}
    for module in designs:
        if not isinstance(module, dict):
            raise ValueError("invalid TopGen module")
        name = identifier(module.get("name"), "instance")
        if name in partitions:
            raise ValueError(f"duplicate TopGen instance: {name}")
        partitions[name] = {
            "block": identifier(module.get("type"), "block"),
            "clocks": module.get("clock_srcs", {}),
            "resets": module.get("reset_connections", {}),
            "base_addrs": module.get("base_addrs", module.get("base_addr", {})),
            "memory": module.get("memory", {}),
            "power_domains": module.get("domain", []),
            "physical_boundary": "BLOCKED",
            "acceptance": "BLOCKED",
        }
    entries = build["files"]
    file_types: Counter[str] = Counter()
    generated = []
    for entry in entries:
        if not isinstance(entry, dict) or not isinstance(entry.get("name"), str):
            raise ValueError("invalid EDAM inventory entry")
        kind = entry.get("file_type") or "unspecified"
        file_types[str(kind)] += 1
        core = entry.get("core") or ""
        if not isinstance(core, str):
            raise ValueError("invalid EDAM core owner")
        path = entry["name"]
        if "/autogen/" in path or ":prim_abstract:" in core:
            generated.append({"path": path, "core": core})
    return {
        "schema_version": 1,
        "readiness_level": 0,
        "source_revision": revision,
        "topgen_design": identifier(top.get("name"), "TopGen design"),
        "native_toplevel": identifier(build.get("toplevel"), "EDAM toplevel"),
        "inputs_sha256": {"topgen_json": topgen_sha, "edam_json": edam_sha},
        "native_manifest_file_count": len(entries),
        "native_manifest_file_types": dict(sorted(file_types.items())),
        "core_dependencies": build.get("dependencies", {}),
        "generated_file_inventory": generated,
        "topgen_memories": top.get("memory", []),
        "topgen_pinout": top.get("pinout", {}),
        "topgen_clocks": top.get("clocks", {}),
        "topgen_resets": top.get("resets", {}),
        "partitions": partitions,
        "gates": {
            "source_discovery": "AVAILABLE",
            "elaboration": "BLOCKED",
            "interface_and_physical_boundary_qualification": "BLOCKED",
            "top_level_acceptance": "BLOCKED",
        },
    }

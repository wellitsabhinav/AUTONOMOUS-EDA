"""Import the ordered HDL synthesis fileset from a FuseSoC EDAM JSON export."""

import hashlib
import json
from pathlib import Path, PurePosixPath
from tempfile import TemporaryDirectory
from typing import Any

from .manifest import identifier, relative_path, rtl_manifest


def source_path(root: Path, edam: Path, value: object) -> str:
    if not isinstance(value, str) or not value.startswith("../src/"):
        raise ValueError(f"expected a FuseSoC exported file under ../src/: {value}")
    suffix = relative_path(value.removeprefix("../src/"), "EDAM file", (".sv", ".v", ".svh", ".vh"))
    candidate = edam.parent.parent / "src" / suffix
    if not candidate.resolve().is_relative_to(edam.parent.parent / "src"):
        raise ValueError(f"EDAM file escapes the export: {value}")
    if not candidate.is_file():
        raise ValueError(f"missing EDAM file: {value}")
    if not candidate.resolve().is_relative_to(root):
        raise ValueError(f"EDAM file is outside the manifest directory: {value}")
    return candidate.relative_to(root).as_posix()


def defines(data: Any) -> dict[str, str]:
    if not isinstance(data, dict):
        raise ValueError("EDAM parameters must be a mapping")
    result = {}
    for name, entry in data.items():
        if not isinstance(entry, dict) or entry.get("paramtype") != "vlogdefine":
            raise ValueError(f"unsupported EDAM parameter: {name}")
        value = entry.get("default")
        if type(value) is bool:
            # Edalize omits false boolean defines and emits true as 1.
            if value:
                result[name] = "1"
        elif isinstance(value, (str, int)):
            result[name] = str(value)
        else:
            raise ValueError(f"missing or unsupported EDAM define value: {name}")
    return result


def import_edam(
    edam: Path, output: Path, revision: str, generated_core_prefixes: tuple[str, ...] = ()
) -> dict[str, object]:
    """Import the standalone EDAM synthesis subset without running any generators or hooks."""
    edam = edam.resolve()
    output = output.absolute()
    root = output.parent.resolve()
    if output.exists() or output.is_symlink():
        raise ValueError("choose a new path for the resolved manifest")
    if not all(prefix and prefix.endswith(":") for prefix in generated_core_prefixes):
        raise ValueError("generated core prefixes must be nonempty and end in ':'")
    raw = edam.read_bytes()
    data = json.loads(raw)
    if not isinstance(data, dict) or data.get("version") != "0.2.1":
        raise ValueError("expected an EDAM 0.2.1 JSON export")
    if data.get("hooks") or data.get("vpi"):
        raise ValueError("EDAM hooks and VPI are unsupported")
    top = identifier(data.get("toplevel"), "EDAM toplevel")
    entries = data.get("files")
    if not isinstance(entries, list) or not entries:
        raise ValueError("EDAM must contain its ordered files list")
    sources: list[str] = []
    generated: list[str] = []
    includes: dict[str, None] = {}
    owners: dict[str, str] = {}
    matched_prefixes: set[str] = set()
    for entry in entries:
        if not isinstance(entry, dict) or entry.get("file_type") not in (
            "systemVerilogSource",
            "verilogSource",
        ):
            raise ValueError(f"unsupported EDAM file type: {entry}")
        if set(entry) - {"core", "file_type", "name", "is_include_file"}:
            raise ValueError(f"unsupported EDAM file attributes: {entry}")
        name = source_path(root, edam, entry.get("name"))
        core = entry.get("core")
        include = entry.get("is_include_file", False)
        if name in owners or not isinstance(core, str) or not core or type(include) is not bool:
            raise ValueError(f"duplicate or invalid EDAM file: {name}")
        owners[name] = core
        prefixes = {prefix for prefix in generated_core_prefixes if core.startswith(prefix)}
        matched_prefixes.update(prefixes)
        if include:
            includes[PurePosixPath(name).parent.as_posix()] = None
        else:
            sources.append(name)
            if prefixes:
                generated.append(name)
    if matched_prefixes != set(generated_core_prefixes):
        raise ValueError("a declared generated core prefix matched no EDAM file")
    parameters = defines(data.get("parameters", {}))
    fields: dict[str, object] = {
        "revision": revision,
        "frontend": "sv2v",
        "sources": sources,
        "generated_sources": generated,
        "include_dirs": list(includes),
        "defines": parameters,
    }
    # Use the same validation and hashing as subsequent independent manifest reads.
    manifest = rtl_manifest(output, top, fields)
    files = manifest.inputs()
    with TemporaryDirectory(prefix=".aeda-import-", dir=root) as temp:
        staged = Path(temp) / output.name
        lines = ["schema_version = 1", 'backend = "yosys"', f'top = "{top}"']
        for name, value in fields.items():
            if name != "defines":
                lines.append(f"{name} = {json.dumps(value)}")
        lines.append("[defines]")
        lines.extend(f"{key} = {json.dumps(value)}" for key, value in sorted(parameters.items()))
        staged.write_text("\n".join(lines) + "\n", encoding="utf-8", newline="\n")
        staged.replace(output)
    return {
        "schema_version": 1,
        "format": "fusesoc-edam-0.2.1",
        "edam_sha256": hashlib.sha256(raw).hexdigest(),
        "source_revision": revision,
        "manifest_sha256": manifest.fingerprint(files),
        "ordered_sources": sources,
        "file_owners": owners,
        "core_dependencies": data.get("dependencies", {}),
        "inputs_sha256": files,
        "generated_sources_sha256": {name: files[name] for name in generated},
    }

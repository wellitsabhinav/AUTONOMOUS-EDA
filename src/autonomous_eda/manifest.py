"""Versioned manifests for explicit RTL source sets and trusted ORFS configs."""

from __future__ import annotations

import hashlib
import json
import math
import re
import tomllib
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Literal


@dataclass(frozen=True)
class RTLManifest:
    top: str
    sources: tuple[Path, ...]
    backend: Literal["yosys"] = "yosys"
    design: str | None = None
    language: Literal["verilog", "systemverilog"] = "systemverilog"
    include_dirs: tuple[Path, ...] = ()
    defines: tuple[tuple[str, str], ...] = ()
    parameters: tuple[tuple[str, int], ...] = ()
    constraints: tuple[Path, ...] = ()
    generated_sources: tuple[Path, ...] = ()
    platform: str | None = None
    revision: str | None = None
    synthesis: Literal["elaborate", "synth"] = "elaborate"
    root: Path | None = None
    frontend: Literal["yosys", "sv2v"] = "yosys"

    def inputs(self) -> dict[str, str]:
        root = self.root or self.sources[0].parent
        files = [*self.sources, *self.constraints]
        for folder in self.include_dirs:
            files.extend(sorted(path for path in folder.rglob("*") if path.is_file()))
        if any(not path.resolve().is_relative_to(root) for path in files):
            raise ValueError("input escapes the manifest directory")
        return {path.relative_to(root).as_posix(): sha256(path) for path in dict.fromkeys(files)}

    def fingerprint(self, files: dict[str, str] | None = None) -> str:
        root = self.root or self.sources[0].parent
        return fingerprint(
            {
                "design": self.design,
                "top": self.top,
                "language": self.language,
                "sources": [path.relative_to(root).as_posix() for path in self.sources],
                "include_dirs": [path.relative_to(root).as_posix() for path in self.include_dirs],
                "constraints": [path.relative_to(root).as_posix() for path in self.constraints],
                "generated_sources": [
                    path.relative_to(root).as_posix() for path in self.generated_sources
                ],
                "defines": self.defines,
                "parameters": self.parameters,
                "platform": self.platform,
                "revision": self.revision,
                "synthesis": self.synthesis,
                "files": self.inputs() if files is None else files,
                **({"frontend": self.frontend} if self.frontend != "yosys" else {}),
            }
        )


def sha256(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def fingerprint(value: object) -> str:
    normalized = json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)
    return hashlib.sha256(normalized.encode()).hexdigest()


@dataclass(frozen=True)
class ORFSManifest:
    top: str
    design: str
    platform: str
    config: str
    revision: str
    clock_period_ns: float
    cores: int
    stage_timeout_seconds: int
    backend: Literal["orfs"] = "orfs"
    core_utilization: int | None = None


def identifier(value: object, field: str) -> str:
    if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", value):
        raise ValueError(f"{field} must be a simple identifier")
    return value


def integer(value: object, field: str, maximum: int) -> int:
    if type(value) is not int or not 1 <= value <= maximum:
        raise ValueError(f"{field} must be an integer between 1 and {maximum}")
    return value


def relative_path(value: object, field: str, extensions: tuple[str, ...]) -> PurePosixPath:
    if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9_./-]+", value):
        raise ValueError(f"{field} must be a relative path")
    path = PurePosixPath(value)
    if path.is_absolute() or ".." in path.parts or (extensions and path.suffix not in extensions):
        raise ValueError(f"invalid {field}: {value}")
    return path


def rtl_manifest(path: Path, top: str, data: dict[str, object]) -> RTLManifest:
    fields = {
        "design",
        "language",
        "sources",
        "include_dirs",
        "defines",
        "parameters",
        "constraints",
        "platform",
        "revision",
        "generated_sources",
        "synthesis",
        "frontend",
    }
    if set(data) - fields:
        raise ValueError(f"unexpected RTL fields: {sorted(set(data) - fields)}")
    root = path.resolve().parent

    def resolve(
        field: str, suffixes: tuple[str, ...] = (), *, directory: bool = False
    ) -> tuple[Path, ...]:
        values = data.get(field, [])
        if not isinstance(values, list):
            raise ValueError(f"{field} must be an ordered list")
        resolved = []
        for value in values:
            relative = relative_path(value, field, suffixes)
            candidate = root / relative
            if not candidate.resolve().is_relative_to(root) or not (
                candidate.is_dir() if directory else candidate.is_file()
            ):
                raise ValueError(f"{field} path missing or outside design directory: {candidate}")
            resolved.append(candidate)
        if len({candidate.resolve() for candidate in resolved}) != len(resolved):
            raise ValueError(f"duplicate {field}")
        return tuple(resolved)

    language = data.get("language", "systemverilog")
    synthesis = data.get("synthesis", "elaborate")
    frontend = data.get("frontend", "yosys")
    if language not in ("systemverilog", "verilog") or synthesis not in ("elaborate", "synth"):
        raise ValueError("unsupported HDL language or synthesis mode")
    if frontend not in ("yosys", "sv2v") or frontend == "sv2v" and language != "systemverilog":
        raise ValueError("sv2v frontend requires SystemVerilog")
    sources = resolve("sources", (".v", ".sv") if language == "systemverilog" else (".v",))
    generated = resolve("generated_sources", (".v", ".sv"))
    if not sources or not set(generated).issubset(sources):
        raise ValueError("provide ordered sources and include each generated source in that order")
    defines, parameters = data.get("defines", {}), data.get("parameters", {})
    if not isinstance(defines, dict) or not isinstance(parameters, dict):
        raise ValueError("defines and parameters must be tables")
    defines_typed = []
    for key, value in sorted(defines.items()):
        if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9_.'+-]*", value):
            raise ValueError(f"invalid define value for {key}")
        defines_typed.append((identifier(key, "define"), value))
    parameters_typed = []
    for key, value in sorted(parameters.items()):
        if type(value) is not int or not -(2**31) <= value < 2**31:
            raise ValueError(f"parameter {key} must be a signed 32-bit integer")
        parameters_typed.append((identifier(key, "parameter"), value))
    revision = data.get("revision")
    if revision is not None and (
        not isinstance(revision, str) or not re.fullmatch(r"[0-9a-f]{40}", revision)
    ):
        raise ValueError("RTL revision must be a full lowercase Git commit SHA")
    platform = data.get("platform")
    return RTLManifest(
        top,
        sources,
        design=identifier(data.get("design", top), "design"),
        language="verilog" if language == "verilog" else "systemverilog",
        include_dirs=resolve("include_dirs", directory=True),
        defines=tuple(defines_typed),
        parameters=tuple(parameters_typed),
        constraints=resolve("constraints", (".sdc",)),
        generated_sources=generated,
        platform=identifier(platform, "platform") if platform is not None else None,
        revision=revision,
        synthesis="synth" if synthesis == "synth" else "elaborate",
        root=root,
        frontend="sv2v" if frontend == "sv2v" else "yosys",
    )


def load_manifest(path: Path) -> RTLManifest | ORFSManifest:
    with path.open("rb") as stream:
        data = tomllib.load(stream)
    version = data.pop("schema_version", None)
    if type(version) is not int or version != 1:
        raise ValueError("schema_version must be the integer 1")
    backend = data.pop("backend", None)
    top = identifier(data.pop("top", None), "top")
    if backend == "yosys":
        return rtl_manifest(path, top, data)
    if backend != "orfs":
        raise ValueError("backend must be yosys or orfs")
    fields = {
        "design",
        "platform",
        "config",
        "revision",
        "clock_period_ns",
        "cores",
        "stage_timeout_seconds",
    }
    utilization = data.pop("core_utilization", None)
    if data.keys() != fields:
        raise ValueError(f"unexpected or missing ORFS fields: {sorted(data.keys() ^ fields)}")
    config = relative_path(data["config"], "config", (".mk",))
    design = identifier(data["design"], "design")
    platform = identifier(data["platform"], "platform")
    if config != PurePosixPath("designs", platform, design, "config.mk"):
        raise ValueError("config must select the matching ORFS design and platform")
    revision = data["revision"]
    if not isinstance(revision, str) or not re.fullmatch(r"[0-9a-f]{40}", revision):
        raise ValueError("revision must be a full lowercase Git commit SHA")
    period = data["clock_period_ns"]
    if type(period) not in (int, float) or not math.isfinite(period) or period <= 0:
        raise ValueError("clock_period_ns must be finite and positive")
    return ORFSManifest(
        top,
        design,
        platform,
        config.as_posix(),
        revision,
        float(period),
        integer(data["cores"], "cores", 256),
        integer(data["stage_timeout_seconds"], "stage_timeout_seconds", 86400),
        core_utilization=(
            integer(utilization, "core_utilization", 80) if utilization is not None else None
        ),
    )

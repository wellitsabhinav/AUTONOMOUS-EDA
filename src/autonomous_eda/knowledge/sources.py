"""Operator-approved inputs and projections from schema 1 evidence."""

import hashlib
import json
import os
import re
import stat
import tomllib
from dataclasses import dataclass
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import BinaryIO

from ..doctor import environment_secrets, redact_diagnostics
from ..evidence import Experiment
from ..mcp.registry import ID_PATTERN, MAX_CONFIG_BYTES, MAX_ROOTS
from ..mcp.snapshot import (
    MAX_DATABASE_BYTES,
    MAX_FILE_BYTES,
    Source,
    UnsafeEvidence,
    load_evidence,
    ordinary,
    posix_file,
    relative_parts,
    stamp,
    windows_file,
)
from .parsers import MAX_CHARS, MAX_LINES, TYPES, Excerpt, line_excerpt, parse

MAX_SOURCES = 256
MAX_TEXT_BYTES = 2 * 1024**2
TRUST = {"trusted_documentation", "historical_observation", "unverified_note", "hypothesis"}
METADATA = {"design", "platform", "run_id", "stage", "corner", "tool_version", "tool_revision"}
TEXT_SUFFIXES = {
    ".md",
    ".txt",
    ".rst",
    ".sdc",
    ".mk",
    ".toml",
    ".rpt",
    ".log",
    ".tcl",
    ".yaml",
    ".yml",
}
SENSITIVE = re.compile(
    r"-----BEGIN [A-Z ]*PRIVATE KEY-----|\b(?:gh[pousr]_[A-Za-z0-9]{20,}|"
    r"github_pat_[A-Za-z0-9_]{20,}|AKIA[A-Z0-9]{16}|sk-[A-Za-z0-9_-]{20,})\b|"
    r"\bBearer\s+[A-Za-z0-9_./+=-]{16,}",
    re.I,
)


@dataclass(frozen=True)
class Registration:
    id: str
    root: Source
    alias: str
    path: str
    kind: str
    trust: str
    metadata: dict[str, str]

    @property
    def signature(self) -> str:
        value = [str(self.root.root), self.alias, self.path, self.kind, self.trust, self.metadata]
        return hashlib.sha256(json.dumps(value, sort_keys=True).encode()).hexdigest()

    @property
    def reference(self) -> str:
        return f"{self.alias}/{self.path}"

    def evidence_root(self) -> Source:
        self.root.check()
        return Source.register(self.root.root.joinpath(*relative_parts(self.path)))


@dataclass(frozen=True)
class Document:
    source_hash: str
    reference: str
    excerpt: Excerpt
    metadata: dict[str, str]
    artifacts: dict[str, str]
    trust: str
    fact: str = ""
    locator: str = ""


def registry(config: Path) -> dict[str, Registration]:
    with config.open("rb") as stream:
        raw = stream.read(MAX_CONFIG_BYTES + 1)
    if len(raw) > MAX_CONFIG_BYTES:
        raise ValueError("Knowledge configuration exceeds its size limit")
    data = tomllib.loads(raw.decode("utf-8"))
    if set(data) != {"roots", "sources"}:
        raise ValueError("Knowledge configuration requires roots and sources tables")
    roots, entries = data["roots"], data["sources"]
    if not isinstance(roots, dict) or not 1 <= len(roots) <= MAX_ROOTS:
        raise ValueError("Invalid number of knowledge roots")
    if not isinstance(entries, dict) or len(entries) > MAX_SOURCES:
        raise ValueError("Invalid number of knowledge sources")
    if any(not re.fullmatch(ID_PATTERN, name) for name in (*roots, *entries)):
        raise ValueError("Use simple root and source identifiers up to 64 characters")
    approved: dict[str, Source] = {}
    for name, value in roots.items():
        if not isinstance(value, str):
            raise ValueError("Knowledge root paths must be strings")
        path = Path(value)
        if not path.is_absolute() or ".." in path.parts or path == Path(path.anchor):
            raise ValueError("Use an absolute, dedicated directory for each knowledge root")
        approved[name] = Source.register(path)
    registered = {}
    for name, entry in sorted(entries.items()):
        if not isinstance(entry, dict) or set(entry) - {
            "root",
            "path",
            "kind",
            "trust",
            "metadata",
        }:
            raise ValueError("Invalid knowledge source fields")
        alias, relative, kind = (entry.get(key) for key in ("root", "path", "kind"))
        if not isinstance(alias, str) or alias not in approved or not isinstance(relative, str):
            raise ValueError("Invalid source root or path")
        parts = relative_parts(relative)
        if any(part.startswith(".") for part in parts):
            raise ValueError("Hidden source paths are excluded")
        if not isinstance(kind, str) or kind not in TYPES | {"evidence"}:
            raise ValueError("Unsupported knowledge source type")
        trust = entry.get("trust", "unverified_note")
        metadata = entry.get("metadata", {})
        if kind == "evidence" and set(entry) != {"root", "path", "kind"}:
            raise ValueError("Evidence trust and metadata come from the recorded experiment")
        if not isinstance(trust, str) or trust not in TRUST:
            raise ValueError("Invalid registered trust classification")
        if (
            not isinstance(metadata, dict)
            or set(metadata) - METADATA
            or any(
                not isinstance(value, str) or not 1 <= len(value) <= 128
                for value in metadata.values()
            )
        ):
            raise ValueError("Invalid source metadata")
        if kind != "evidence" and Path(relative).suffix.lower() not in TEXT_SUFFIXES:
            raise ValueError("Knowledge text source has an unsupported extension")
        check_sensitive(json.dumps([name, alias, relative, metadata]))
        registered[name] = Registration(
            name, approved[alias], alias, relative, kind, trust, metadata
        )
    return registered


def check_sensitive(text: str) -> None:
    if SENSITIVE.search(text) or redact_diagnostics(text, environment_secrets()) != text:
        raise ValueError("Sensitive content detected in a knowledge source")


def read_file(source: Source, relative: str, limit: int, *, hash_only: bool = False) -> bytes:
    path = source.root.joinpath(*relative_parts(relative))
    source.check()
    for parent in reversed(path.relative_to(source.root).parents):
        ordinary(source.root / parent, directory=True)
    before = ordinary(path, directory=False)
    opener = windows_file if os.name == "nt" else posix_file
    with opener(path, source) as stream:
        info = os.fstat(stream.fileno())
        if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or stamp(info) != stamp(before):
            raise UnsafeEvidence("Knowledge source changed during open")
        if info.st_size > limit:
            raise ValueError("Knowledge source exceeds its byte limit")
        result = bounded_hash(stream, info.st_size) if hash_only else stream.read(limit + 1)
        if len(result) > limit or stamp(os.fstat(stream.fileno())) != stamp(before):
            raise UnsafeEvidence("Knowledge source changed during capture")
    return result


def bounded_hash(stream: BinaryIO, size: int) -> bytes:
    digest = hashlib.sha256()
    remaining = size
    while remaining:
        block = stream.read(min(remaining, 1024**2))
        if not block:
            raise UnsafeEvidence("Knowledge source shrank during capture")
        digest.update(block)
        remaining -= len(block)
    return digest.digest()


def decoded(raw: bytes) -> str:
    text = raw.decode("utf-8-sig").replace("\r\n", "\n").replace("\r", "\n")
    if any(ord(ch) < 32 and ch not in "\n\t" for ch in text):
        raise ValueError("Knowledge sources must contain UTF-8 text")
    check_sensitive(text)
    return text


def current_hash(source: Source, relative: str, limit: int) -> str:
    return read_file(source, relative, limit, hash_only=True).hex()


def evidence_documents(
    entry: Registration, target: Path, record: Experiment, receipt_hash: str
) -> list[Document]:
    facts = []
    for key, metric in record["metrics"].items():
        if metric["status"] == "MEASURED" and metric["artifact"]:
            facts.append(
                (
                    key,
                    f"{metric['value']} {metric['unit']}",
                    [metric["artifact"]],
                    metric["stage"],
                    metric["corner"],
                    metric["reference"],
                )
            )
    for key, gate in record["verification_gates"].items():
        if gate["state"] in {"PASSED", "FAILED"} and gate["artifacts"]:
            paths = gate["artifacts"]
            if key in {"platform_drc", "lvs", "gds_reopen"}:
                # Lead with the result text while retaining the layout dependency.
                paths = sorted(paths, key=lambda path: not path.endswith((".log", ".rpt")))
            first = record["artifacts"][paths[0]]
            stage = {"platform_drc": "drc", "lvs": "lvs"}.get(key, first["stage"])
            facts.append((key, gate["state"], paths, stage, record["corner"], None))

    metadata = {key: str(record[key]) for key in ("design", "platform", "run_id")}
    toolchain = record["toolchain"]
    for key, value in (
        ("tool_revision", toolchain.get("orfs_revision")),
        ("tool_version", toolchain.get("openroad")),
    ):
        if value and value != "unknown":
            metadata[key] = value
    check_sensitive(json.dumps(metadata))
    documents = []
    artifact_lines: dict[str, list[str]] = {}
    for key, value, paths, stage, corner, locator in facts:
        artifacts = {}
        for path in paths:
            artifact = record["artifacts"][path]
            if artifact["availability"] == "VERIFIED" and artifact["sha256"]:
                artifacts[path] = artifact["sha256"]
        if len(artifacts) != len(set(paths)):
            continue
        if record["origin"] != "external":
            if (target / "manifest.toml").is_file():
                artifacts["manifest.toml"] = hashlib.sha256(
                    (target / "manifest.toml").read_bytes()
                ).hexdigest()
            if stage in {"drc", "lvs"}:
                sidecar = f"verification-{stage}.json"
                if not (target / sidecar).is_file():
                    continue
                artifacts[sidecar] = hashlib.sha256((target / sidecar).read_bytes()).hexdigest()
        if locator:
            check_sensitive(locator)
        excerpt = Excerpt(0, 0, "")
        # The structured locator is authoritative. Attach a line citation only
        # when the exact report key occurs on a single, identifiable source line.
        if locator and re.fullmatch(r"[\w:.-]{1,128}", locator) and paths[0].endswith(".json"):
            path = paths[0]
            if path not in artifact_lines:
                artifact_lines[path] = decoded((target / path).read_bytes()).split("\n", MAX_LINES)
            lines = artifact_lines[path]
            matches = [
                i for i, line in enumerate(lines) if line.lstrip().startswith(f'"{locator}":')
            ]
            if len(matches) == 1 and len(lines[matches[0]]) <= MAX_CHARS:
                excerpt = line_excerpt(lines, matches[0], matches[0] + 1)
        label = key.replace("_", " ")
        heading = f"{metadata['design']} {metadata['platform']} {label}: {value}"
        if corner:
            heading += f" at timing corner {corner}" if stage == "finish" else f" at {corner}"
        check_sensitive(heading)
        excerpt = Excerpt(excerpt.start, excerpt.end, excerpt.text, heading, {"value": value})
        documents.append(
            Document(
                receipt_hash,
                f"{entry.reference}/{paths[0]}",
                excerpt,
                {**metadata, "stage": stage, **({"corner": corner} if corner else {})},
                artifacts,
                "verified_measured",
                key,
                f"schema1/{key}" + (f"/{locator}" if locator else ""),
            )
        )
    return documents


def capture(entry: Registration) -> tuple[str, str, list[Document]]:
    if entry.kind == "evidence":
        with TemporaryDirectory(prefix="aeda-knowledge-") as temporary:
            root = entry.evidence_root()
            target = Path(temporary)
            try:
                record = load_evidence(root, target)
            except (KeyError, TypeError) as error:
                raise ValueError("Malformed experiment evidence") from error
            receipt = "reference.json" if record["origin"] == "external" else "run.sqlite3"
            # The snapshot has already bounded and copied this receipt. Its
            # private temporary directory may have a Windows short-path alias.
            with (target / receipt).open("rb") as stream:
                digest = hashlib.file_digest(stream, "sha256").hexdigest()
            return digest, receipt, evidence_documents(entry, target, record, digest)
    if entry.trust not in TRUST or Path(entry.path).suffix.lower() not in TEXT_SUFFIXES:
        raise ValueError("Unsupported text source extension or trust classification")
    raw = read_file(entry.root, entry.path, MAX_TEXT_BYTES)
    digest = hashlib.sha256(raw).hexdigest()
    excerpts = parse(decoded(raw), entry.kind)
    return (
        digest,
        entry.path,
        [
            Document(
                digest,
                entry.reference,
                excerpt,
                {
                    **entry.metadata,
                    **{key: value for key, value in excerpt.fields.items() if key in METADATA},
                },
                {},
                entry.trust,
            )
            for excerpt in excerpts
        ],
    )


def integrity(entry: Registration, relative: str, digest: str, *, artifact: bool = False) -> str:
    try:
        root = entry.evidence_root() if entry.kind == "evidence" else entry.root
        limit = MAX_TEXT_BYTES
        if artifact:
            if entry.kind != "evidence":
                return "unavailable"
            limit = MAX_FILE_BYTES
        elif entry.kind == "evidence":
            if relative not in {"reference.json", "run.sqlite3"}:
                return "unavailable"
            if relative == "reference.json" and os.path.lexists(root.root / "run.sqlite3"):
                return "modified"
            if any(
                os.path.lexists(root.root / f"run.sqlite3-{s}") for s in ("wal", "shm", "journal")
            ):
                return "unavailable"
            limit = MAX_DATABASE_BYTES
        elif relative != entry.path:
            return "unavailable"
        if current_hash(root, relative, limit) != digest:
            return "modified"
    except FileNotFoundError:
        return "missing"
    except (ValueError, OSError):
        return "unavailable"
    return "verified" if entry.kind == "evidence" else "current"

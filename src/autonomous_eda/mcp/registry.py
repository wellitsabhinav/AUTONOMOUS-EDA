"""Operator-owned allowlist for local MCP experiments."""

import re
import tomllib
from dataclasses import dataclass
from pathlib import Path

from .snapshot import Source, UnsafeEvidence, relative_parts

ID_PATTERN = r"^[a-zA-Z0-9][a-zA-Z0-9_-]{0,63}$"
MAX_EXPERIMENTS = 128
MAX_ROOTS = 16
MAX_CONFIG_BYTES = 64 * 1024


@dataclass(frozen=True)
class Registry:
    roots: dict[str, Source]
    experiments: dict[str, Source]

    @classmethod
    def load(cls, config: Path) -> "Registry":
        with config.open("rb") as stream:
            raw = stream.read(MAX_CONFIG_BYTES + 1)
        if len(raw) > MAX_CONFIG_BYTES:
            raise ValueError("MCP configuration exceeds its size limit")
        data = tomllib.loads(raw.decode("utf-8"))
        if set(data) != {"roots", "experiments"}:
            raise ValueError("MCP configuration requires roots and experiments tables")
        roots, experiments = data["roots"], data["experiments"]
        if not isinstance(roots, dict) or not 1 <= len(roots) <= MAX_ROOTS:
            raise ValueError("Invalid number of MCP roots")
        if not isinstance(experiments, dict) or not 1 <= len(experiments) <= MAX_EXPERIMENTS:
            raise ValueError("Invalid number of MCP experiments")
        if any(not re.fullmatch(ID_PATTERN, name) for name in (*roots, *experiments)):
            raise ValueError("Use simple MCP identifiers up to 64 characters")
        approved: dict[str, Source] = {}
        for name, value in roots.items():
            if not isinstance(value, str):
                raise ValueError("MCP root paths must be strings")
            path = Path(value)
            if not path.is_absolute() or ".." in path.parts or path == Path(path.anchor):
                raise UnsafeEvidence("Use an absolute, dedicated directory for each MCP root")
            approved[name] = Source.register(path)
        entries: dict[str, Source] = {}
        for name, entry in experiments.items():
            if not isinstance(entry, dict) or set(entry) != {"root", "directory"}:
                raise ValueError("Each MCP experiment needs a root alias and relative directory")
            alias, relative = entry["root"], entry["directory"]
            if not isinstance(alias, str) or alias not in approved or not isinstance(relative, str):
                raise ValueError("Invalid MCP experiment root or directory")
            root = approved[alias]
            source = Source.register(root.root.joinpath(*relative_parts(relative)))
            if not source.root.is_relative_to(root.root):
                raise UnsafeEvidence("Experiment directory escaped its configured root")
            entries[name] = source
        return cls(approved, dict(sorted(entries.items())))

"""Immutable chip stage attempts, hash verification, and bounded EDA dispatch."""

from __future__ import annotations

import re
import shutil
import subprocess
import time
import uuid
from dataclasses import asdict, dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any

from .io_ring import ENGINE
from .manifest import fingerprint, sha256
from .mcp.snapshot import ordinary
from .orfs import container_command
from .reference import IMAGE
from .verification.process import execute
from .verification.report import atomic, read_json

STAGE_SCHEMA = 1


@dataclass(frozen=True)
class Stage:
    folder: Path
    receipt: dict[str, Any]

    def output(self, name: str) -> Path:
        if name not in self.receipt["outputs"]:
            raise ValueError(f"stage did not export {name}")
        return self.folder / name

    def complete(self, marker: str, *outputs: str) -> bool:
        status = self.receipt["execution"]
        return (
            status["return_code"] == 0
            and not status["stop"]
            and set(outputs) <= self.receipt["outputs"].keys()
            and marker in self.stdout().splitlines()
        )

    def stdout(self) -> str:
        return self.output("tool.stdout.log").read_text(encoding="utf-8", errors="replace")


def stage_key(
    tool: str,
    args: list[str],
    inputs: dict[str, str],
    scripts: dict[str, str],
    backend: dict[str, str],
) -> str:
    return fingerprint([STAGE_SCHEMA, IMAGE, tool, args, inputs, scripts, backend])


def verified_stage(folder: Path, key: str) -> Stage:
    pointer = read_json(folder / f"{key}.json")
    attempt = pointer["attempt"]
    if not re.fullmatch(r"[0-9a-f]{32}", attempt):
        raise ValueError("invalid chip stage attempt")
    work = folder / attempt
    ordinary(work, directory=True)
    path = work / "receipt.json"
    receipt = read_json(path)
    if pointer["receipt_sha256"] != sha256(path) or receipt["key"] != key:
        raise ValueError("chip stage receipt hash mismatch")
    for name, expected in {
        **receipt["inputs"],
        **receipt["scripts"],
        **receipt["outputs"],
    }.items():
        if not re.fullmatch(r"[a-zA-Z0-9_.-]+", name) or name in {".", ".."}:
            raise ValueError("invalid chip stage output path")
        ordinary(work / name, directory=False)
        if sha256(work / name) != expected:
            raise ValueError(f"stale chip stage artifact: {name}")
    return Stage(work, receipt)


def dispatch(
    docker: str,
    root: Path,
    name: str,
    tool: str,
    args: list[str],
    *,
    backend: dict[str, str],
    inputs: dict[str, Path],
    scripts: dict[str, str],
    outputs: tuple[str, ...],
) -> Stage:
    for path in inputs.values():
        ordinary(path, directory=False)
    hashes = {filename: sha256(path) for filename, path in inputs.items()}
    key = stage_key(tool, args, hashes, scripts, backend)
    folder = root / "stages" / name
    folder.mkdir(exist_ok=True, parents=True)
    ordinary(folder.parent, directory=True)
    ordinary(folder, directory=True)
    try:
        previous = verified_stage(folder, key)
        if (
            previous.receipt["execution"]["return_code"] == 0
            and not previous.receipt["execution"]["stop"]
            and set(outputs) <= previous.receipt["outputs"].keys()
        ):
            return previous
    except (OSError, ValueError, KeyError, TypeError):
        pass
    attempt = uuid.uuid4().hex
    work = folder / attempt
    work.mkdir()
    for filename, source in inputs.items():
        shutil.copyfile(source, work / filename)
        if sha256(work / filename) != hashes[filename]:
            raise ValueError(f"chip input changed during staging: {filename}")
    for filename, script in scripts.items():
        (work / filename).write_text(script, encoding="utf-8", newline="\n")
    name_in_docker = f"aeda-chip-{attempt}"
    command = container_command(docker, work, name_in_docker, 1, 2400)
    command[-1:] = ["--pids-limit", "128", "--entrypoint", tool, IMAGE, *args]
    result = execute(command, work, "tool", time.monotonic() + 180, lambda: False)
    if result.stop:
        subprocess.run(
            [docker, "rm", "-f", name_in_docker], capture_output=True, timeout=10, check=False
        )
    artifacts = {}
    for filename in (*outputs, result.stdout, result.stderr):
        path = work / filename
        if path.is_file():
            ordinary(path, directory=False)
            artifacts[filename] = sha256(path)
    receipt = {
        "key": key,
        "attempt": attempt,
        "image": IMAGE,
        "inputs": hashes,
        "backend": backend,
        "scripts": {filename: sha256(work / filename) for filename in scripts},
        "execution": asdict(result),
        "outputs": artifacts,
    }
    atomic(work / "receipt.json", receipt)
    atomic(
        folder / f"{key}.json",
        {"attempt": attempt, "receipt_sha256": sha256(work / "receipt.json")},
    )
    if result.stop == "cancelled":
        raise KeyboardInterrupt
    return Stage(work, receipt)


def openroad_args(script: str = "stage.tcl") -> list[str]:
    return ["-no_init", "-threads", "1", "-exit", f"/work/{script}"]


@lru_cache(maxsize=4)
def pinned_version(docker: str, tool: str, flag: str) -> str:
    result = subprocess.run(
        [
            docker,
            "run",
            "--rm",
            "--pull=never",
            "--network=none",
            "--entrypoint",
            tool,
            IMAGE,
            flag,
        ],
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    if result.returncode:
        raise ValueError(f"could not identify the pinned tool version: {tool}")
    return result.stdout.strip()


def image_hashes(docker: str, files: list[str]) -> dict[str, str]:
    requested = sorted({ENGINE, *files})
    result = subprocess.run(
        [
            docker,
            "run",
            "--rm",
            "--pull=never",
            "--network=none",
            "--entrypoint",
            "sha256sum",
            IMAGE,
            *requested,
        ],
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    records = {line.split(maxsplit=1)[1]: line[:64] for line in result.stdout.splitlines()}
    if (
        result.returncode
        or set(records) != set(requested)
        or any(not re.fullmatch(r"[a-f0-9]{64}", digest) for digest in records.values())
    ):
        raise ValueError("could not inventory the pinned chip tools and platform files")
    return records

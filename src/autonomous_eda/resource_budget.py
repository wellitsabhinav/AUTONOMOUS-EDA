"""Cooperative wall-time and retained artifact limits for controller dispatch."""

import os
import time
from pathlib import Path

from .manifest import integer
from .orfs import DispatchGuard, DispatchStopped

MIB = 2**20


def tree_bytes(root: Path) -> int:
    total = 0
    for directory, _, names in os.walk(root, followlinks=False):
        for name in names:
            path = Path(directory) / name
            try:
                if not path.is_symlink():
                    total += path.stat().st_size
            except FileNotFoundError:
                # Native tools atomically replace temporary files during a scan.
                continue
    return total


def run_guard(
    root: Path, *, wall_seconds: int | None, artifact_mib: int | None
) -> DispatchGuard | None:
    if wall_seconds is None and artifact_mib is None:
        return None
    deadline = (
        time.monotonic() + integer(wall_seconds, "wall-clock-seconds", 7 * 86400)
        if wall_seconds is not None
        else None
    )
    limit = (
        integer(artifact_mib, "max-artifact-mib", 1048576) * MIB
        if artifact_mib is not None
        else None
    )
    next_sample = 0.0

    def guard() -> float:
        nonlocal next_sample
        now = time.monotonic()
        if deadline is not None and now >= deadline:
            raise DispatchStopped("wall-clock budget exceeded")
        if limit is not None and now >= next_sample:
            next_sample = now + 1.0
            used = tree_bytes(root)
            if used > limit:
                raise DispatchStopped(
                    f"artifact budget exceeded ({used} bytes recorded; limit {limit} bytes)"
                )
        return deadline - now if deadline is not None else float("inf")

    return guard

"""Bounded native dispatch for fixed verification commands."""

from __future__ import annotations

import os
import signal
import subprocess
import sys
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from ..experiment import now

MAX_LOG = 4 * 1024**2


@dataclass(frozen=True)
class Execution:
    command: list[str]
    return_code: int | None
    started_at: str
    runtime_seconds: float
    stop: str | None
    stdout: str
    stderr: str


def stop_process(process: subprocess.Popen[bytes]) -> None:
    if sys.platform == "win32":
        try:
            subprocess.run(
                ["taskkill.exe", "/PID", str(process.pid), "/T", "/F"],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=5,
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired):
            pass
        if process.poll() is None:
            process.kill()
    else:
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
    process.wait(timeout=5)


def execute(
    command: list[str],
    cwd: Path,
    label: str,
    deadline: float,
    cancelled: Callable[[], bool],
) -> Execution:
    started_at, start = now(), time.monotonic()
    stdout, stderr = cwd / f"{label}.stdout.log", cwd / f"{label}.stderr.log"
    reason = None
    code = None
    with stdout.open("xb") as out, stderr.open("xb") as err:
        process = None
        try:
            if cancelled():
                reason = "cancelled"
            elif start >= deadline:
                reason = "timeout"
            else:
                process = subprocess.Popen(
                    command,
                    cwd=cwd,
                    stdin=subprocess.DEVNULL,
                    stdout=out,
                    stderr=err,
                    start_new_session=os.name != "nt",
                    creationflags=subprocess.CREATE_NEW_PROCESS_GROUP if os.name == "nt" else 0,
                )
                while True:
                    code = process.poll()
                    if cancelled():
                        reason = "cancelled"
                    elif max(stdout.stat().st_size, stderr.stat().st_size) > MAX_LOG:
                        reason = "output limit exceeded"
                    elif time.monotonic() >= deadline:
                        reason = "timeout"
                    if reason or code is not None:
                        break
                    time.sleep(0.05)
        except KeyboardInterrupt:
            reason = "cancelled"
        except OSError:
            reason = "could not launch or monitor tool"
        finally:
            if process is not None and (reason or process.poll() is None):
                stop_process(process)
    return Execution(
        command, code, started_at, time.monotonic() - start, reason, stdout.name, stderr.name
    )


def log_text(work: Path, name: str) -> str:
    with (work / name).open("rb") as stream:
        return stream.read(MAX_LOG).decode("utf-8", errors="replace")

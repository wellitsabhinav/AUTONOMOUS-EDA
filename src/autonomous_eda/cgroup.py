"""Read Linux accounting for a Docker container whose cgroup is visible to the host."""

from __future__ import annotations

import re
from pathlib import Path

COUNTERS_V2 = {
    "memory.events": {"oom": "oom_events", "oom_kill": "oom_kills"},
    "cpu.stat": {
        "usage_usec": "cpu_usage_usec",
        "nr_throttled": "cpu_throttle_events",
        "throttled_usec": "cpu_throttled_usec",
    },
    "pids.events": {"max": "process_limit_events"},
}


def read(path: Path) -> str:
    try:
        with path.open(encoding="ascii", errors="replace") as stream:
            return stream.read(16384).strip()
    except OSError:
        return ""


def number(value: str) -> int | None:
    return int(value) if re.fullmatch(r"[0-9]{1,20}", value) else None


def counters(path: Path) -> dict[str, int]:
    values = {}
    for line in read(path).splitlines():
        fields = line.split()
        if len(fields) == 2 and (value := number(fields[1])) is not None:
            values[fields[0]] = value
    return values


def sample_v2(root: Path) -> dict[str, int]:
    values = {}
    for filename, name in (
        ("memory.current", "memory_current_bytes"),
        ("memory.peak", "memory_kernel_peak_bytes"),
        ("memory.swap.current", "swap_current_bytes"),
    ):
        if (value := number(read(root / filename))) is not None:
            values[name] = value
    for filename, names in COUNTERS_V2.items():
        observed = counters(root / filename)
        values.update({name: observed[key] for key, name in names.items() if key in observed})
    return values


def sample_v1(roots: dict[str, Path]) -> dict[str, int]:
    values = {}
    for key, filename, name, scale in (
        ("memory", "memory.usage_in_bytes", "memory_current_bytes", 1),
        ("memory", "memory.max_usage_in_bytes", "memory_kernel_peak_bytes", 1),
        ("memory", "memory.failcnt", "memory_limit_events", 1),
        ("cpuacct", "cpuacct.usage", "cpu_usage_usec", 1000),
    ):
        if key in roots and (value := number(read(roots[key] / filename))) is not None:
            values[name] = value // scale
    if "cpu" in roots:
        cpu = counters(roots["cpu"] / "cpu.stat")
        if "nr_throttled" in cpu:
            values["cpu_throttle_events"] = cpu["nr_throttled"]
        if "throttled_time" in cpu:
            values["cpu_throttled_usec"] = cpu["throttled_time"] // 1000
    if "memory" in roots:
        oom = counters(roots["memory"] / "memory.oom_control")
        if "oom_kill" in oom:
            values["oom_kills"] = oom["oom_kill"]
    if "pids" in roots:
        pid_events = counters(roots["pids"] / "pids.events")
        if "max" in pid_events:
            values["process_limit_events"] = pid_events["max"]
    return values


def locate(container_id: str, pid: int) -> dict[str, Path]:
    """Require the full Docker ID in the cgroup path before trusting a host PID."""
    if pid <= 0 or not re.fullmatch(r"[0-9a-f]{64}", container_id):
        return {}
    base = Path("/sys/fs/cgroup")
    roots = {}
    for line in read(Path(f"/proc/{pid}/cgroup")).splitlines():
        fields = line.split(":", 2)
        if len(fields) != 3:
            continue
        _, controllers, pathname = fields
        relative = Path(pathname.lstrip("/"))
        if relative.name not in {container_id, f"docker-{container_id}.scope"}:
            continue
        if ".." in relative.parts:
            continue
        if not controllers and (base / relative).is_dir():
            return {"v2": base / relative}
        for controller in set(controllers.split(",")) & {"memory", "cpu", "cpuacct", "pids"}:
            for mount in (controller, controllers, "cpu,cpuacct", "cpuacct,cpu"):
                candidate = base / mount / relative
                if candidate.is_dir() and (candidate / "cgroup.procs").is_file():
                    roots[controller] = candidate
                    break
    return roots


def process_rss(directory: Path) -> int | None:
    pids = read(directory / "cgroup.procs").splitlines()
    if not pids or len(pids) > 128 or any(not pid.isdecimal() for pid in pids):
        return None
    total = 0
    for pid in pids:
        match = re.search(r"^VmRSS:\s+(\d+) kB$", read(Path(f"/proc/{pid}/status")), re.M)
        if match is None:
            return None
        total += int(match[1]) * 1024
    return total


def available_memory() -> int | None:
    match = re.search(r"^MemAvailable:\s+(\d+) kB$", read(Path("/proc/meminfo")), re.M)
    return int(match[1]) * 1024 if match else None

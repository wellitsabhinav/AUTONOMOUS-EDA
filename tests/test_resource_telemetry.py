import json
import os
import shutil
import subprocess
import sys
import time
import uuid
from pathlib import Path

import pytest

from autonomous_eda.cgroup import sample_v1, sample_v2
from autonomous_eda.orfs import (
    container_command,
    execute,
    record_interrupted_dispatch,
    remove_interrupted_container,
)
from autonomous_eda.reference import IMAGE
from autonomous_eda.telemetry import (
    ResourceContext,
    classify,
    docker_inspect,
    encode,
    read_samples,
    receipt_path,
    samples_path,
    update_peaks,
)


def test_cgroup_accounting(tmp_path: Path) -> None:
    files = {
        "memory.current": "2200",
        "memory.peak": "3400",
        "memory.events": "oom 4\noom_kill 1\n",
        "cpu.stat": "usage_usec 1234\nnr_throttled 2\nthrottled_usec 55\n",
        "pids.events": "max 0\n",
        "memory.usage_in_bytes": "400",
        "memory.max_usage_in_bytes": "800",
        "memory.failcnt": "12",
        "cpuacct.usage": "7000000",
        "memory.oom_control": "oom_kill_disable 0\nunder_oom 0\n",
    }
    for file, value in files.items():
        (tmp_path / file).write_text(value, encoding="ascii")
    assert sample_v2(tmp_path) == {
        "memory_current_bytes": 2200,
        "memory_kernel_peak_bytes": 3400,
        "oom_events": 4,
        "oom_kills": 1,
        "process_limit_events": 0,
        "cpu_usage_usec": 1234,
        "cpu_throttle_events": 2,
        "cpu_throttled_usec": 55,
    }
    assert sample_v1({"memory": tmp_path, "cpuacct": tmp_path}) == {
        "memory_current_bytes": 400,
        "memory_kernel_peak_bytes": 800,
        "memory_limit_events": 12,
        "cpu_usage_usec": 7000,
    }
    assert sample_v2(tmp_path / "missing") == {}


def test_exit_classification_does_not_infer_oom_from_exit_code() -> None:
    assert classify(137, {"oom_killed": True}, {}) == "CONFIRMED_OOM"
    assert classify(137, {}, {"oom_kills": {"value": 1}}) == "CONFIRMED_OOM"
    assert classify(137, {}, {}) == "RESOURCE_OBSERVATION_UNAVAILABLE"
    assert classify(137, {"oom_killed": False}, {"oom_kills": {"value": 0}}) == "UNKNOWN_FAILURE"
    assert classify(124, {}, {}, timed_out=True) == "WALL_TIMEOUT"
    assert classify(0, {}, {}) == "COMPLETED"
    assert classify(2, {}, {}) == "TOOL_FAILURE"
    assert classify(2, {}, {"process_limit_events": {"value": 1}}) == "CPU_OR_PROCESS_LIMIT"
    assert classify(137, {}, {}, termination_signal=9) == "SIGNAL_TERMINATED"


def test_schema_high_water_and_partial_recovery(tmp_path: Path) -> None:
    path = tmp_path / "resource.ndjson"
    row = {
        "schema_version": 1,
        "observed_at": "2026-11-15T01:00:00+00:00",
        "elapsed_seconds": 0.0,
        "container_id": None,
        "measurements": {"memory_current_bytes": {"value": 4096, "source": "linux cgroup v2"}},
    }
    lower = {
        **row,
        "elapsed_seconds": 2.0,
        "measurements": {"memory_current_bytes": {"value": 512, "source": "linux cgroup v2"}},
    }
    path.write_text(encode(row) + encode(lower) + '{"schema', encoding="utf-8")
    peaks: dict[str, dict[str, object]] = {}
    for sample in read_samples(path, allow_partial=True):
        peaks = update_peaks(peaks, sample)
    assert peaks["memory_current_bytes"]["value"] == 4096
    assert "oom_kills" not in peaks
    with pytest.raises(ValueError, match="incomplete"):
        read_samples(path)
    path.write_text(encode({**row, "schema_version": 99}), encoding="utf-8")
    with pytest.raises(ValueError, match="schema"):
        read_samples(path, allow_partial=True)


@pytest.mark.skipif(os.getenv("AEDA_NATIVE_RESOURCE_TEST") != "1", reason="requires pinned Docker")
@pytest.mark.parametrize(
    ("suffix", "arguments", "limit", "expected"),
    [
        ("a", ["sleep", "3"], 10, "COMPLETED"),
        ("b", ["bash", "-c", "sleep 3; exit 137"], 10, "UNKNOWN_FAILURE"),
        ("c", ["sleep", "30"], 4, "WALL_TIMEOUT"),
        (
            "d",
            ["python3", "-c", "import time; time.sleep(3); b = bytearray(96 * 2**20)"],
            15,
            "CONFIRMED_OOM",
        ),
    ],
)
def test_native_docker_disposition(
    tmp_path: Path, suffix: str, arguments: list[str], limit: int, expected: str
) -> None:
    docker = shutil.which("docker")
    assert docker
    name = f"aeda-{suffix}{uuid.uuid4().hex[1:]}"
    log = tmp_path / "probe.log"
    context = ResourceContext(tmp_path, "native-probe", None, name, IMAGE, 1, 48 * 2**20, 32, 0)
    code, _elapsed = execute(
        container_command(docker, tmp_path, name, 1, 48, max_processes=32) + arguments,
        limit,
        log,
        name,
        resources=context,
    )
    result = json.loads(receipt_path(log).read_bytes())
    assert result["classification"] == expected
    assert result["exit_code"] == code
    assert result["cleanup_succeeded"]
    assert result["native_state"]["memory_bytes"] == 48 * 2**20
    assert result["observed_maxima"]["memory_kernel_peak_bytes"]["value"] > 0
    assert read_samples(samples_path(log))


@pytest.mark.skipif(os.getenv("AEDA_NATIVE_RESOURCE_TEST") != "1", reason="requires pinned Docker")
@pytest.mark.parametrize("signal", [15, 9])
def test_native_docker_interruption(tmp_path: Path, signal: int) -> None:
    docker = shutil.which("docker")
    assert docker
    name = f"aeda-{uuid.uuid4().hex}"
    log = tmp_path / "probe.log"
    child = """
import shutil
import signal
import sys
from pathlib import Path
from autonomous_eda.orfs import container_command, execute, terminate_on_signal
from autonomous_eda.reference import IMAGE
from autonomous_eda.telemetry import ResourceContext
root, name = Path(sys.argv[1]), sys.argv[2]
docker = shutil.which("docker")
signal.signal(signal.SIGTERM, terminate_on_signal)
context = ResourceContext(root, "native-interrupt", None, name, IMAGE, 1, 48 * 2**20, 32, 0)
execute(container_command(docker, root, name, 1, 48) + ["sleep", "30"],
        30, root / "probe.log", name, resources=context)
"""
    with subprocess.Popen(
        [sys.executable, "-c", child, str(tmp_path), name],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    ) as process:
        try:
            deadline = time.monotonic() + 12
            while time.monotonic() < deadline:
                if (
                    samples_path(log).is_file()
                    and samples_path(log).stat().st_size > 0
                    and docker_inspect(docker, name).get("status") == "running"
                ):
                    break
                time.sleep(0.10)
            else:
                pytest.fail("container never reached an observed running state")
            process.send_signal(signal)
            assert process.wait(timeout=15) != 0
            if signal == 9:
                assert docker_inspect(docker, name).get("status") == "running"
                record_interrupted_dispatch(docker, name, log)
                recovered = json.loads(log.with_suffix(".resources-recovery.json").read_bytes())
                assert recovered["native_state"]["status"] == "running"
                remove_interrupted_container(docker, name)
            else:
                result = json.loads(receipt_path(log).read_bytes())
                assert result["controller_signal"] == 15
                assert result["classification"] == "SIGNAL_TERMINATED"
                assert result["cleanup_succeeded"]
            assert not docker_inspect(docker, name)
            assert read_samples(samples_path(log))
        finally:
            if process.poll() is None:
                process.kill()
            remove_interrupted_container(docker, name)

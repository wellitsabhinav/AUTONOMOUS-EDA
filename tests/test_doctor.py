import json
import os
import platform
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from autonomous_eda import doctor
from autonomous_eda.cli import main
from autonomous_eda.doctor import Check, CommandResult, Report, Status
from autonomous_eda.reference import IMAGE


@pytest.mark.parametrize(
    ("status", "required", "overall", "exit_code"),
    [
        ("PASS", True, "PASS", 0),
        ("WARN", True, "WARN", 0),
        ("FAIL", True, "FAIL", 2),
        ("UNKNOWN", True, "UNKNOWN", 3),
        ("FAIL", False, "WARN", 0),
        ("UNKNOWN", False, "WARN", 0),
    ],
)
def test_cli_reports_status_and_exit_code(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    status: Status,
    required: bool,
    overall: Status,
    exit_code: int,
) -> None:
    report = Report("sky130hd", "/work", [Check("fixture", status, "detail", required, "remedy")])
    monkeypatch.setattr(doctor, "diagnose_environment", lambda *_args: report)
    assert main(["doctor", "--json"]) == exit_code
    result = json.loads(capsys.readouterr().out)
    assert result["schema_version"] == 1
    assert result["status"] == overall
    assert result["image"] == IMAGE
    assert result["checks"][0]["status"] == status
    assert main(["doctor"]) == exit_code
    assert f"Environment Doctor {overall}" in capsys.readouterr().out


def test_wsl_permission_denied_is_unknown(monkeypatch: pytest.MonkeyPatch) -> None:
    error = "Access is denied.\r\nError code: Wsl/E_ACCESSDENIED"
    monkeypatch.setattr(platform, "system", lambda: "Windows")
    monkeypatch.setattr(shutil, "which", lambda _name: "wsl.exe")
    monkeypatch.setattr(
        subprocess,
        "run",
        lambda *_args, **_kwargs: subprocess.CompletedProcess(
            [], 1, b"", error.encode("utf-16-le")
        ),
    )
    (check,) = doctor.wsl_checks("11")
    assert check.status == "UNKNOWN"
    assert error in check.detail
    assert check.remediation


def test_wsl_version_uses_registered_distributions(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(platform, "system", lambda: "Windows")
    monkeypatch.setattr(shutil, "which", lambda _name: "wsl.exe")
    listing = "  NAME           STATE         VERSION\n* Project Linux  Stopped       2\n"
    monkeypatch.setattr(
        doctor, "run_probe", lambda *_args, **_kwargs: CommandResult("PASS", listing)
    )
    assert doctor.wsl_checks("11")[0].detail == "Registered WSL2: Project Linux"


def test_path_access_denied_and_missing_are_distinct(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    assert doctor.path_check("fixture", tmp_path / "missing").status == "FAIL"

    def denied(_path: Path) -> None:
        raise PermissionError("access denied by fixture")

    monkeypatch.setattr(Path, "stat", denied)
    check = doctor.path_check("fixture", tmp_path)
    assert check.status == "UNKNOWN"
    assert "access denied by fixture" in check.detail
    assert check.remediation


def test_timeout_is_unknown(monkeypatch: pytest.MonkeyPatch) -> None:
    def timeout(args: list[str], **_kwargs: object) -> None:
        raise subprocess.TimeoutExpired(args, 5)

    monkeypatch.setattr(subprocess, "run", timeout)
    result = doctor.run_probe(["fixture"])
    assert result.status == "UNKNOWN"
    assert "timed out after 5 seconds" in result.detail


def test_remote_docker_context_is_not_contacted(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.delenv("DOCKER_CONTEXT", raising=False)
    monkeypatch.setenv("DOCKER_HOST", "ssh://remote")
    monkeypatch.setattr(shutil, "which", lambda _name: "docker")

    def probe(args: list[str]) -> CommandResult:
        assert args == ["docker", "--version"]
        return CommandResult("PASS", "Docker fixture")

    monkeypatch.setattr(doctor, "run_probe", probe)
    checks = {check.id: check for check in doctor.engine_checks("docker", tmp_path, "sky130hd")}
    assert checks["engine.docker.context"].status == "UNKNOWN"
    assert checks["backend.engine"].status == "UNKNOWN"


@pytest.mark.parametrize(
    ("observed", "expected"),
    [
        (CommandResult("FAIL", detail="No such image"), "FAIL"),
        (CommandResult("UNKNOWN", detail="Access denied"), "UNKNOWN"),
        (CommandResult("PASS", "{}"), "UNKNOWN"),
        (
            CommandResult(
                "PASS",
                json.dumps([{"RepoDigests": [IMAGE], "Os": "linux", "Architecture": "arm64"}]),
            ),
            "FAIL",
        ),
        (
            CommandResult(
                "PASS", json.dumps([{"RepoDigests": [], "Os": "linux", "Architecture": "amd64"}])
            ),
            "FAIL",
        ),
    ],
)
def test_unverified_image_never_starts_container(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    observed: CommandResult,
    expected: Status,
) -> None:
    def probe(args: list[str], timeout: int) -> CommandResult:
        assert timeout == 10
        assert args == ["docker", "image", "inspect", IMAGE]
        return observed

    monkeypatch.setattr(doctor, "run_probe", probe)
    checks = {
        c.id: c.status for c in doctor.backend_checks("docker", "docker", tmp_path, "sky130hd")
    }
    assert checks["backend.image"] == expected
    assert checks["backend.mount"] == "UNKNOWN"


@pytest.mark.parametrize("engine", ["docker", "podman"])
def test_only_runs_bounded_read_only_container(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    engine: str,
) -> None:
    prefix = [engine] if engine == "docker" else [engine, "--remote=false"]
    report = {
        check: ["PASS", "observed"]
        for check in (
            "mount",
            "makefile",
            "platform",
            "cpu",
            "memory",
            "disk",
            *(f"tool.{name}" for name in doctor.TOOLS),
        )
    }
    report["tool.openroad"] = ["WARN", "unknown version"]

    def probe(args: list[str], timeout: int) -> CommandResult:
        if args == [*prefix, "image", "inspect", IMAGE]:
            return CommandResult(
                "PASS",
                json.dumps([{"RepoDigests": [IMAGE], "Os": "linux", "Architecture": "amd64"}]),
            )
        assert args[: len(prefix) + 1] == [*prefix, "run"]
        assert {"--read-only", "--pull=never", "--network=none", "--rm", IMAGE} <= set(args)
        assert f"type=bind,source={tmp_path},target=/work,readonly" in args
        assert timeout == 30
        return CommandResult("PASS", json.dumps(report))

    monkeypatch.setattr(doctor, "run_probe", probe)
    result = doctor.backend_checks(engine, engine, tmp_path, "sky130hd")
    assert Report("sky130hd", str(tmp_path), result).status == "WARN"
    assert {check.id: check.status for check in result}["backend.mount"] == "PASS"


def test_module_cli_invalid_platform_is_a_usage_error() -> None:
    result = subprocess.run(
        [sys.executable, "-m", "autonomous_eda", "doctor", "--platform", "../invalid"],
        capture_output=True,
        text=True,
        timeout=10,
        check=False,
    )
    assert result.returncode == 2
    assert "invalid choice" in result.stderr


@pytest.mark.parametrize("platform_name", ["sky130hd", "nangate45"])
def test_cached_orfs_image_integration(platform_name: str) -> None:
    workspace = os.environ.get("AEDA_TEST_ORFS_WORKSPACE")
    if not workspace:
        pytest.skip("AEDA_TEST_ORFS_WORKSPACE requires a cached pinned ORFS image and local engine")
    engine = os.environ.get("AEDA_TEST_ORFS_ENGINE", "docker")
    distro = os.environ.get("AEDA_TEST_WSL_DISTRO")
    if distro:
        source = f"{workspace.rstrip('/')}/src"
        command = [
            "wsl.exe",
            "-d",
            distro,
            "--exec",
            "env",
            "PYTHONDONTWRITEBYTECODE=1",
            f"PYTHONPATH={source}",
            "python3",
        ]
    else:
        command = [sys.executable]
    result = doctor.run_probe(
        [
            *command,
            "-m",
            "autonomous_eda",
            "doctor",
            "--json",
            "--engine",
            engine,
            "--workspace",
            workspace,
            "--platform",
            platform_name,
        ],
        timeout=75,
    )
    assert result.stdout, result.detail or result.stderr
    report = json.loads(result.stdout)
    assert result.status == "PASS", report
    assert report["status"] in {"PASS", "WARN"}
    assert report["image"] == IMAGE
    checks = {check["id"]: check for check in report["checks"]}
    for check_id in (
        "backend.image",
        "backend.mount",
        "backend.makefile",
        "backend.platform",
        "backend.tool.make",
        "backend.tool.yosys",
        "backend.tool.klayout",
    ):
        assert checks[check_id]["status"] == "PASS", checks[check_id]
    assert checks["backend.tool.openroad"]["status"] in {"PASS", "WARN"}

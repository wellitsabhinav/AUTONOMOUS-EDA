"""Inspect the active host and locally cached ORFS backend without running a flow."""

from __future__ import annotations

import argparse
import ctypes
import json
import os
import platform
import re
import shutil
import signal
import stat
import subprocess
import sys
import uuid
from collections.abc import Iterable
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Literal

from .reference import IMAGE, ORFS_ROOT, REVISION

Status = Literal["PASS", "FAIL", "WARN", "UNKNOWN"]
ToolEngine = Literal["auto", "docker", "podman"]
TOOLS = {
    "make": ("--version",),
    "yosys": ("-V",),
    "openroad": ("-version",),
    "sta": ("-version",),
    "klayout": ("-v",),
}
ORFS_TOOL_FOLDERS = {"yosys": "yosys", "openroad": "OpenROAD", "sta": "OpenROAD"}
PERMISSION = re.compile(
    r"access (?:is )?denied|permission denied|e_accessdenied|operation not permitted|"
    r"0x80070005|unauthorized",
    re.IGNORECASE,
)
MISSING_IMAGE = re.compile(r"no such image|image not known|image not found", re.IGNORECASE)
ENGINE_UNAVAILABLE = re.compile(
    r"cannot connect .*?(?:daemon|socket)|connection refused|no such file or directory",
    re.IGNORECASE,
)
SECRET_KEY = re.compile(
    r"token|pass(?:word|wd|phrase)|secret|credential|auth|(?:api|access|private)[_-]?key", re.I
)
SECRET_FIELD = re.compile(
    r"(?im)(\b[\w-]*(?:token|password|passwd|secret|credential|authorization|api[_-]?key)"
    r"[\w-]*[\"']?\s*[:=]\s*)[^\r\n]+"
)
URL_AUTH = re.compile(r"(\b[a-z][a-z0-9+.-]*://)[^/\s@]+@", re.I)


@dataclass(frozen=True)
class Check:
    id: str
    status: Status
    detail: str
    required: bool = True
    remediation: str | None = None


@dataclass(frozen=True)
class CommandResult:
    status: Status
    stdout: str = ""
    detail: str = ""
    stderr: str = ""


@dataclass(frozen=True)
class Report:
    platform: str
    workspace: str
    checks: list[Check]
    schema_version: int = 1
    image: str = IMAGE
    orfs_revision: str = REVISION

    @property
    def status(self) -> Status:
        if any(check.required and check.status == "FAIL" for check in self.checks):
            return "FAIL"
        if any(check.required and check.status == "UNKNOWN" for check in self.checks):
            return "UNKNOWN"
        return "WARN" if any(check.status != "PASS" for check in self.checks) else "PASS"


def output_text(value: bytes) -> str:
    if value.startswith((b"\xff\xfe", b"\xfe\xff")):
        return value.decode("utf-16", errors="replace").strip()
    if len(value) % 2 == 0 and value[1:100:2].count(0) >= 3:
        return value.decode("utf-16-le", errors="replace").strip()
    return value.decode("utf-8", errors="replace").strip()


def redact_diagnostics(value: str, secrets: Iterable[str]) -> str:
    for secret in secrets:
        value = value.replace(secret, "[redacted]")
    value = URL_AUTH.sub(r"\1[redacted]@", value)
    return SECRET_FIELD.sub(r"\1[redacted]", value)


def environment_secrets() -> list[str]:
    return sorted(
        {value for name, value in os.environ.items() if value and SECRET_KEY.search(name)},
        key=len,
        reverse=True,
    )


def run_probe(arguments: list[str], timeout: int = 5) -> CommandResult:
    try:
        result = subprocess.run(arguments, capture_output=True, timeout=timeout, check=False)
    except subprocess.TimeoutExpired as error:
        return CommandResult("UNKNOWN", detail=f"Probe timed out after {error.timeout} seconds")
    except OSError as error:
        return CommandResult("UNKNOWN", detail=str(error))
    stdout = output_text(result.stdout)
    stderr = output_text(result.stderr)
    if result.returncode == 0:
        return CommandResult("PASS", stdout, stderr=stderr)
    output = "\n".join(part for part in (stderr, stdout) if part)
    safe_output = redact_diagnostics(output, environment_secrets())[:1200]
    detail = f"exit {result.returncode}: {safe_output or 'no output'}"
    return CommandResult("UNKNOWN" if PERMISSION.search(output) else "FAIL", stdout, detail, stderr)


def wsl_checks(release: str) -> list[Check]:
    system = platform.system()
    if system == "Linux":
        if "wsl2" in release.lower():
            return [Check("host.wsl", "PASS", f"WSL2 kernel {release}")]
        if "microsoft" in release.lower():
            return [
                Check("host.wsl", "UNKNOWN", f"WSL version unclear from kernel {release}", False)
            ]
        return [Check("host.wsl", "PASS", "Native Linux or Linux VM")]
    if system != "Windows":
        return [Check("host.wsl", "FAIL", f"Unsupported execution host {system}")]

    remediation = "Run wsl.exe --list --verbose from the owning Windows account."
    executable = shutil.which("wsl.exe")
    if executable is None:
        return [Check("host.wsl", "FAIL", "wsl.exe not found", remediation=remediation)]
    result = run_probe([executable, "--list", "--verbose"], timeout=10)
    if result.status != "PASS":
        no_distro = "no installed distributions" in result.detail.lower()
        status: Status = "FAIL" if no_distro else "UNKNOWN"
        return [Check("host.wsl", status, result.detail, remediation=remediation)]
    distros = re.findall(r"^\s*\*?\s*(.+?)\s{2,}\S.*?\s{2,}([12])\s*$", result.stdout, re.M)
    wsl2 = [name for name, version in distros if version == "2"]
    if wsl2:
        return [Check("host.wsl", "PASS", f"Registered WSL2: {', '.join(wsl2)}")]
    if distros:
        return [
            Check(
                "host.wsl",
                "FAIL",
                "Only WSL1 distributions are registered",
                remediation="Configure a WSL2 Linux distribution.",
            )
        ]
    return [
        Check(
            "host.wsl",
            "UNKNOWN",
            "Could not interpret WSL distribution listing",
            remediation=remediation,
        )
    ]


def linux_distribution() -> Check:
    try:
        release = platform.freedesktop_os_release()
        return Check("host.distribution", "PASS", release.get("PRETTY_NAME") or release["ID"])
    except (OSError, KeyError) as error:
        return Check(
            "host.distribution",
            "UNKNOWN",
            str(error),
            remediation="Check access to /etc/os-release or /usr/lib/os-release.",
        )


def available_cpus() -> int | None:
    if sys.platform == "linux":
        return len(os.sched_getaffinity(0))
    return os.cpu_count()


def memory_bytes() -> tuple[int, int]:
    if sys.platform == "win32":

        class MemoryStatus(ctypes.Structure):
            _fields_ = [
                ("length", ctypes.c_uint32),
                ("load", ctypes.c_uint32),
                ("total", ctypes.c_uint64),
                ("available", ctypes.c_uint64),
                ("other", ctypes.c_uint64 * 5),
            ]

        status = MemoryStatus()
        status.length = ctypes.sizeof(status)
        if not ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(status)):
            raise ctypes.WinError()
        return status.total, status.available

    fields = dict(
        re.findall(
            r"^(MemTotal|MemAvailable):\s+(\d+)\s+kB",
            Path("/proc/meminfo").read_text(encoding="ascii"),
            re.M,
        )
    )
    return int(fields["MemTotal"]) * 1024, int(fields["MemAvailable"]) * 1024


def host_resources(workspace: Path) -> list[Check]:
    checks = []
    try:
        cpu = available_cpus()
        checks.append(
            Check(
                "resources.cpu",
                "PASS" if cpu else "UNKNOWN",
                f"{cpu} visible logical CPUs" if cpu else "CPU count unavailable",
            )
        )
    except OSError as error:
        checks.append(
            Check(
                "resources.cpu",
                "UNKNOWN",
                str(error),
                remediation="Check access to the active CPU affinity mask.",
            )
        )
    try:
        total, available = memory_bytes()
        checks.append(
            Check(
                "resources.memory",
                "PASS",
                f"{available} bytes available out of {total} visible bytes",
            )
        )
    except (OSError, KeyError, ValueError) as error:
        checks.append(
            Check(
                "resources.memory",
                "UNKNOWN",
                str(error),
                remediation="Check access to host memory counters.",
            )
        )
    try:
        free = shutil.disk_usage(workspace).free
        checks.append(
            Check("resources.disk", "PASS" if free else "FAIL", f"{free} free bytes on {workspace}")
        )
    except FileNotFoundError as error:
        checks.append(Check("resources.disk", "UNKNOWN", str(error)))
    except OSError as error:
        checks.append(
            Check(
                "resources.disk",
                "UNKNOWN",
                str(error),
                remediation="Check permission to inspect the workspace volume.",
            )
        )
    return checks


def path_check(check_id: str, path: Path, *, directory: bool = False) -> Check:
    remedy = f"Check the location and access permissions of {path}."
    try:
        mode = path.stat().st_mode
        correct_type = stat.S_ISDIR(mode) if directory else stat.S_ISREG(mode)
        if not correct_type:
            return Check(
                check_id, "FAIL", f"Unexpected filesystem object at {path}", remediation=remedy
            )
        if directory:
            with os.scandir(path):
                pass
        else:
            with path.open("rb"):
                pass
    except FileNotFoundError:
        return Check(check_id, "FAIL", f"Path missing: {path}", remediation=remedy)
    except OSError as error:
        return Check(check_id, "UNKNOWN", str(error), remediation=remedy)
    return Check(check_id, "PASS", str(path))


def host_tools() -> list[Check]:
    checks = []
    for name, args in TOOLS.items():
        path = shutil.which(name)
        if path is None:
            checks.append(Check(f"host.tool.{name}", "WARN", "Executable not on host PATH", False))
            continue
        result = run_probe([path, *args])
        version = (result.stdout or result.stderr).splitlines()
        status: Status = result.status if version or result.status != "PASS" else "UNKNOWN"
        detail = (
            result.detail or f"{path}: {version[0] if version else 'version output unavailable'}"
        )
        checks.append(
            Check(
                f"host.tool.{name}",
                status,
                detail,
                False,
                f"Check whether {path} can run and report its version.",
            )
        )
    return checks


CONTAINER_PROBE = """
import json
import os
import shutil
import signal
import subprocess
import sys
from pathlib import Path

root, platform, tool_flags, install_folders = sys.argv[1:]
signal.alarm(25)
install_folders = json.loads(install_folders)
checks = {}
for name, path, directory in (
    ('mount', '/work', True),
    ('makefile', root + '/flow/Makefile', False),
    ('platform', root + '/flow/platforms/' + platform + '/config.mk', False),
):
    try:
        with (os.scandir(path) if directory else open(path, 'rb')):
            pass
    except FileNotFoundError as error:
        checks[name] = ['FAIL', str(error)]
    except OSError as error:
        checks[name] = ['UNKNOWN', str(error)]
    else:
        checks[name] = ['PASS', path]
for name, flags in json.loads(tool_flags).items():
    path = shutil.which(name)
    if name in install_folders:
        installed = Path(root, 'tools', 'install', install_folders[name], 'bin', name)
        if installed.is_file() and os.access(installed, os.X_OK):
            path = str(installed)
    if path is None:
        checks['tool.' + name] = ['FAIL', 'Executable not on image PATH']
        continue
    try:
        result = subprocess.run([path, *flags], capture_output=True, text=True,
                                errors='replace', timeout=4, check=False)
        output = (result.stdout or result.stderr).strip()
        status = 'FAIL' if result.returncode else 'PASS' if output else 'UNKNOWN'
        if status == 'PASS' and output.lower() == 'unknown':
            status = 'WARN'
        detail = output.splitlines()[0][:300] if output else 'No version output'
        checks['tool.' + name] = [status, f'{path}: {detail} (exit {result.returncode})']
    except (OSError, subprocess.TimeoutExpired) as error:
        checks['tool.' + name] = ['UNKNOWN', str(error)]
try:
    checks['cpu'] = ['PASS', f'{len(os.sched_getaffinity(0))} visible logical CPUs']
    mem = Path('/proc/meminfo').read_text().splitlines()
    values = [line for line in mem if line.startswith(('MemAvailable:', 'MemTotal:'))]
    checks['memory'] = ['PASS' if len(values) == 2 else 'UNKNOWN', '; '.join(values)]
    checks['disk'] = ['PASS', f'{shutil.disk_usage("/work").free} free bytes at /work']
except OSError as error:
    for check in ('cpu', 'memory', 'disk'):
        checks.setdefault(check, ['UNKNOWN', str(error)])
print(json.dumps(checks))
"""


def engine_checks(engine: ToolEngine, workspace: Path, platform_name: str) -> list[Check]:
    names = ("docker", "podman") if engine == "auto" else (engine,)
    engines = [(name, shutil.which(name)) for name in names]
    checks = []
    for name, path in engines:
        if path is None:
            checks.append(Check(f"engine.{name}", "WARN", "Executable not on PATH", False))
            continue
        version = run_probe([path, "--version"])
        checks.append(
            Check(
                f"engine.{name}",
                version.status,
                version.detail or version.stdout or version.stderr,
                False,
                "Check that the engine client can report its version.",
            )
        )

    ready: list[tuple[str, str]] = []
    failures: list[Status] = []
    for name, path in engines:
        if path is None:
            continue
        if name == "podman" and platform.system() != "Linux":
            checks.append(
                Check(
                    "engine.podman.server",
                    "UNKNOWN",
                    "Run Doctor inside the Linux host for Podman probing",
                    False,
                )
            )
            failures.append("UNKNOWN")
            continue
        if name == "docker":
            endpoint = (
                os.environ.get("DOCKER_HOST") if not os.environ.get("DOCKER_CONTEXT") else None
            )
            if endpoint:
                context = CommandResult("PASS", endpoint)
            else:
                context = run_probe(
                    [path, "context", "inspect", "--format", "{{.Endpoints.docker.Host}}"]
                )
            if context.status != "PASS" or not context.stdout.startswith(("unix://", "npipe://")):
                checks.append(
                    Check(
                        "engine.docker.context",
                        "UNKNOWN",
                        context.detail or "Active Docker context is not a local socket",
                        False,
                        "Select a local Docker context for offline diagnostics.",
                    )
                )
                failures.append("UNKNOWN")
                continue
        prefix = [path] if name == "docker" else [path, "--remote=false"]
        field = "{{.OSType}}" if name == "docker" else "{{.Host.OS}}"
        result = run_probe([*prefix, "info", "--format", field])
        if result.status == "PASS":
            status: Status = "PASS" if result.stdout == "linux" else "FAIL"
            detail = f"{name} server OS: {result.stdout or 'no OS reported'}"
            if not result.stdout:
                status = "UNKNOWN"
        else:
            status = (
                "FAIL"
                if (result.status == "FAIL" and ENGINE_UNAVAILABLE.search(result.detail))
                else "UNKNOWN"
            )
            detail = result.detail
        checks.append(
            Check(
                f"engine.{name}.server",
                status,
                detail,
                False,
                "Check access to the local Linux container engine or run inside WSL2.",
            )
        )
        if status == "PASS":
            ready.append((name, path))
        else:
            failures.append(status)

    if not ready:
        status = "UNKNOWN" if platform.system() == "Windows" or "UNKNOWN" in failures else "FAIL"
        checks.append(
            Check(
                "backend.engine",
                status,
                "No accessible Linux container engine",
                remediation="Run Doctor inside the project WSL2 distribution or on Linux "
                "with Docker or Podman available.",
            )
        )
        checks.extend(unavailable_backend("Engine access is required for this check."))
        return checks

    outcomes = []
    for name, path in ready:
        outcome = backend_checks(name, path, workspace, platform_name)
        if not any(item.required and item.status in {"FAIL", "UNKNOWN"} for item in outcome):
            return checks + outcome
        outcomes.append(outcome)
    # Prefer an engine with a verified image over an engine with an unverified image.
    return checks + next((items for items in outcomes if items[1].status == "PASS"), outcomes[0])


def unavailable_backend(reason: str, *, image: bool = True) -> list[Check]:
    ids = [
        "backend.container",
        "backend.mount",
        "backend.makefile",
        "backend.platform",
        *[f"backend.tool.{name}" for name in TOOLS],
        "backend.cpu",
        "backend.memory",
        "backend.disk",
    ]
    if image:
        ids.insert(0, "backend.image")
    return [Check(check_id, "UNKNOWN", reason, check_id != "backend.tool.sta") for check_id in ids]


def probe_container(prefix: list[str], options: list[str]) -> CommandResult:
    name = f"aeda-doctor-{uuid.uuid4().hex}"
    result: CommandResult
    cleanup: CommandResult
    try:
        # Register a known identity before starting the attached process.
        result = run_probe([*prefix, "create", "--rm", "--name", name, *options], timeout=10)
        if result.status == "PASS":
            result = run_probe([*prefix, "start", "--attach", name], timeout=30)
    finally:
        cleanup = run_probe([*prefix, "rm", "--force", name], timeout=5)
    if cleanup.status != "PASS" and not re.search(
        r"no such container|no container with", cleanup.detail, re.I
    ):
        return CommandResult(
            "UNKNOWN", detail=f"{result.detail} Cleanup of {name} failed: {cleanup.detail}".strip()
        )
    return result


def backend_checks(name: str, executable: str, workspace: Path, platform_name: str) -> list[Check]:
    checks = [Check("backend.engine", "PASS", name)]
    prefix = [executable] if name == "docker" else [executable, "--remote=false"]
    result = run_probe([*prefix, "image", "inspect", IMAGE], timeout=10)
    remedy = f"Make the pinned image available in the selected engine's local cache: {IMAGE}"
    status: Status = "UNKNOWN"
    detail = result.detail
    if result.status == "PASS":
        try:
            image = json.loads(result.stdout)[0]
            digests = image["RepoDigests"] or []
            digest_matches = any(item.endswith("@" + IMAGE.split("@", 1)[1]) for item in digests)
            architecture, os_name = image["Architecture"], image["Os"]
            status = (
                "PASS"
                if digest_matches and (architecture, os_name) == ("amd64", "linux")
                else "FAIL"
            )
            detail = (
                f"{os_name}/{architecture}, digest {'matches' if digest_matches else 'differs'}"
            )
        except (ValueError, TypeError, AttributeError, IndexError, KeyError) as error:
            detail = f"Could not read image identity: {error}"
    elif result.status == "FAIL" and MISSING_IMAGE.search(result.detail):
        status = "FAIL"
    checks.append(Check("backend.image", status, detail, remediation=remedy))
    if status != "PASS":
        return checks + unavailable_backend("The pinned image must be verified first.", image=False)

    workspace_check = path_check("backend.mount", workspace, directory=True)
    if workspace_check.status != "PASS" or any(char in str(workspace) for char in ',"\r\n'):
        unavailable = unavailable_backend("A usable workspace path is required.", image=False)
        if workspace_check.status == "PASS":
            workspace_check = Check(
                "backend.mount",
                "FAIL",
                "Workspace contains an unsupported bind-mount delimiter",
                remediation="Choose a path without commas, quotes or line breaks.",
            )
        return checks + [
            workspace_check,
            *(item for item in unavailable if item.id != "backend.mount"),
        ]

    user: list[str] = []
    if sys.platform != "win32":
        user = ["--user", f"{os.getuid()}:{os.getgid()}"]
    result = probe_container(
        prefix,
        [
            "--pull=never",
            "--network=none",
            "--read-only",
            "--entrypoint",
            "python3",
            *user,
            "--env",
            "HOME=/tmp",
            "--mount",
            f"type=bind,source={workspace},target=/work,readonly",
            IMAGE,
            "-B",
            "-c",
            CONTAINER_PROBE,
            ORFS_ROOT,
            platform_name,
            json.dumps(TOOLS),
            json.dumps(ORFS_TOOL_FOLDERS),
        ],
    )
    if result.status != "PASS":
        return checks + [
            Check(
                "backend.container",
                "UNKNOWN",
                result.detail,
                remediation="Check runtime access, the bind path, and local image compatibility.",
            ),
            *unavailable_backend("The container probe did not complete.", image=False)[1:],
        ]

    remediation = "Check the pinned image and read access to its platform and workspace paths."
    try:
        data = json.loads(result.stdout)
        observed = []
        for check in unavailable_backend("", image=False)[1:]:
            key = check.id.removeprefix("backend.")
            observed_status, observed_detail = data[key]
            if observed_status not in {"PASS", "WARN", "FAIL", "UNKNOWN"} or not isinstance(
                observed_detail, str
            ):
                raise ValueError(f"Invalid result for {key}")
            if observed_status == "FAIL" and PERMISSION.search(observed_detail):
                observed_status = "UNKNOWN"
            if key == "tool.sta" and observed_status == "FAIL":
                observed_status = "WARN"
            observed.append(
                Check(check.id, observed_status, observed_detail, check.required, remediation)
            )
    except (ValueError, TypeError, KeyError) as error:
        return (
            checks
            + [
                Check(
                    "backend.container",
                    "UNKNOWN",
                    f"Invalid probe output: {error}",
                    remediation=remediation,
                )
            ]
            + unavailable_backend("Container output could not be interpreted.", image=False)[1:]
        )
    return (
        checks + [Check("backend.container", "PASS", "Local read-only probe completed")] + observed
    )


def diagnose_environment(
    workspace: Path, orfs_root: Path | None, platform_name: str, engine: ToolEngine
) -> Report:
    system = platform.system()
    release = platform.release()
    workspace = workspace.absolute()
    version = sys.version_info[:2]
    checks = [
        Check("host.kernel", "PASS", f"{system} {release} ({platform.machine()})"),
        Check(
            "host.python",
            "PASS" if version >= (3, 11) else "FAIL",
            f"Python {platform.python_version()} at {sys.executable}",
            remediation="Run with Python 3.11 or newer.",
        ),
    ]
    checks.extend(wsl_checks(release))
    if system == "Linux":
        checks.append(linux_distribution())
    else:
        checks.append(
            Check(
                "host.distribution",
                "UNKNOWN",
                "Linux guest was not queried",
                required=False,
                remediation="Run Doctor inside the target Linux guest.",
            )
        )
    checks.append(path_check("host.workspace", workspace, directory=True))
    checks.extend(host_resources(workspace))
    if orfs_root is not None:
        config = orfs_root / "flow" / "platforms" / platform_name / "config.mk"
        checks.append(path_check("orfs.makefile", orfs_root / "flow" / "Makefile"))
        checks.append(path_check("orfs.platform", config))
        git = shutil.which("git")
        result = (
            run_probe([git, "-C", str(orfs_root), "rev-parse", "HEAD"])
            if git
            else (CommandResult("UNKNOWN", detail="Git executable not on PATH"))
        )
        status: Status = result.status
        detail = result.detail
        if status == "PASS":
            status = "PASS" if result.stdout == REVISION else "FAIL"
            detail = f"Checkout {result.stdout}, expected {REVISION}"
        elif status == "FAIL":
            status = "UNKNOWN"
        checks.append(
            Check(
                "orfs.revision",
                status,
                detail,
                remediation="Use the reference ORFS checkout at the documented revision.",
            )
        )
    checks.extend(host_tools())
    checks.extend(engine_checks(engine, workspace, platform_name))
    return Report(platform_name, str(workspace), checks)


def configure_doctor(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--json", action="store_true", help="emit a versioned JSON report")
    parser.add_argument("--workspace", type=Path, default=Path.cwd(), help="existing run directory")
    parser.add_argument("--orfs-root", type=Path, help="also check this local ORFS checkout")
    parser.add_argument("--platform", default="sky130hd", choices=("sky130hd", "nangate45"))
    parser.add_argument("--engine", default="auto", choices=("auto", "docker", "podman"))


def doctor_main(args: argparse.Namespace) -> int:
    def stop(_signum: int, _frame: object) -> None:
        raise KeyboardInterrupt

    previous = signal.signal(signal.SIGTERM, stop)
    try:
        report = diagnose_environment(args.workspace, args.orfs_root, args.platform, args.engine)
    except KeyboardInterrupt:
        print("Environment Doctor interrupted", file=sys.stderr)
        return 130
    finally:
        signal.signal(signal.SIGTERM, previous)
    secrets = environment_secrets()
    if args.json:
        data = asdict(report)
        data["workspace"] = redact_diagnostics(report.workspace, secrets)
        for check in data["checks"]:
            for field in ("detail", "remediation"):
                if check[field] is not None:
                    check[field] = redact_diagnostics(check[field], secrets)
        print(json.dumps({**data, "status": report.status}, indent=2))
    else:
        print(f"Environment Doctor {report.status}")
        for check in report.checks:
            print(f"{check.status:7} {check.id}  {redact_diagnostics(check.detail, secrets)}")
            if check.remediation and check.status != "PASS":
                print(f"        {redact_diagnostics(check.remediation, secrets)}")
    return {"PASS": 0, "WARN": 0, "FAIL": 2, "UNKNOWN": 3}[report.status]

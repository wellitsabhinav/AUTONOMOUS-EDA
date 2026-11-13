# Environment Doctor

Install the package with Python 3.11 or newer and inspect the environment that will run ORFS.

```sh
python -m pip install -e .
autonomous-eda doctor
autonomous-eda doctor --json --engine docker --workspace /path/to/existing/run/directory
```

On Windows, Doctor checks registered WSL distributions from the host. Run it again inside the target WSL2 distribution to check its distribution, kernel and Linux Docker or Podman engine. The `--workspace` path defaults to the current directory. `--platform` defaults to `sky130hd` and can select `nangate45`. `--orfs-root` optionally checks a local ORFS checkout for required files and the expected Git revision.

Doctor reports Python, kernel, distribution, host CPU and memory visibility, workspace free disk, optional host tools, and local engine access. In automatic mode it checks installed engines and prefers Docker when both can verify the backend. It checks the reference digest and Linux/amd64 image identity before running bounded version and file probes. It uses an ephemeral container, a read-only filesystem and workspace bind mount, and disables pulling and container networking. The bind probe verifies read access. Resource counters show visibility at the time of inspection. The controller must enforce each experiment's actual resource limits and verify write access when it starts a run. Local Docker sockets and native Linux/WSL2 Podman are supported probe paths. [Docker contexts](https://docs.docker.com/reference/cli/docker/context/inspect/) · [Podman mounts](https://docs.podman.io/en/stable/markdown/podman-run.1.html)

| Status | Meaning | Overall exit code |
| --- | --- | ---: |
| PASS | The selected check ran and verified its condition. | 0 |
| WARN | The check found a degraded or optional condition, such as a runnable binary reporting an unknown version. | 0 |
| FAIL | The check verified a problem, such as a missing local pinned image or wrong architecture. | 2 |
| UNKNOWN | The check could not verify its condition. Examples include a permission denial, timeout, or missing prerequisite. | 3 |

An observed required failure takes precedence, followed by an unknown required check and then warnings or optional problems. JSON includes a schema version, expected ORFS revision and image, selected platform and workspace, overall status, and individual check IDs, status, required flag, detail, and remediation where applicable. Diagnostic output redacts recognized credential fields, URL credentials, and values of credential-like environment variables. Check details can still contain local paths and tool errors. Doctor removes its named probe container after completion, timeout, or catchable interruption. It reports blocked cleanup when it can still return a report. Hard termination such as SIGKILL prevents host-side cleanup, and the active in-container probe has its own alarm.

## Reproducing validation

```sh
ruff check .
ruff format --check .
python -m mypy
python -m pytest -q
autonomous-eda --version
python -m autonomous_eda doctor --help
```

The automated suite uses lightweight fixtures by default. To exercise a cached reference image, set `AEDA_TEST_ORFS_WORKSPACE` to an existing directory in the target Linux environment and run `python -m pytest -q -k cached_orfs_image_integration`. Set `AEDA_TEST_ORFS_ENGINE=podman` to select native Podman. From Windows, set `AEDA_TEST_WSL_DISTRO` to the registered distribution and set the workspace to the WSL path of this checkout so its `src` directory is available to the guest process.

The WSL2 integration suite passed both Sky130HD and Nangate45 cases on Ubuntu 24.04.5 LTS, kernel `6.18.33.2-microsoft-standard-WSL2`, Python 3.12.3 and Docker 29.1.3. It verified the image digest, workspace bind, platform configs, GNU Make 4.4.1, Yosys 0.68+post, OpenROAD executable access, and KLayout 0.30.12. Doctor returned WARN because the image's OpenROAD version string is `unknown`, standalone OpenSTA was absent, and optional host tools were missing. WSL startup also reported a missing swap path. The restricted Windows run returned UNKNOWN and included `Wsl/E_ACCESSDENIED`. Live Podman remains unverified.

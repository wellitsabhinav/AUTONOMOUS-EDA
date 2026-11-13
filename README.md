# AUTONOMOUS-EDA

AUTONOMOUS-EDA is building a deterministic Python controller for reproducible RTL-to-GDSII experiments using OpenROAD Flow Scripts (ORFS). The current CLI provides Environment Doctor. The local controller prototypes use GCD and Ibex on SkyWater 130 HD. The long-term benchmark is OpenTitan Earl Grey 2.

With Python 3.11 or newer, run `python -m pip install -e .`, then `autonomous-eda doctor`. See the [Environment Doctor guide](docs/ENVIRONMENT_DOCTOR.md) for JSON output, exit codes, and the Windows/WSL2 workflow.

See the [architecture](docs/ARCHITECTURE.md), [roadmap](docs/ROADMAP.md), [project state](docs/PROJECT_STATE.md), and [experiment protocol](docs/EXPERIMENT_PROTOCOL.md).

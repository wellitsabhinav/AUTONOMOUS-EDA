# AUTONOMOUS-EDA

AUTONOMOUS-EDA provides a deterministic Python controller for reproducible RTL-to-GDSII experiments using OpenROAD Flow Scripts (ORFS). It includes Environment Doctor, ordered RTL inspection, staged physical implementation, checkpoint recovery, and artifact-linked evidence. The built-in configurations target GCD and Ibex on SkyWater 130 HD. The long-term benchmark is OpenTitan Earl Grey 2.

With Python 3.11 or newer, run `python -m pip install -e .`, then `autonomous-eda doctor`. See the [Environment Doctor guide](docs/ENVIRONMENT_DOCTOR.md) for JSON output, exit codes, and the Windows/WSL2 workflow.

```sh
autonomous-eda inspect examples/counter.toml
autonomous-eda plan configs/gcd-sky130hd.toml
autonomous-eda run configs/gcd-sky130hd.toml experiments/gcd-run --memory-mib 1200
autonomous-eda verify experiments/gcd-run
autonomous-eda resume experiments/gcd-run
autonomous-eda report experiments/gcd-run
```

RTL inspection uses a local Yosys executable. Physical execution and deck verification use the locally cached pinned image with Docker on Linux or WSL2. A successful `run` or `resume` means the stage plan completed. `verify` reports platform DRC and LVS. Read the independent gates and acceptance verdict in the [evidence contract](docs/EVIDENCE_CONTRACT.md).

See the [architecture](docs/ARCHITECTURE.md), [roadmap](docs/ROADMAP.md), [project state](docs/PROJECT_STATE.md), and [experiment protocol](docs/EXPERIMENT_PROTOCOL.md).

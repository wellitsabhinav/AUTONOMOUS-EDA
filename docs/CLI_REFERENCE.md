# Controller CLI reference

Run `autonomous-eda COMMAND --help` for current arguments and defaults. The same entry point is available as `python -m autonomous_eda`. Commands use paths relative to the current shell directory unless the relevant guide says a configuration file resolves them differently.

| Command | Use | Guide |
| --- | --- | --- |
| `doctor` | Check the host, local container image and platform files | [Environment Doctor](ENVIRONMENT_DOCTOR.md) |
| `inspect` | Elaborate an ordered RTL manifest or validate a pinned ORFS manifest | [Quickstart](../README.md#installation-and-quickstart) |
| `plan` | Print an ORFS stage plan without invoking synthesis | [GCD walkthrough](tutorials/GCD_WALKTHROUGH.md#plan-the-implementation) |
| `run` | Start a new implementation in a fresh experiment directory | [GCD walkthrough](tutorials/GCD_WALKTHROUGH.md#run-and-resume) |
| `resume` | Continue from verified checkpoints or resume a native verification run | [Evidence contract](EVIDENCE_CONTRACT.md) |
| `report` | Read run evidence or evaluate an acceptance profile | [Verification and acceptance](VERIFICATION_AND_ACCEPTANCE.md) |
| `verify` | Run platform decks or evaluate a named verification profile | [Verification and acceptance](VERIFICATION_AND_ACCEPTANCE.md) |
| `experiments` | List, import or compare run evidence | [Evidence contract](EVIDENCE_CONTRACT.md) |
| `artifacts` | Render images and stage bundles, open the local workspace, or classify tool logs | [Physical workspace](PHYSICAL_WORKSPACE.md) · [Gallery](EXAMPLE_GALLERY.md#regenerate) |
| `diagnose` | Read deterministic timing analysis with report citations | [Timing diagnosis](TIMING_DIAGNOSIS.md) |
| `io` | Plan, approve, execute and inspect ICeWall placement | [IO pad-ring placement](IO_PAD_RING.md) |
| `optimize` | Plan and run approved bounded optimization campaigns | [Optimization](OPTIMIZATION.md) |
| `ai` | Observe approved evidence, request a supported explanation or replay the journal | [AI supervisor](AI_SUPERVISOR.md) |
| `knowledge` | Build and search a local engineering reference index | [Knowledge](KNOWLEDGE.md) |
| `mcp` | Serve or query approved run evidence over Model Context Protocol | [MCP](MCP.md) |

## Output and exit codes

- `plan`, `run`, `resume` and unprofiled `report` use JSON. The controller returns 0 for completed implementation and 1 when implementation cannot complete successfully.
- `verify RUN` invokes the platform decks. Its exit code reflects their outcomes. Use `verify RUN --profile physical --coverage --decks` for the wider physical verification scope.
- A profiled `verify` accepts `--markdown` and returns 0 for acceptance and 1 for failed or blocked acceptance. `report RUN --profile PROFILE` reads the verdict for an existing run without executing physical stages.
- `diagnose RUN --json` emits structured evidence. A successful diagnosis can report a failed gate.
- `artifacts snapshots RUN OUTPUT` emits a stage inventory and returns 1 when a selected checkpoint is missing or fails export. `artifacts triage` emits classified messages and evidence without evaluating acceptance.
- The controller uses 2 for invalid inputs and 130 for interruption. Doctor has its own [host readiness exit codes](ENVIRONMENT_DOCTOR.md).

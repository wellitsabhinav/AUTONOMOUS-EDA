# Verification and acceptance

The acceptance matrix is a separate schema 1 report derived from controller evidence and verification attempts. Existing experiment JSON, deck commands, checkpoints and optimization campaign records remain compatible.

~~~sh
cp -R examples/verification experiments/logic-verification
autonomous-eda verify experiments/logic-verification --profile rtl-formal
autonomous-eda report experiments/logic-verification --markdown
autonomous-eda resume experiments/logic-verification
autonomous-eda report .local/diagnosis-reference/ibex --profile full
autonomous-eda verify experiments/gcd-run --profile physical --coverage
autonomous-eda optimize report experiments/gcd-search --json
~~~

An RTL directory contains an authoritative `manifest.toml`, sources and optional `verification.toml`. A physical directory is a completed controller run or a historical archive with an audited `reference.json`. On Linux, `--coverage` runs OpenSTA inside the cached ORFS image. `--decks` reruns the controller's existing DRC and LVS targets. The standalone `verify RUN` command still runs those targets and reports their independent outcomes. Profiled verification returns 0 for acceptance, 1 for failed or blocked, 2 for invalid input, and 130 for interruption.

| Profile | Required evidence |
| --- | --- |
| `rtl` | Structural elaboration, explicit regression and artifact integrity |
| `rtl-formal` | RTL profile plus exact-port combinational equivalence |
| `physical` | Synthesis, setup, hold, coverage, routing, DRC, LVS, GDS creation, independent reopening and artifact integrity |
| `full` | Physical profile plus elaboration, regression, equivalence and independent connectivity |

The [example policy](../examples/verification/verification.toml) configures an explicit testbench, two oracle markers, a reference source list and port correspondence. A policy can add required gates and objective bounds. The built-in requirements are mandatory. To declare a sequential proof or property obligation, add `sequential_equivalence` or `formal_properties` to `require`. Those obligations stay blocked until a suitable backend and proof setup exist.

`[timing]` accepts `corner`, `sdc_sha256` for the final SDC, and an exact `clocks` list. By default the evaluator checks the recorded final and synthesis SDCs, the pinned Liberty and corner, the original clock period, and the clock inventories. A coverage pass requires a complete OpenSTA probe for missing clocks, multiple clocks, unconstrained endpoints, missing I/O delays, generated clock sources and loops. Unsupported exceptions block coverage. Setup and hold use independent verified finish metrics.

`[objectives]` accepts upper bounds on `standard_cell_area_um2`, `estimated_power_w`, and `routed_wirelength_um`, and lower bounds on `setup_worst_slack_ns` and `hold_worst_slack_ns`. A missing measurement blocks the objective. Campaigns continue to use their approved profile and compatible-run comparisons.

| State | Meaning |
| --- | --- |
| `PASSED` | An applicable check completed with passing, hash-bound evidence. |
| `FAILED` | Verified evidence demonstrates a check or hard objective violation. |
| `BLOCKED` | A required check cannot be resolved from compatible, intact evidence. |
| `UNKNOWN` | An optional applicable check has no observation. |
| `NOT_APPLICABLE` | A check is outside the run scope, such as a layout check for standalone RTL. |

The matrix gives independent implementation, verification, physical feasibility, objective and acceptance verdicts. A mandatory failure takes precedence over gaps. Physical profiles require implementation evidence. A historical GDS and independent reopen demonstrate terminal output. Individual checkpoints require a controller record. Pad-ring integration is tracked in [Patch 09A](https://github.com/wellitsabhinav/AUTONOMOUS-EDA/issues/20).

Each native verification or coverage dispatch creates an attempt and atomically advances `verification/latest.json`. An interrupted attempt cannot fall back to an older pass. Reports rehash inputs and outputs, validate the selected policy, check native tool executable identities and interpret the retained logs. Failed attempts stay in place. ORFS resume uses the controller's verified checkpoints. The matrix decision digest covers all fields except the digest itself.

Icarus runs trusted operator HDL. A pass requires one exact success marker with a zero exit status. An exact failure marker fails regression. Yosys compares distinct reference and candidate source sets with matching ports and supported combinational cells. SAT checks the output mismatch trigger for every defined two-state input. Sequential behavior, X/Z behavior and property checking require separate obligations. Native dispatch uses separate logs, a timeout and process cleanup. The pinned coverage container also has CPU, memory and PID limits.

MCP's `get_acceptance_summary` reads the full profile from an allowlisted schema 1 snapshot. It uses the read-only worker. The AI supervisor consumes recorded facts, while the deterministic evaluator owns acceptance.

Use `verify RUN --profile PROFILE --render` to export `acceptance.json`, `acceptance.md` and PNGs. `artifacts gallery RUN` revalidates the matrix and figure provenance. See the [evaluation](VERIFICATION_EVALUATION.md) and [gallery](EXAMPLE_GALLERY.md).

Larger designs require authoritative generated inputs, macro timing and physical views, clock-domain coverage, and proof correspondence at IP boundaries. See the [OpenSTA check definitions](https://github.com/The-OpenROAD-Project/OpenSTA/blob/master/search/Search.tcl) and the [Yosys command manual](https://yosyshq.readthedocs.io/_/downloads/yosys/en/0.33/pdf/).

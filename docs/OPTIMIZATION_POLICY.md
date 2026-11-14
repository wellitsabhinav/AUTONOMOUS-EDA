# Optimization policy

## Allowed parameter

`CORE_UTILIZATION` is the pinned ORFS core utilization percentage. The adapter accepts integers from 20 to 65, a deliberately bounded subset of the upstream float domain. GCD uses 38 by default and Ibex uses 50. The value affects automatic floorplanning. The optimizer starts each new candidate at synthesis in an isolated directory. Within that candidate it can reuse validated stage checkpoints on resume.

The pinned definitions are `flow/scripts/variables.yaml` and `flow/designs/sky130hd/{gcd,ibex}/config.mk` in revision `23341145f0fc92a522de26d5244a4a0013818f97`. The implementation verifies the cached image and hashes of the GCD config, Verilog, input SDC, platform config, Liberty and variable catalog before physical dispatch. The image digest binds the remaining packaged implementation and PDK files. The normalized synthesis SDC must match the audited baseline. Only the typed utilization argument reaches Make.

Other controls require a separate typed adapter and compatibility validation. This applies to placement density, routing Tcl, CTS, buffering switches and timing constraints.

## Execution scope

Explicit approval binds the full plan digest, baseline digest, canonical campaign root, finite parameter space, profile and budgets for up to 24 hours. A campaign allows at most four reservations, one active implementation, 900 seconds per candidate and 3,600 seconds total. Campaign time runs from first dispatch and includes interruption and verification time. Each candidate uses two baseline CPU cores and a memory cap checked against host availability. Docker containers use the exact cached image, no network and no pulls. The optimizer accepts only the local loopback model adapter with zero paid cost.

The controller writes stage commands, exit codes, runtime, hashes, and restart history. A campaign reservation retains its identity and time allocation after a crash. Invalid checkpoints are archived by the controller before rebuilding affected stages. The campaign lock prevents concurrent runners. Cancellation and deadlines are polled during dispatch. Named-container cleanup can take additional time after a stop. Independent verification results are reused only when bound to the same GDS hash and still intact.

The campaign and evidence directories are operator-owned. Filesystem protections reject linked experiment roots and candidate contents. A journal is a local engineering record, so its containing directory must be protected from other writers. Preserved output remains available for diagnosis when evidence cannot be revalidated.

## Feasibility

The physical profile requires immutable inputs, artifact integrity, completed stages, synthesis, GDS generation and reopening, setup, hold, router checks, DRC, and LVS. Full acceptance adds the remaining schema 1 gates and requires all requested objective measurements. An explicitly failed gate keeps the candidate infeasible. Missing required gates block verification. Physical exclusions are listed in each outcome. The feasible frontier uses only verified, comparable candidates with all declared objective measurements. A run can complete while both frontiers remain empty.

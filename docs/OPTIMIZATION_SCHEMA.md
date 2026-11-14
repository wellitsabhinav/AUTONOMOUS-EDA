# Optimization and visualization schemas

Both report formats use schema version 1.

| Optimization record | Fields |
| --- | --- |
| Plan | Campaign ID, baseline run and evidence digests, design, top, immutable RTL and SDC identity, clock and corner, toolchain, parameter registry, grid, seed, strategy, objectives, budgets and stop criteria |
| Approval | Plan and baseline digests, approval time, expiration and policy |
| Candidate | ID, parent digest, baseline identity, typed parameter delta, strategy, predicted effects and risks, diagnosis citations, resource bounds and verification requirements |
| Outcome | Execution completion, physical feasibility, full acceptance, every independent gate, exclusions, comparison compatibility, measured metrics, deltas and supporting hashes |
| Campaign report | Plan, approval, candidates, events, budget consumption, terminal reason and feasible frontiers |

The campaign journal binds candidate IDs to controller run IDs. The report rehashes retained artifacts before assigning current feasibility. Missing metrics are null. Timing deltas are absolute because percentages of signed slack are misleading. Area, power and routing percentages require positive denominators. The recorded runtime sums verified controller and verification attempts where those logs are available. Peak process or container memory remains unavailable when unmeasured.

A visualization manifest maps each image name to its run ID, source artifact paths and SHA-256 hashes, PNG SHA-256, dimensions, renderer and version, render settings, generated timestamp and evidence status. Campaign images cite the generated structured report, which carries the verified run references. Layout views cite final GDSII and record the pinned renderer and layer mapping. Gallery inspection validates source and image integrity and marks changed inputs stale. A PNG illustrates its verified source. Gate outcomes come from the structured evidence.

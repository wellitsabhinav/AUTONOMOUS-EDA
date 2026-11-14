# ICeWall chip integration

Patch 09C uses the approved [ICeWall placement flow](IO_PAD_RING.md) to check a SKY130 pad ring, connect core signal nets to pad terminals, route those signals, and assemble and reopen a chip GDSII file. The [evaluation](IO_CHIP_EVALUATION.md) records the measured results and acceptance limits.

## Architecture

The optional `io chip` command reads the pad-placement plan, mapped core netlist, and verified OpenDB and DEF checkpoint. OpenROAD checks the classes, sizes and selected terminal geometries of the pinned pad, corner and filler abstracts. A second OpenROAD process creates separate inner signal and external bond nets, checks signal coverage and driver counts, and writes a connected database. An independent process reopens that database and inspects the signal terminals.

On the supported mapped-core example, OpenROAD places the demo core cells and routes the inner signal nets with the SKY130 routing tracks. The detail-router violation count and positive-length wire encoding for each required signal are checked against the reopened routed database. The external terminal uses the pad bond-pin rectangle. The experiment retains the supply and control observations from the database.

KLayout independently reads candidate GDS files to check the expected cell names and nonempty views. KLayout and the pinned ORFS DEF-to-stream utility merge the routed DEF with real pad and standard-cell geometry. A separate KLayout process reads the resulting GDS, walks its hierarchy, checks macro multiplicities against the DEF, and checks the die outline and top-level geometry. The geometry extent has its own gate.

## Supported configuration

The [placement manifest](../examples/io/placement.toml) defines the die, pad side, location, master, scalar core port and direction. The [chip manifest](../examples/io/chip.toml) selects the corresponding live pin roles and external ports. Every signal pad needs exactly one mapping. External port names must be unique.

| Role | Pad master or pin |
| --- | --- |
| Digital input | `sky130_ef_io__gpiov2_pad_wrapped/IN` drives the core net |
| Digital output | `sky130_ef_io__gpiov2_pad_wrapped/OUT` receives the core net |
| External signal | `PAD` on the wrapped GPIO master |
| Digital power and ground abstracts | `sky130_ef_io__vccd_lvc_pad/VCCD` and `sky130_ef_io__vssd_lvc_pad/VSSD` |
| Continuity cells | Pinned 1, 5, 10 and 20 µm bus slices and the SKY130 corner |

The adapter supports the pinned SKY130HD mapped-core floorplan from Patch 09A. Import and preservation of an independently routed controller core, bidirectional mode, unused pad tie-offs, and qualified supply-domain routing need further implementation. The example uses single-threaded routing and a `met1` through `met4` signal layer range. Any configured range must cover `met2`, the inward-facing access layer for these GPIO pins.

## Run the example

Use Linux or WSL2, Python 3.11 or newer, Docker, and the cached ORFS image. Install the visual extra for the Matplotlib reports.

~~~sh
python -m pip install -e '.[dev,visuals]'
docker pull openroad/orfs@sha256:8eff077230b86729f6248b34eeab370e486fa4052d34c60d9067e3e4aa720987
autonomous-eda io doctor
placement=experiments/io-09c-placement
autonomous-eda io plan examples/io/placement.toml "$placement"
digest=$(python -c 'import json,sys; print(json.load(open(sys.argv[1]))["plan_digest"])' "$placement/plan.json")
autonomous-eda io approve "$placement" --digest "$digest"
autonomous-eda io run "$placement"
autonomous-eda io chip "$placement" examples/io/chip.toml experiments/io-09c-pinned
autonomous-eda io chip-render experiments/io-09c-pinned
~~~

The pinned image contains real IO LEFs and a dummy IO Liberty. The pinned-only run measures mapped signal routes and reports the missing stream and timing views. For the independently reopened example, follow the [pinned upstream view recipe](IO_CHIP_EVALUATION.md#reproduce-the-upstream-view-run).

The command writes `chip-report.json`, `chip-report.md`, `pad_library_qualification.json`, and `pad_library_qualification.md`. The standard exit status covers the requested signal-probe execution. Use `--require-acceptance` for a nonzero exit when chip acceptance remains blocked. Use `routing.enabled = false` or `signoff = "excluded"` to record an explicit `SKIPPED` outcome. Full acceptance still requires all physical signoff gates.

## Verification and troubleshooting

Each gate uses `PASS`, `FAIL`, `BLOCKED`, or `SKIPPED`. `database_signal_connectivity` records terminal association. `signal_routing` records completed signal detail routing, the router violation count, and OpenDB wire readback. Full `logical_connectivity` requires functional pad modes and complete supplies. The adapter flags shared nets containing mixed POWER and GROUND terminals and lists unconnected controls and supply terms. The round-trip structural GDS check verifies hierarchy and outline. Electrical connectivity still requires extracted LVS and device-level checks.

Full-chip design rule checking (DRC) requires a qualified IO-inclusive deck. Layout versus schematic (LVS) requires qualified extraction and an independent reference including pad devices and supply topology. Static timing analysis (STA) requires characterized IO modes and voltage corners, chip constraints and parasitics. The adapter records the pinned core DRC, LVS and Liberty hashes for context. The controller's separate core timing results retain their own scope.

Stage receipts bind generated scripts, invocation arguments, the image, per-stage tool and collateral hashes, and every staged input and output. Rerun the same command to verify and reuse current stages. A changed input invalidates consumers through their content hashes. The signoff policy is evaluated each invocation. Failed attempts remain in `stages/`. A changed placement DEF or ODB invalidates the upstream checkpoint. Run `io resume` on the placement directory before dispatching again. For GDS discrepancies, inspect the library inventory, stream-out logs, and reopening result before choosing different views or placement offsets.

# ICeWall chip integration evaluation

Patch 09C starts from `797fdf2ed2197abb9ac4ccd9edea6a8778c05a26`. PRs #21 through #23 were merged before work began. The Linux strict-mypy correction reached `main` in #22. The baseline and Patch 09A head checks passed. [Baseline CI](https://github.com/wellitsabhinav/AUTONOMOUS-EDA/actions/runs/43556123430)

## Measured results

The retained 0 µm-offset checkpoint routed two inner nets and exported a GDS with the expected hierarchy. KLayout independently observed a 0.035 µm overhang to the west. A separate run inset the IO rows by 2 µm, retained four pads and four corners, and placed 168 continuity cells. OpenROAD reported 1,317 µm of signal wire and zero detailed-router violations. KLayout reopened the 16,361,700-byte layout with 545 hierarchical cells, a 1,200 × 1,200 µm outline, exact top-level component counts and zero empty reachable cells.

The selected wrapped GPIO Liberty passed the pin direction checks and exposed 102 timing arc sets, including the two signal paths used by the probe. The OpenDB report still shows 36 unconnected GPIO controls, 10 unconnected supply terminals, and 22 pad supply terminal observations on nets containing both POWER and GROUND terminals. These are database-level associations. Full-chip electrical behavior remains unverified.

| Gate | Status | Observed evidence |
| --- | --- | --- |
| LEF abstracts | PASS | Pinned OpenROAD parsed and checked selected classes, sizes, pins and access layers |
| Pad GDS inventory | PASS | Eight selected pad, corner and filler masters found |
| Liberty interface | PASS | Wrapped cell, required pin directions and 102 arc sets |
| Pad library qualified | BLOCKED | Pin geometry equivalence, operating modes and supply-domain qualification pending |
| Placement | PASS | Four pads, four corners and 168 filler cells on a 1,200 µm square die |
| Full logical connectivity | BLOCKED | Mode controls and supplies unresolved |
| OpenDB signal association | PASS | Independent OpenDB read verified two mapped pad terminals and external ports |
| Core-to-pad signal routing | PASS | Two detailed routes, 1,317 µm total reported wire and zero router violations |
| Power connectivity | FAIL | Shared nets contain both POWER and GROUND terminals |
| Chip GDS generation | PASS | Strict stream-out completed with upstream cell views |
| Independent reopening | PASS | Hierarchy, expected multiplicities, boundary and top geometry matched |
| Geometry extent | PASS | The 2 µm inset brought the hierarchy inside the die |
| Full-chip DRC | BLOCKED | IO-qualified deck and electrical implementation pending |
| Full-chip LVS | BLOCKED | Qualified extraction, IO devices and independent reference schematic pending |
| Full-chip STA | BLOCKED | Functional IO modes, corner agreement, chip constraints and parasitics pending |
| Checkpoint integrity | PASS | Hash-bound reads, stage reuse and deterministic routed DEF on rerun |
| Documentation | PASS | Documentation tests verify links and published source receipts |
| CI | See draft checks | [PR #26](https://github.com/wellitsabhinav/AUTONOMOUS-EDA/pull/26/checks) |
| Full-chip acceptance | BLOCKED | Supply routing and full-chip signoff remain open |

The [machine-readable run](images/examples/io-integration/chip-report.json) records the stage keys, commands, timestamps, tool and view hashes, terminal findings and output hashes. The [PNG provenance](images/examples/io-integration/provenance.json) links the figures to that run and the separately reopened GDS. Full databases and native logs remain under `experiments/m9c-inset/`. The failed 0 µm run, pinned-only attempt, and earlier failed stream-out remain in separate experiment directories.

## Reproduce the upstream view run

Install the [toolchain](IO_CHIP_INTEGRATION.md#run-the-example) first. The recipe pins LambdaPDK for the Foundry IO dependencies and Open_PDKs for the wrapped GPIO and Efabless IO GDS, plus typical wrapped GPIO Liberty. The open-source collateral stays under the ignored experiment directory. [LambdaPDK revision](https://github.com/siliconcompiler/lambdapdk/tree/51ac6529207ce9b161a637fe596133a8b111928a/lambdapdk/sky130/libs/sky130io) · [Open_PDKs revision](https://github.com/fossi-foundation/open-pdks/tree/e7a6777d157a9d94c7ac757457b9e4309e9e9000/sky130/custom/sky130_fd_io)

~~~sh
base=experiments/io-09c-views
mkdir -p "$base"
git clone --filter=blob:none --sparse https://github.com/siliconcompiler/lambdapdk.git "$base/lambdapdk"
git -C "$base/lambdapdk" checkout 51ac6529207ce9b161a637fe596133a8b111928a
git -C "$base/lambdapdk" sparse-checkout set lambdapdk/sky130/libs/sky130io
git clone --filter=blob:none --sparse https://github.com/fossi-foundation/open-pdks.git "$base/open-pdks"
git -C "$base/open-pdks" checkout e7a6777d157a9d94c7ac757457b9e4309e9e9000
git -C "$base/open-pdks" sparse-checkout set sky130/custom/sky130_fd_io
open_views="$base/open-pdks/sky130/custom/sky130_fd_io"
cp "$base/lambdapdk/lambdapdk/sky130/libs/sky130io/gds/sky130_fd_io.gds" "$base"
cp "$open_views/gds/sky130_ef_io.gds" "$open_views/gds/sky130_ef_io__gpiov2_pad_wrapped.gds" "$base"
cp "$open_views/lib/sky130_ef_io__gpiov2_pad_wrapped_tt_tt_025C_1v80_3v30.lib" "$base"
cp examples/io/core.v "$base"
sed 's/^offset_um = 0$/offset_um = 2/' examples/io/placement.toml > "$base/placement.toml"
cp examples/io/chip.toml "$base/chip.toml"
cat >> "$base/chip.toml" <<'TOML'

[library]
provenance = "open-pdks e7a6777d157a9d94c7ac757457b9e4309e9e9000 and LambdaPDK 51ac6529207ce9b161a637fe596133a8b111928a"
gds = ["sky130_fd_io.gds", "sky130_ef_io.gds", "sky130_ef_io__gpiov2_pad_wrapped.gds"]
liberty = "sky130_ef_io__gpiov2_pad_wrapped_tt_tt_025C_1v80_3v30.lib"
TOML
placement=experiments/io-09c-inset-placement
chip=experiments/io-09c-inset
autonomous-eda io plan "$base/placement.toml" "$placement"
digest=$(python -c 'import json,sys; print(json.load(open(sys.argv[1]))["plan_digest"])' "$placement/plan.json")
autonomous-eda io approve "$placement" --digest "$digest"
autonomous-eda io run "$placement"
autonomous-eda io chip "$placement" "$base/chip.toml" "$chip"
autonomous-eda io chip-render "$chip"
AEDA_TEST_ICEWALL=1 AEDA_TEST_CHIP_VIEWS="$base/chip.toml" python -m pytest -q
~~~

The real-tool suite exercised ICeWall, mapping and routing, a modified DEF, checkpoint reuse, and independent GDS reopening. The pinned OpenROAD build reports version `unknown`; its executable SHA-256 and image digest identify the binary. KLayout reports `0.30.12`. Historical controller and pad-placement evidence remains available through the [gallery](EXAMPLE_GALLERY.md).

## Next engineering step

Start from a qualified pad power topology. Use the appropriate isolated or bridging bus slices, drive the documented GPIO mode controls, bind independent schematic and layout views, and verify supply domains before running full-chip DRC, extraction, LVS and STA. The scoped follow-up is tracked by [issue #24](https://github.com/wellitsabhinav/AUTONOMOUS-EDA/issues/24) and [pad assembly issue #20](https://github.com/wellitsabhinav/AUTONOMOUS-EDA/issues/20).

# Physical workspace

Patch 09D exports retained checkpoints and opens them in a local browser with stage comparison, layout inspection and a live readout of recorded execution. Rendering uses the pinned ORFS image on Linux or WSL2. Once a bundle exists, the viewer runs with base Python on Windows or Linux. Follow the [GCD walkthrough](tutorials/GCD_WALKTHROUGH.md) for environment setup and run commands.

## Export and open GCD

~~~sh
autonomous-eda artifacts snapshots experiments/gcd-walkthrough experiments/gcd-snapshots
autonomous-eda artifacts workspace experiments/gcd-snapshots --run experiments/gcd-walkthrough --journal .local/manual-ai.sqlite3
~~~

Open the loopback URL printed by the second command. Omit the journal argument if you skipped the AI walkthrough. Give each export a fresh destination outside its input root. The command returns 1 if a stage is absent, modified, superseded or fails to render, and saves the outcomes for every selected stage in the bundle. Stop the server with Ctrl+C.

| Backend | Input | Available inspection |
| --- | --- | --- |
| OpenROAD | OpenDB checkpoint | Placed instance rectangles and masters, pad instances, port boxes, net connectivity, clock pins, routed metal rectangles, route guides and GCell records when present |
| OpenROAD | DEF with ordered technology and cell LEFs | Layout geometry and connectivity carried by the DEF and LEFs |
| KLayout | GDSII | Hierarchical polygon, box and path geometry by GDS layer and datatype. Large layouts have a 300,000-polygon vector preview limit. |

Each PNG carries JSON in the PNG text field **autonomous-eda.provenance**, including source path, run, stage, SHA-256, tool banner, pinned image digest and generation command. The bundle records PNG and geometry hashes and retains tool logs. OpenROAD in the pinned image reports its version as **OpenROAD unknown**. The image digest identifies the build. KLayout reports **0.30.12**. [OpenROAD image and area options](https://openroad.readthedocs.io/en/latest/main/src/gui/README.html) · [OpenDB geometry](https://openroad.readthedocs.io/en/latest/main/src/odb/README.html)

The viewer starts at placement. Use the stage timeline to select CTS, then turn on **Clock pin topology**. Clock lines join the sole recorded driver to placed sink pins. Highlighted clock buffer and inverter masters also connect to a recorded CLOCK net. Choose **Global route** in **Compare** to inspect both stages with a shared viewport. Select a cell, port or routed shape, or search its name. **Checkpoint PNG** exports the exact renderer output. **Export view** exports the current primary canvas with the viewport and display settings embedded in its provenance.

The **Route guides** display shows guide rectangles. **GCell usage / capacity** shows retained routing grid measurements. **Routed wire shapes** shows layer-resolved signal wire rectangles, with vias and special wires outside this vector overlay. The OpenROAD PNG and final GDS provide additional physical geometry. The timing overlay joins matched pin locations in a verified stage OpenSTA report and breaks at unresolved anchors. It uses the printed path slack. The GCD final setup metric remains −1.20488 ns.

## Additional inputs

For a standalone DEF, select run-relative paths. Supply the technology LEF first and then the matching cell LEFs. An explicit checkpoint receives operator-selected provenance and hashes of the source and LEFs.

~~~sh
autonomous-eda artifacts snapshots .local/def-inputs experiments/def-preview --checkpoint finish-def gcd.def --run-id gcd-def --top gcd --lef sky130_fd_sc_hd.tlef --lef sky130_fd_sc_hd_merged.lef
~~~

The [retained Ibex archive](EXAMPLE_GALLERY.md#ibex-at-8-ns) rendered its hash-indexed final GDS. The six OpenDB stages were absent from that archive inventory and are listed as missing. The exporter returned 1 with a usable GDS entry. To build a fresh pinned reference, run the [Ibex manifest](../configs/ibex-sky130hd.toml) and export its stage bundle.

~~~sh
autonomous-eda plan configs/ibex-sky130hd.toml
autonomous-eda run configs/ibex-sky130hd.toml experiments/ibex-workspace
autonomous-eda artifacts snapshots experiments/ibex-workspace experiments/ibex-snapshots
~~~

For the [ICeWall placement example](IO_PAD_RING.md), use the current attempt from **io-latest.json** in both the run ID and checkpoint path. Search for a pad, corner or spacer and inspect its master and connected nets. The retained placement example has four pads, four corners and 156 spacers. Patch 09C full-chip power and signoff are tracked in [issue #24](https://github.com/wellitsabhinav/AUTONOMOUS-EDA/issues/24).

~~~sh
autonomous-eda artifacts snapshots experiments/icewall experiments/icewall-snapshots --checkpoint pad-ring io-attempts/ATTEMPT_ID/placement.odb --run-id ATTEMPT_ID --top io_demo
autonomous-eda artifacts workspace experiments/icewall-snapshots
~~~

## Execution console

The console checks the selected Git checkout, detects host tools on PATH, reads renderer versions from the bundle and rehashes indexed assets. It polls the selected controller database and AI journal every two seconds while visible. Controller rows report recorded state, runtime, exit code and artifact references. Expand an attempt for its retained log and classified messages. Supervisor activity comes from the journal’s transitions and active leases. A recorded running attempt is labeled accordingly. The console observes existing CLI execution.

## Diagnose the retained OpenROAD failure

The [original Tcl probe](evidence/physical-snapshots/teardown-probe.tcl) loaded the floorplan checkpoint, then tried to destroy and recreate the chip and block before loading placement. The [original log](evidence/physical-snapshots/openroad-teardown.log) stops with **Signal 11 received** after the floorplan inventory. The local workaround starts one process and one temporary work directory per stage. An unsuccessful process retains its log. A crash regression verifies that any PNG produced by that process is excluded and subsequent checkpoints can still publish.

~~~sh
autonomous-eda artifacts triage docs/evidence/physical-snapshots --log floorplan openroad-teardown.log --tool OpenROAD --output experiments/teardown-diagnostic
~~~

The bundle includes the **OPENROAD_PROCESS_SIGNAL** code, line 8, the exact message, its log-prefix hash, a bounded hypothesis and a diagnostic command. The classifier also recognizes selected Yosys, OpenSTA, ICeWall, KLayout, Icarus and verification signatures. Unsupported messages keep their cited excerpt and an unclassified code. Multiple matching stages correlate by signature.

Review the proposed correction and use its exact **approval_digest** to record approval for manual execution. Then run the proposed command or repeat the export in a new directory. The diagnostic bundle and approval preserve the observed log and correction independently of acceptance.

~~~sh
autonomous-eda artifacts triage-approve experiments/teardown-diagnostic --digest APPROVAL_DIGEST
~~~

Optional **--local-model** and **--local-endpoint** use a configured loopback Chat Completions model. The model can prioritize existing findings by ID. The classifier rejects unsupported IDs and retains the deterministic findings. See [model gateway](MODEL_GATEWAY.md) for local runtime setup.

## Measured GCD export

Run **b162fa09cf9442dda08b0b31cb58c728** rendered seven checkpoints at 1,000 px wide. Placement contains 502 placed cells. CTS contains 521 placed cells and six clock nets. The global-route checkpoint exposes 2,867 guide rectangles and 500 GCell records. The final GDS provides 56,243 polygons. The published PNGs and their [provenance](images/gcd-stage-snapshots/provenance.json) bind to the original [run receipt](evidence/gcd-walkthrough/receipt.json).

| Floorplan | Placement | CTS |
| --- | --- | --- |
| ![GCD floorplan](images/gcd-stage-snapshots/floorplan.png) | ![GCD placement](images/gcd-stage-snapshots/place.png) | ![GCD CTS](images/gcd-stage-snapshots/cts.png) |
| [Global route](images/gcd-stage-snapshots/grt.png) | [Detailed route](images/gcd-stage-snapshots/route.png) | [Final OpenDB](images/gcd-stage-snapshots/finish.png) · [Final GDS](images/gcd-stage-snapshots/gds.png) |

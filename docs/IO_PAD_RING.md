# IO pad-ring placement

Patch 09A adds an optional chip-assembly entry point for the pinned OpenROAD ICeWall engine. Core IO pin placement assigns terminals to the core boundary. ICeWall places pad macros, corner cells and continuity cells in die-side rows. The seven-stage controller remains the entry point for core implementation.

## Run the placement example

Use Linux or WSL2 with the cached ORFS image.

~~~sh
autonomous-eda io doctor
autonomous-eda io plan examples/io/placement.toml experiments/icewall
autonomous-eda io approve experiments/icewall --digest PLAN_DIGEST
autonomous-eda io run experiments/icewall
autonomous-eda io resume experiments/icewall
autonomous-eda io report experiments/icewall
autonomous-eda io verify experiments/icewall
autonomous-eda io render experiments/icewall --all
autonomous-eda artifacts gallery experiments/icewall
~~~

The [configuration](../examples/io/placement.toml) sets die dimensions, core clearance, row offset, fixed per-side locations, scalar input/output mapping and digital supply pads. OpenROAD checks that the mapped netlist's port names and directions match. The supplied buffer uses an actual Sky130HD standard-cell master. A disabled configuration consists of `schema_version = 1` and `enabled = false`. It reports pad gates as `NOT_APPLICABLE`.

The plan hashes the copied netlist, fixed Tcl, image, executable and selected LEF, Verilog and Liberty views. Approval binds the digest. Run and resume recheck the library and checkpoint hashes. A changed artifact invalidates the placement. The named offline container has CPU, memory and process limits. Failed attempts stay available.

The fixed Tcl uses `make_io_sites`, `place_corners`, `place_pad`, `place_io_fill` and `connect_by_abutment`. DEF checks confirm the pad masters, fixed locations, row orientations and four corners. Filler counts document placed abstracts. Electrical continuity requires qualified connectivity evidence. The pinned tool also exposes automatic placement, terminal creation, bond-pad placement, bump arrays and redistribution routing. Those operations remain outside this adapter's supported configuration.

## Verification scope

The bundled `sky130io` directory supplies LEF abstracts, black-box Verilog and dummy Liberty. The pinned image lacks matching pad GDS. The adapter exports placement evidence and charts. Chip GDS, independent reopening, pad DRC and LVS, routed signal connectivity, and supply continuity stay blocked. Chip acceptance requires qualified timing, geometry, connectivity, ESD and package evidence.

See the [measured evaluation](IO_PAD_RING_EVALUATION.md), [OpenROAD ICeWall documentation](https://openroad.readthedocs.io/en/latest/main/src/pad/README.html), and [ORFS tutorial](https://openroad-flow-scripts.readthedocs.io/en/latest/tutorials/FlowTutorial.html).

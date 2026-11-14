# IO pad-ring evaluation

Pinned ICeWall placed a four-sided ring of real Sky130 IO abstracts around a mapped Sky130HD buffer. The plan used a 1,200 × 1,200 µm die, 280 µm requested core clearance, two wrapped GPIO pads, one digital power pad and one digital ground pad. OpenDB and DEF recorded four corners and 39 continuity cells on each side. The successful attempt took 1.28 s including container dispatch. Resume reused the same attempt.

| Check | Outcome |
| --- | --- |
| Scalar core port names and directions | PASSED |
| Fixed pad masters, locations and orientations | PASSED |
| Four corner instances | PASSED |
| Full library qualification | BLOCKED |
| Filler continuity and core-to-pad routing | BLOCKED |
| Supply network connectivity | BLOCKED |
| Chip GDS, independent reopening, DRC and LVS | BLOCKED |
| Full pad-ring acceptance | BLOCKED |

The LEFs identify actual wrapped GPIO, digital supply, corner and bus-slice cells. The black-box Verilog and dummy Liberty do not establish functional or timing qualification. Bond-pad geometry and matching pad GDS are unavailable in the pinned Sky130 IO directory. The probe ran ICeWall abutment connectivity. Electrical continuity remains unresolved.

The [IO figure provenance](images/examples/io-placement/provenance.json) binds the [placement report](images/examples/io-placement/io-report.json). Full OpenDB and DEF remain in the local experiment directory. See the [placement guide](IO_PAD_RING.md) for reproduction.

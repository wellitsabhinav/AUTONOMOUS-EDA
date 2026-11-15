# OpenTitan AES source resolution and elaboration

The intermediate target is the `aes` peripheral from OpenTitan Earl Grey 1.0.0 at `b2d662157db2802bde0e40dfc96fd0316042d116`. Its native synthesis target selects the default configuration with masking and AES-192 enabled. The evidence includes packages, TL-UL and EDN interfaces, generated primitive wrappers, two clock ports and a separate shadow-register reset. [Pinned AES core](https://github.com/lowRISC/opentitan/blob/b2d662157db2802bde0e40dfc96fd0316042d116/hw/ip/aes/aes.core) · [Pinned interface and configuration](https://github.com/lowRISC/opentitan/blob/b2d662157db2802bde0e40dfc96fd0316042d116/hw/ip/aes/data/aes.hjson)

## Design selection

| Candidate | Native inputs and resource implications | Decision |
| --- | --- | --- |
| OpenTitan AES | FuseSoC resolves ordered SystemVerilog packages and generated wrappers. The design exercises masked datapaths, hierarchical registers and buses, and clock/reset crossings with the upstream parameter defaults. sv2v and the pinned Yosys build process the source on a 4 GiB WSL host. | Primary target |
| ORFS Sky130HD AES | The image ships this independent AES core with a Verilog fileset and Sky130HD timing constraints. It has a single core clock and a smaller source dependency graph. | Alternate if first-party OpenTitan front-end support fails |
| ORFS Sky130HD JPEG | The image ships an arithmetic-intensive Verilog encoder, include paths and Sky130HD constraints. Its upstream README estimates 417,000 cells. This milestone has no measured resource budget for that workload. | Later resource stress case |
| ORFS Sky130HD Chameleon | The image ships a SoC config with explicit black boxes, SRAM and other hard-block LEF and GDS views. Physical qualification also requires matching timing and electrical views. | Later macro integration case |

The ORFS alternatives are present in the [pinned flow's Sky130HD design directory](https://github.com/The-OpenROAD-Project/OpenROAD-flow-scripts/tree/23341145f0fc92a522de26d5244a4a0013818f97/flow/designs/sky130hd).

## Reproduce the ordered manifest

Run from the AUTONOMOUS-EDA checkout on Linux or WSL2. Use a Linux filesystem for the upstream clone so Git preserves symlinks. Use Python 3.12 and a fresh virtual environment.

~~~bash
repo=$(pwd -P)
root=$(mktemp -d)
revision=b2d662157db2802bde0e40dfc96fd0316042d116
git clone --branch earlgrey_1.0.0 --single-branch --depth 1 \
  https://github.com/lowRISC/opentitan.git "$root/earlgrey"
git -C "$root/earlgrey" checkout --detach "$revision"
python3.12 -m venv "$root/tools"
source "$root/tools/bin/activate"
python -m pip install \
  'fusesoc @ https://github.com/lowRISC/fusesoc/archive/refs/tags/ot-0.5.dev0.zip' \
  'edalize @ https://github.com/lowRISC/edalize/archive/refs/tags/v0.4.0.zip' \
  mako==1.1.6 Jinja2==3.1.6 MarkupSafe==3.0.4 attrs==26.1.0 okonomiyaki==3.0.0 \
  pyparsing==3.3.3 pyyaml==6.0.3 simplesat==0.9.2 six==1.17.0
for attempt in first second; do
  out="$root/$attempt"
  (cd "$root/earlgrey" && PYTHONHASHSEED=0 fusesoc --cores-root=. run \
     --target=syn --tool=icarus --setup --build-root="$out" lowrisc:ip:aes)
  edam="$out/syn-icarus/lowrisc_ip_aes_1.0.eda"
  python -c 'import json,sys,yaml; json.dump(yaml.safe_load(sys.stdin),sys.stdout,indent=2)' \
    <"$edam.yml" >"$edam.json"
  PYTHONPATH="$repo/src" python -m autonomous_eda ingest \
    "$edam.json" "$out/design.toml" --revision "$revision" \
    --generated-core-prefix lowrisc:prim_abstract: >"$out/resolved.json"
done
cmp "$root/first/resolved.json" "$root/second/resolved.json"
~~~

The two native builds must agree on the source order, include and source hashes, dependency graph, generated outputs and manifest fingerprint. The initial unseeded runs changed the enum order in `prim_pkg.sv`. The upstream generator iterates a Python set when enumerating technology libraries. The pinned environment and fixed hash seed produced identical receipts in independent builds. Keep the complete resolved source tree alongside the manifest to replay inspection without FuseSoC.

The EDAM adapter reads the exported synthesis fileset in order and preserves headers, defines, source ownership and dependency edges. Files outside the export tree, unsupported EDAM entries and missing inputs cause an error. `--generated-core-prefix` identifies generated core outputs so the receipt records them separately. FuseSoC and the upstream generator still own source resolution and generation.

## Elaborate and inspect

Use the Linux sv2v v0.0.13 release and Yosys `0.68+post` from the [pinned ORFS image](../EVIDENCE_CONTRACT.md). The sv2v release archive SHA-256 is `552799a1d76cd177b9b4cc63a3e77823a3d2a6eb4ec006569288abeff28e1ff8`. Put both binaries on the tool path and use a fresh output location. [sv2v v0.0.13](https://github.com/zachjs/sv2v/releases/tag/v0.0.13)

~~~bash
PYTHONPATH="$repo/src" python -m autonomous_eda inspect "$root/first/design.toml" \
  --timeout 180 --netlist "$root/aes-netlist.json" \
  --translated-verilog "$root/aes-translated.v" >"$root/aes-inspect.json"
~~~

The translated Verilog is a generated derivative of the selected top and manifest. The report binds the sv2v version and binary SHA-256, argument order, input fingerprint, translation hash and Yosys netlist hash. The hierarchy and structural clock/reset observations come from the elaborated netlist. Named clocks, reset topology and power intent require the upstream integration metadata.

The repeated runs agreed on 226 ordered sources, 233 hashed inputs, 95 core dependency nodes and 11 generated primitive sources. The ordered manifest matches Edalize's native Icarus file list. Yosys observed 174 reachable module definitions and 2,172 hierarchical instances. Generic synthesis produced 56,622 primitive cells, including 6,016 flip-flops. Those counts characterize the elaboration and generic synthesis, with no Liberty mapping or area measurement. [Resolved manifest](../evidence/opentitan-aes/design.toml) · [Source and dependency receipt](../evidence/opentitan-aes/resolved.json) · [Inspection and hierarchy](../evidence/opentitan-aes/inspect.json)

The source setup took 4.31 s and 4.56 s in the independent runs. Staging, translation and elaboration took 38.33 s with a peak RSS of 484,400 KiB. Generic synthesis took 40.18 s with a peak RSS of 196,732 KiB. GNU time reported process RSS and wall time under a two-CPU, 3 GiB container limit. The WSL2 host exposed four Ryzen 7 7735HS CPUs and 3.8 GiB of RAM. [Tool and measurement receipt](../evidence/opentitan-aes/receipt.json)

## Readiness and remaining gates

| Level | Evidence required |
| --- | --- |
| 0 | Pinned source and dependency discovery |
| 1 | Successful elaboration of the pinned configuration |
| 2 | Technology-mapped synthesis with documented limits |
| 3 | Core ORFS implementation with stage evidence |
| 4 | Core timing, integrity, DRC and LVS qualification |
| 5 | Qualified hierarchical partitions, abstracts and interfaces |
| 6 | Complete chip timing, physical and electrical collateral |

The 10A source receipt established **Level 1** and recorded generic gate synthesis. The 10B physical receipt records the higher observed level from Sky130HD execution. Earl Grey connects both AES clock inputs to the main source and applies distinct port and reset connections at integration. The upstream Nangate pre-synthesis SDC drives inputs with `BUF_X2`. The isolated Sky130HD flow still needs a qualified interface timing contract and the AES functional DV suite. [Pinned top configuration](https://github.com/lowRISC/opentitan/blob/b2d662157db2802bde0e40dfc96fd0316042d116/hw/top_earlgrey/data/top_earlgrey.hjson) · [Pinned chip SDC](https://github.com/lowRISC/opentitan/blob/b2d662157db2802bde0e40dfc96fd0316042d116/hw/top_earlgrey/syn/chip_earlgrey_asic.sdc)

The elaborated AES netlist has no unresolved black boxes or inferred memories in the current inventory. For the full chip, SRAM, ROM, nonvolatile memory, analog support and IO still need technology-bound logical, timing, geometry and electrical views. Whole-chip generation, elaboration, signoff and partition interface qualification are outside this measurement. [Earl Grey design documentation](https://github.com/lowRISC/opentitan/tree/b2d662157db2802bde0e40dfc96fd0316042d116/hw/top_earlgrey)

## Attempt the Sky130HD core flow

Use the [pinned physical manifest](../../configs/opentitan-aes-sky130hd.toml). The controller accepts only the 10A translated Verilog digest and stages a fixed Make config and SDC. It verifies the three files before dispatch and on resume. Make config and SDC checks are independent of the tool stage checkpoints.

The block SDC applies Earl Grey's 10 ns main target multiplied by its 0.85 overconstraint factor to both clock ports. The ports retain independent physical nets. The experiment uses the stock Sky130HD typical library, 35% starting utilization, two cores and a 3,000 MiB container memory limit. The IO delays and cross-block, reset and supply constraints remain integration inputs. [Clock target](https://github.com/lowRISC/opentitan/blob/b2d662157db2802bde0e40dfc96fd0316042d116/hw/top_earlgrey/syn/chip_earlgrey_asic.sdc) · [Clock factor](https://github.com/lowRISC/opentitan/blob/b2d662157db2802bde0e40dfc96fd0316042d116/hw/top_earlgrey/syn/ot.sdc_setup.tcl)

~~~bash
autonomous-eda run configs/opentitan-aes-sky130hd.toml experiments/aes-physical \
  --translated-rtl "$root/aes-translated.v" --memory-mib 3000 \
  --wall-clock-seconds 1800 --max-artifact-mib 4096
autonomous-eda report experiments/aes-physical
autonomous-eda report experiments/aes-physical --engineering
autonomous-eda verify experiments/aes-physical --profile physical --coverage --decks
~~~

The example wall-clock and artifact budgets are operator limits. The controller preserves elapsed stage time, native logs and interrupted attempts, and permits resuming with a new invocation budget. The sampled artifact limit counts logical bytes in the run tree and can be exceeded between samples. Container memory is enforced by Docker. A hard kill with no direct memory evidence remains a possible memory-exhaustion event.

Mapped synthesis produced 60,206 Sky130HD standard cells and 487,449 µm² of cell area. Detailed placement reported 550,759 µm² and roughly 40% utilization. CTS reported 1.10392 ns setup skew and −0.0109841 ns setup worst slack at its checkpoint. Final acceptance uses the finish and verification evidence. [Physical measurements and attempt receipts](../evidence/opentitan-aes/physical.json)

![OpenTitan AES Sky130HD placement checkpoint](../images/examples/opentitan-aes/placement.png)

OpenROAD rendered this placement image from the hash-verified `3_place.odb` checkpoint. The physical receipt includes the OpenDB SHA-256, renderer settings and PNG SHA-256.

## Terminal physical result

The controller completed synthesis, floorplanning, pin placement, standard-cell placement, CTS and global routing. The initial global-route report showed 4,446,663 µm of wire and zero overflow. The CTS log recorded 770 clock buffers across the two clock inputs. The final global-route checkpoint has +0.0053717 ns setup worst slack and +0.344325 ns hold worst slack using estimated parasitics. The final setup and hold gates require extracted timing evidence.

Detailed routing expired at its 900-second limit with exit 124. The last native progress row showed 30% of the first optimization iteration and 15,973 interim violations. The run never reached finish, parasitic extraction, GDS stream-out or independent reopening. The reevaluated physical profile passed synthesis and artifact integrity. It blocked final timing, coverage, router, DRC, LVS and reopening, and recorded failed implementation completion and acceptance. The pinned AES block stays at **Level 2**. [Terminal attempt and gate receipt](../evidence/opentitan-aes/physical.json) · [Studio diagnostic screenshot](../images/studio/aes-diagnostic.png)

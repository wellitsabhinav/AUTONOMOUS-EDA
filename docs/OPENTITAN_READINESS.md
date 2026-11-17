# OpenTitan and macro readiness

## Pinned Earl Grey source discovery

The native FuseSoC synthesis setup for `chip_earlgrey_asic` completed at `b2d662157db2802bde0e40dfc96fd0316042d116`. Its EDAM lists 706 files and 257 core dependency nodes. The inventory identifies 30 files in generated paths or primitive-generator cores. It also identifies 18 auxiliary C, C++ or unspecified entries. The normalized TopGen configuration describes 43 module instances with clock sources, reset connections, memory configuration and address assignments where declared. This is **Level 0** readiness for the SoC and a partition planning input. [Native inventory and partition boundaries](evidence/earlgrey/readiness.json) · [Pinned top configuration](https://github.com/lowRISC/opentitan/blob/b2d662157db2802bde0e40dfc96fd0316042d116/hw/top_earlgrey/data/top_earlgrey.hjson)

| Partition or dependency | Available evidence | Blocker and next qualification |
| --- | --- | --- |
| AES | Default source configuration elaborated and mapped. Verified global route was reused in a bounded retry. | Detailed route hit the 3,000 MiB cap with confirmed OOM. Final gates, interface timing, DV and equivalence remain open. |
| SRAM controllers, ROM, flash and OTP | Native module boundaries, clock/reset connections and memory settings | Bind qualified logical, Liberty, LEF, GDS and power views to each exact configuration. Generic models support source discovery. Substitution would change electrical and timing evidence. |
| Clock and reset managers | TopGen source and reset maps, including main, AON, divided IO and USB paths | Trace generated clocks, reset-domain behavior and timing constraints through the selected partitions. |
| Analog sensor top and pads | Native interface and clock/reset inventory | Require technology-qualified analog, IO, power and extraction views before top-level timing, electrical checks and signoff. |
| Native verification | EDAM includes auxiliary DV and DPI sources | Run the upstream verification toolchain and map functional results to exact configurations. The HDL ingestion adapter rejects mixed build entries it cannot execute. |

The [AES walkthrough](tutorials/OPENTITAN_AES.md#readiness-and-remaining-gates) defines all seven readiness levels and records the distinct **Level 2** result for the isolated block.

## Independent core qualification

`autonomous-eda readiness qualification RUN` evaluates implementation, physical
acceptance and design qualification separately. The implementation result checks
completed route and retained final OpenDB, netlist, SDC, SPEF and GDS. Physical
acceptance also requires extracted timing, evaluated coverage, router and platform
DRC, LVS, GDS reopening and hash integrity. A verified route can still fail setup.

The reader preserves Levels 0–2. Level 3 requires a verified core layout and final
evaluation, including affirmative extraction evidence from the native OpenRCX log
and SPEF. Level 4 adds the full interface timing contract, peripheral DV and
translated-RTL equivalence. The existing Levels 5–6 describe hierarchical and full-chip
qualification. An incomplete route blocks downstream verification.

The Milestone 12 AES receipt adds independent cipher-core, peripheral,
structural and differential evidence. The blocked formal and timing obligations
remain visible alongside the physical result in CLI, Studio, and MCP.
[Milestone 12 evaluation](MILESTONE_12_EVALUATION.md)

Milestone 12B verifies the generated Earl Grey AES clock and reset nets. Both
clock ports connect to `clk_main_aes`. Primary and EDN use the same reset net,
and the shadow registers use the corresponding shadow reset. Source-bound edge traces cover independently
driven clocks, sideload and alert/error behavior. The documented CDC and full
interface timing blockers remain. [Milestone 12B evaluation](MILESTONE_12B_EVALUATION.md)

The isolated AES SDC treats `clk_i` and `clk_edn_i` as one experimental 8.5 ns
clock. The block has `rst_ni`, `rst_shadowed_ni` and `rst_edn_ni` reset inputs.
Its isolated constraint evidence does not qualify EDN clock relationships, reset and
CDC assumptions, clock uncertainty or port delay constraints for TL-UL, key manager,
alerts and EDN traffic. The independent OpenSTA coverage probe reports the checks
it actually evaluated against the pinned final SDC and SPEF. [Milestone evaluation](MILESTONE_11_EVALUATION.md)

### Reproduce the source and partition inventory

Use the native Linux checkout and FuseSoC environment from the [AES tutorial](tutorials/OPENTITAN_AES.md#reproduce-the-ordered-manifest). The ASIC synthesis setup generates EDAM metadata. Read the normalized metadata with AUTONOMOUS-EDA on Linux or Windows.

~~~bash
python -m pip install hjson==3.1.0
(cd "$root/earlgrey" && PYTHONHASHSEED=0 fusesoc --cores-root=. run \
  --target=syn --tool=icarus --flag fileset_tapeout --setup \
  --build-root="$root/earlgrey-asic" lowrisc:systems:chip_earlgrey_asic)
edalize="$root/earlgrey-asic/syn-icarus/lowrisc_systems_chip_earlgrey_asic_0.1.eda.yml"
python -c 'import json,sys,yaml; json.dump(yaml.safe_load(sys.stdin),sys.stdout,indent=2)' \
  <"$edalize" >"$root/earlgrey-edam.json"
python -c 'import json,sys,hjson; json.dump(hjson.load(sys.stdin),sys.stdout,indent=2)' \
  <"$root/earlgrey/hw/top_earlgrey/data/top_earlgrey.hjson" >"$root/earlgrey-topgen.json"
PYTHONPATH="$repo/src" python -m autonomous_eda readiness partitions \
  "$root/earlgrey-topgen.json" "$root/earlgrey-edam.json" --revision "$revision"
~~~

## DFFRAM hard macro probe

The pinned Sky130HD Chameleon configuration provides a physical `DFFRAM_4K` macro implemented with register cells. The explicit [view manifest](evidence/earlgrey/dffram.toml) declares a 32-bit, 1,024-word register file, the hard-macro policy, `CLK` input, `VPWR` and `VGND`, the typical timing corner, and the requested R0 orientation. The macro inventory verifies native ports and geometry while retaining blocked qualification.

| View or check | Outcome |
| --- | --- |
| Verilog port inventory | Yosys read the logical black-box interface and expanded 80 signal bits. |
| LEF and signal mapping | OpenROAD matched the master, signal names and directions, plus both supply pins. |
| GDS identity and geometry | KLayout independently reopened `DFFRAM_4K` and measured 1,089.845 × 1,389.360 µm, matching LEF extent. |
| Liberty and corner | Missing matching `tt_025C_1v80` Liberty in the pinned Chameleon collateral. |
| Physical and timing signoff | Blocked pending characterized timing, GDS pin geometry, orientation qualification and LVS. |

[Native macro readback and source hashes](evidence/earlgrey/dffram-readiness.json) · [Pinned Chameleon configuration](https://github.com/The-OpenROAD-Project/OpenROAD-flow-scripts/tree/23341145f0fc92a522de26d5244a4a0013818f97/flow/designs/sky130hd/chameleon)

### Reproduce the native macro checks

Prepare the pinned Docker image as described in the [environment guide](ENVIRONMENT_DOCTOR.md). Use a new experiment directory.

~~~bash
out="$PWD/experiments/dffram-views"
mkdir "$out"
image=openroad/orfs@sha256:8eff077230b86729f6248b34eeab370e486fa4052d34c60d9067e3e4aa720987
docker run --rm --pull=never --network=none --user "$(id -u):$(id -g)" \
  --mount "type=bind,source=$out,target=/work" --entrypoint bash "$image" -ec '
  cd /OpenROAD-flow-scripts/flow/designs
  cp sky130hd/chameleon/lef/DFFRAM_4K.lef /work/
  cp src/chameleon/IPs/DFFRAM_4K.v /work/
  gzip -cd sky130hd/chameleon/gds/DFFRAM_4K.gds.gz >/work/DFFRAM_4K.gds
'
cp docs/evidence/earlgrey/dffram.toml "$out/"
autonomous-eda readiness macro "$out/dffram.toml" experiments/dffram-readback
~~~

The macro reader runs on Linux or WSL2. It stages ordinary manifest-relative inputs, retains native logs and hashes, and emits a report even when a required view is missing. Liberty files can be inventoried. A qualified corner and electrical consistency need a separate timing and LVS evaluation.

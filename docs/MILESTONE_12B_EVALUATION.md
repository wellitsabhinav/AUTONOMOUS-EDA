# Milestone 12B OpenTitan AES interface closure

The validated starting commit is `15bb972485993fa6098a3a23789d14c72024b5d2`.
It merged PR #42 and passed all four main CI jobs. The engineering implementation
commit is `6b6d0f39f4b3431f29f4c1de44bc27a4629d98c0`.
[Delivery PR #45](https://github.com/wellitsabhinav/AUTONOMOUS-EDA/pull/45) records the final delivery SHA.
[Tracking issue #43](https://github.com/wellitsabhinav/AUTONOMOUS-EDA/issues/43) ·
[OpenTitan prerequisite #11](https://github.com/wellitsabhinav/AUTONOMOUS-EDA/issues/11) ·
[Starting CI](https://github.com/wellitsabhinav/AUTONOMOUS-EDA/actions/runs/44044670089)

## Verified baseline

The Earl Grey AES revision is `b2d662157db2802bde0e40dfc96fd0316042d116`.
The FuseSoC peripheral synthesis manifest retains 226 ordered sources and 11
generated primitives. This configuration enables AES-192, masking and DOM S-boxes.
Milestone 12 measured 3,297 upstream cipher-core cycles, a masked peripheral smoke
pass, matching directions and widths on all 14 ports, and 626 matching differential
cycles. The mapped Sky130HD pilot has 60,206 standard cells and reached global routing.
Its estimated global-route setup and hold slack were +0.0053717 ns and +0.344325 ns.
The original 900 s timeout, confirmed 3,000 MiB OOM, and separate 1,000 s bounded
timeout remain available. [Milestone 12 evaluation](MILESTONE_12_EVALUATION.md) ·
[Physical pilot](evidence/opentitan-aes/physical.json) ·
[Bounded route attempt](evidence/opentitan-aes/bounded-route.json)

## Interface timing and clock/reset review

TopGen names `main` for both clocks. The generated Earl Grey `u_aes` instance
connects both inputs to `clkmgr_aon_clocks.clk_main_aes`. It ties the primary and EDN
resets to `rst_lc_n[Domain0Sel]` and connects the shadow reset to
`rst_lc_shadowed_n[Domain0Sel]`. Key-manager sideload, EDN channel 5, TL-UL, alert
channels 42–43, life-cycle escalation, and idle indication are explicitly mapped.
The matrix binds TopGen, generated integration RTL, the upstream SDC and setup Tcl,
the native EDAM and all resolved inputs, the sv2v derivative, and the mapped context.
[Pinned generated integration](https://github.com/lowRISC/opentitan/blob/b2d662157db2802bde0e40dfc96fd0316042d116/hw/top_earlgrey/rtl/autogen/top_earlgrey.sv#L2227-L2260)

| Obligation | Classification | Evidence and outstanding work |
| --- | --- | --- |
| Source and nominal frequency | `UPSTREAM_DERIVED` | 100 MHz, 10 ns and a common generated net. Clock tree phase and skew need implementation evidence. |
| Test synthesis period | `UPSTREAM_DERIVED` | Earl Grey's 10 ns target × 0.85 setup factor gives 8.5 ns. |
| Top-level uncertainty | `UPSTREAM_DERIVED` | ASIC test synthesis uses 0.05 ns in `pt_shell`, 0.5 ns otherwise. Block setup and hold allocations remain unresolved. |
| Isolated mapped clock | `EXPERIMENTAL` | OpenSTA observed one ideal 8.5 ns clock over the two pinned SDC port references. |
| Cell timing and capacitance | `TECHNOLOGY_DERIVED` | Pinned Sky130HD `tt_025C_1v80` Liberty, ns and pF. Boundary transition and load require integration values. |
| TL-UL, key manager, EDN, alerts, life cycle and idle | `UNRESOLVED` | Per-port direction, width, reference clock and peer are bound. Minimum and maximum budgets require peer timing. |
| Resets | `UNRESOLVED` | The three nets are mapped. Release, recovery/removal and physical skew require qualification. |
| Exceptions and path groups | `UNRESOLVED` | The pinned block SDC has empty exception and explicit path-group lists. Review crossing and reset intent before assigning them. |

OpenSTA in the pinned ORFS image read the synthesis ODB and rehashed the actual
Liberty. All 14 logical port widths matched the mapped port inventory. The
clock, multiple-clock, generated-clock and loop checks completed. The clock-only
SDC left 80 unconstrained endpoints, 671 uncovered inputs and 68 uncovered outputs.
The diagnostic trial applied both min/max TL-UL delays and separate setup/hold
uncertainty on the same mapped checkpoint. It left 21 unconstrained endpoints,
562 uncovered inputs and 9 uncovered outputs. Mapped, cell-delay-only setup and
hold slack were −0.379158 ns and +0.331210 ns on the clock-only constraint,
and −1.931299 ns and +0.231210 ns with the selected diagnostic numbers.
Global-route slack remains an estimated-parasitic
observation. Extracted setup and hold, clock skew, recovery/removal, electrical
checks and physical correlation require the final views.
[Clock-only receipt](evidence/opentitan-aes/m12b/contract.json) ·
[Diagnostic receipt](evidence/opentitan-aes/m12b/diagnostic-contract.json)

The clock/reset inventory verifies the EDN request/acknowledge two-flop instances,
the bundled entropy return with `DataReg=0`, the alert ping/ack decoders, and the
life-cycle synchronizer. The selected EDN handshake assumes coordinated resets.
The staggered startup case releases the supporting domains before the primary
domain. CDC signoff requires a qualified tool, numerical bundled-data and differential
skew checks, reset release and reconvergence analysis, and integration-specific STA.

## Functional verification and correspondence

The Verilator harness drives genuine TL-UL requests with the upstream integrity
functions, verifies echoed source/size and stable stalled responses, and checks
`INPUT_READY`, `OUTPUT_VALID`, result delivery, and consumption. The independent
scoreboard uses the FIPS 197 original Appendix C vectors, plus the AES-128 all-zero
key/block known answer. The bus stores each vector in little-endian register words.
Seeded waits vary TL-UL launch and response backpressure as well as EDN timing and
entropy words. [NIST FIPS 197](https://doi.org/10.6028/NIST.FIPS.197)

| Profile, seed 41 | Core / EDN period and phase offsets (ns) | Core / EDN edges per model | TL transactions | EDN transfers / wait edges | Both models |
| --- | --- | ---: | ---: | ---: | --- |
| Common | 10 / 10, 0 / 0 | 647 / 647 | 158 | 32 / 0 | PASS |
| Phase offset | 10 / 10, 0 / 3 | 743 / 742 | 182 | 32 / 128 | PASS |
| Independent | 8 / 14, 1 / 2 | 1,578 / 901 | 215 | 32 / 406 | PASS |
| Sideload and active reset | 10 / 14, 0 / 2 | 2,492 / 1,780 | 408 | 88 / 176 | PASS |
| Alerts and errors | 10 / 12, 0 / 1 | 1,028 / 856 | 244 | 48 / 0 | PASS |

Sideload drives the real 513-bit key-manager request with two shares. It verifies
AES-128 and AES-192 encryption, AES-256 encryption and decryption, invalid-key
gating, the retained loaded key while the pin value changes, and adoption of a new
key on a committed control update. Invalid operation/key-length encodings sanitize
to encrypt/AES-256. Reset is asserted during a busy operation and followed by an
independent software-key known-answer operation. This covers the selected ECB
configuration and the observed reset behavior of the tested transaction.

The error profile checks the TL-UL response for an unmapped CSR, a shadowed update
mismatch with recoverable status and differential ACK, the fatal `ALERT_TEST`
pair and ACK, and a bad TL command integrity bit with a bus error, latched fatal
status, differential handshake, and post-reset decryption. Internal fault campaigns,
zeroization properties, ping liveness and upstream DV signoff remain open.

The differential runner timestamps both clock schedules, compares core and EDN
edge traces including bus and alert handshakes, and records the first differing
edge with both model values if traces diverge. All 11,414 sampled edges agreed
across 6,488 core and 4,926 EDN edges. Both native interfaces matched all 14
ports. The original and translated compilations took 129.007 s and 123.378 s.
Verilator uses two-state semantics.
The raw translation still hits Verilator's negative-width extract diagnostic.
Yosys reads the byte-identical sv2v translation, checks hierarchy and emits the
behavioral model used for the comparison. Physical CDC timing and four-state
initialization remain unverified. [Peripheral and correspondence receipt](evidence/opentitan-aes/m12b/peripheral.json)

The first native run passed the original RTL and failed to compile the translated
model after the raw-reader error because its container PATH lacked Yosys. The next
run used the image's pinned Yosys executable and a new directory.
[Preserved negative attempt receipt](evidence/opentitan-aes/m12b/negative-attempts.json)

Yosys `0.68+post` also proved bounded correspondence of `aes_reg_status`, the
authentic 8-bit IV and 16-bit key tracking instances. Both queries ran to depth
8 and returned exit 0 in 3.819 s and 3.970 s under 20 s solver limits. Each model
sees one active-low reset step before comparisons begin, then arbitrary binary
register-write, arm, use, clear and reset inputs. Shared abstract clock steps and
`async2sync` bound the reset semantics. The partition has no entropy input.
The proof checks `new_o`, `new_pulse_o`, and `clean_o`. The masked cipher state,
EDN phase and entropy assumptions, multi-domain reset and X/Z correspondence
need their own proof setup. Full peripheral sequential equivalence remains blocked.

## Physical admission

The planner rehashed five upstream ODB/SDC checkpoint pairs and retained both
original route attempts in the source run. It observed 3,536,183,296 B available
memory, four CPUs and 169,937,907,712 B of destination filesystem space. After the
8% reserve, the available cap was 3,122 MiB. The 3,240 MiB exploratory allocation,
2,700 s stage deadline, 3,700 s wall deadline and 2,048 MiB artifact budget were
evaluated against the confirmed 3,000 MiB OOM. The planner returned
`INSUFFICIENT_RESOURCES`. Detailed routing stayed idle and readiness remains Level 2.
The exploratory cap is derived from incomplete attempts.
[Resource admission receipt](evidence/opentitan-aes/m12b/resources.json)

## Independent acceptance

| Gate | Outcome | Native result or blocker |
| --- | --- | --- |
| Cipher core | PASS | Retained Milestone 12 upstream 3,297-cycle receipt |
| Interface contract | BLOCKED | Production min/max budgets, uncertainty, electrical limits and exceptions |
| Pre-route STA probe | PASS | 8.5 ns, verified mapped inventory and Liberty, measured coverage gaps |
| Clock/reset structural review | PASS | Pinned generated nets and elaborated synchronizer inventory |
| Formal CDC qualification | BLOCKED | Qualified crossing, timing and reset analysis pending |
| Multi-clock functional verification | PASS | Common, phased and independent profiles, measured EDN response waits |
| Sideload | PASS | AES-128/192/256 known answers, availability, update and active reset |
| Alert and error verification | PASS | Illegal bus access, shadow mismatch, test alert, real integrity fault and recovery |
| RTL correspondence | PASS | Matching 14-port inventories and 11,414 trace edges |
| Bounded partition proof | PASS | Both native SAT queries at depth 8 |
| Full peripheral sequential equivalence | BLOCKED | Full-state correspondence and model assumptions pending |
| Resource admission | BLOCKED | 3,122 MiB available after reserve versus 3,240 MiB exploratory cap |
| Implementation completion | FAILED | Latest detailed-route attempt hit confirmed OOM |
| Physical acceptance | BLOCKED | Route, final OpenDB/netlist/SDC, extraction and SPEF, extracted STA, router and platform DRC, LVS, GDS and reopening |
| Design qualification | BLOCKED | Interface timing, full DV and equivalence, and physical acceptance |
| Readiness | Level 2 | Technology mapping and global route |

[Bound qualification summary](evidence/opentitan-aes/m12b/aes-qualification.json)

## Readback and validation

The CLI read the bound receipt on a copy of the physical attempt. The real MCP
SDK stdio worker returned all five scenario statuses. Headless Edge 157 loaded
the Studio Resources panel, verified the visible AES gates and the case inventory,
and retained a 390 px document width on the 390 px mobile viewport. The browser
artifacts stay in the isolated experiment. [Readback receipt](evidence/opentitan-aes/m12b/readback.json) ·
[Browser receipt](evidence/opentitan-aes/m12b/studio-browser.json)

## Reproduce

Prepare the pinned source export, normalized EDAM and TopGen, upstream checkout,
physical source run, and simulator packages using the [Milestone 12 procedure](MILESTONE_12_EVALUATION.md#reproduce).
Use fresh output paths with the pinned Linux toolchain. The native regressions
used the pinned ORFS container, two CPUs, 2,900 MiB RAM, zero additional swap and
Verilator `5.020` from the cached Debian package. Add the image's
`/OpenROAD-flow-scripts/tools/install/yosys/bin` directory and the extracted
Verilator `usr/bin` to `PATH` for peripheral DV.

```bash
run=experiments/m11b-aes-route-01
evidence=docs/evidence/opentitan-aes
work=$(mktemp -d)

autonomous-eda readiness aes-contract "$run" \
  --source-manifest "$source_manifest" --edam "$edam" --resolved "$evidence/resolved.json" \
  --inspection "$evidence/inspect.json" --topgen "$topgen" --upstream "$upstream" \
  --native-output "$work/sta" --timeout 90 > "$work/contract.json"

cat > "$work/budget.json" <<'JSON'
{"inputs":{"tl_i":{"min":0.25,"max":1.5}},"outputs":{"tl_o":{"min":0.25,"max":1.5}},"uncertainty_ns":{"setup":0.25,"hold":0.10}}
JSON
autonomous-eda readiness aes-contract "$run" \
  --source-manifest "$source_manifest" --edam "$edam" --resolved "$evidence/resolved.json" \
  --inspection "$evidence/inspect.json" --topgen "$topgen" --upstream "$upstream" \
  --native-output "$work/diagnostic-sta" --diagnostic-budget "$work/budget.json" \
  --timeout 90 > "$work/diagnostic-contract.json"

autonomous-eda readiness aes-peripheral "$run" \
  --source-manifest "$source_manifest" --edam "$edam" --resolved "$evidence/resolved.json" \
  --inspection "$evidence/inspect.json" --testbench examples/opentitan-aes/peripheral_tb.sv \
  --multi-clock --seed 41 --timeout 270 --output "$work/peripheral" > "$work/peripheral.json"

autonomous-eda resources plan "$run" --memory-mib 3240 --stage-seconds 2700 \
  --wall-seconds 3700 --artifact-mib 2048 --margin 0.08 --policy bounded-trial \
  > "$work/resources.json"

autonomous-eda readiness aes-record "$run" --contract "$work/contract.json" \
  --core "$evidence/qualification/cipher-core.json" --peripheral "$work/peripheral.json" \
  --resource-plan "$work/resources.json" > "$work/aes-qualification.json"
```

Copy the resulting `aes-qualification.json` to `verification/` in a readback copy
of the same run, then query `autonomous-eda readiness qualification`, the MCP
`get_aes_qualification` tool on an allowlisted copy, or the Studio Resources panel.
The separate `get_acceptance_summary` MCP tool reads the full controller profile.
Verilator logs and model outputs stay in the native attempt directories.

If a larger host returns `execution_allowed: true`, launch into a new experiment
directory. The controller rechecks admission and all upstream identities before it
seeds the verified global-route checkpoint. Only a completed implementation
can proceed to extracted checks.

```bash
retry=experiments/aes-route-3240mib-01
autonomous-eda resources retry "$run" "$retry" --memory-mib 3240 \
  --stage-seconds 2700 --wall-seconds 3700 --artifact-mib 2048 \
  --margin 0.08 --policy bounded-trial
autonomous-eda verify "$retry" --profile physical --coverage --decks
autonomous-eda readiness qualification "$retry"
```

## Milestone 13

Close the AES block's peer timing budgets against generated Earl Grey integration
and matching timing views. Qualify bundled-data and reset crossings, and partition
the peripheral proof beyond the key and IV tracking registers. On an admitted
host, complete the isolated routing and evaluate extracted physical gates before
assessing higher readiness.

# Milestone 12 AES qualification

The starting commit was `f193d3f479f47f4ce680f2091b7f104a8b5f61d4`, the merge
of the bounded-route telemetry prerequisite. All four main CI jobs passed.
The engineering implementation commit is `4d420d5a7bc30e329fe02eabdf1eb4564c790c44`.
[Tracking issue #41](https://github.com/wellitsabhinav/AUTONOMOUS-EDA/issues/41) ·
[Prerequisite #40](https://github.com/wellitsabhinav/AUTONOMOUS-EDA/pull/40) ·
[Starting CI](https://github.com/wellitsabhinav/AUTONOMOUS-EDA/actions/runs/44040349132)

## Verified baseline

OpenTitan Earl Grey AES at `b2d662157db2802bde0e40dfc96fd0316042d116` resolves
226 ordered sources and 11 generated primitives. Synthesis mapped 60,206
Sky130HD standard cells. Floorplan, placement, CTS and global route completed.
The 8.5 ns experiment estimated +0.0053717 ns setup and +0.344325 ns hold slack
using global-route parasitics. The audit matched all 29 physical pilot hashes
and the staged inputs, replay checkpoints and terminal logs. The pilot timed
out after 900.839 s. A 3,000 MiB retry hit a confirmed OOM after 2,255.853 s.
A separate 3,000 MiB trial timed out after 1,001.181 s with a 3,091,898,368 B
cgroup high-water mark and Docker `OOMKilled=false`.
[Physical pilot](evidence/opentitan-aes/physical.json) ·
[Route follow-up](evidence/opentitan-aes/bounded-route.json)

## Timing contract

The controller binds the EDAM digest and every resolved source and generated
input hash to the pinned configuration, native Yosys interface inventory,
upstream TopGen HJSON and ASIC SDC, and the experimental block SDC. The 14 ports
cover the two clock inputs, three resets, TL-UL, key manager, EDN, alert,
life-cycle escalation and idle. This configuration has no interrupt ports.
TopGen names the same nominal 100 MHz `main` source for both AES clock inputs
and `lc` for the primary and EDN reset connections. The isolated experiment uses
a single ideal 8.5 ns clock over both inputs.

OpenSTA in the pinned ORFS build parsed the SDC on the verified mapped synthesis
checkpoint and found one clock named `main`. Native endpoint, input-delay and
output-delay coverage checks failed. Block coverage remains **BLOCKED** on
clock relationships and uncertainty, min/max TL-UL and other I/O timing,
transitions and loads, shadow reset mapping and CDC, path groups, and exception
review. No timing exceptions or port delays were added to the physical SDC.
[Contract and native pre-route receipt](evidence/opentitan-aes/qualification/contract.json)

## Functional verification and correspondence

Both simulations used Verilator 5.020 (Debian 5.020-1), seed 41 and a pinned
ORFS container with two CPUs, 2,900 MiB RAM and zero additional swap. The
cipher-core FuseSoC test rebuilt in 86.466 s and ran in 0.856 s. It recorded
3,297 cycles, both upstream round-trip success markers and exit 0. The first
source setup failed because its generator resolved a Python without Mako.
A successful early build exposed the double-space cycle marker missing from
the first parser. The final run used a fresh directory and retained both
earlier attempts. [Cipher-core receipt](evidence/opentitan-aes/qualification/cipher-core.json)
[Negative attempt receipts](evidence/opentitan-aes/qualification/negative-attempts.json)

The independent peripheral harness uses OpenTitan's register, TL-UL and
integrity definitions. It checks shadowed control writes, TL-UL backpressure,
automatic AES-128 ECB encrypt and decrypt using the FIPS 197 example, output
valid and consumption, resets, an unmapped-address error, and deterministic
EDN handshakes. The pinned AES peripheral remains masked. Key-manager sideload
is inactive and both clock ports use a common test clock. The upstream UVM
environment adds a DPI AES model, coverage, and simulator-specific integration.
Its selected signoff tool is Xcelium, and the FuseSoC DV target defaults to VCS.
The native test used the available Verilator toolchain.

Original SystemVerilog matched all 14 directions and widths from the Yosys
inventory. Verilator rejected an unsupported zero-width part select in the
raw sv2v output. The retained comparison used the exact pinned translation
as input to Yosys 0.68+post `hierarchy -check`, `proc` and `check -assert`, then
simulated its written Verilog. Both model interfaces matched and both
peripheral oracles passed. All **626** observed cycles matched, with a trace
digest of `090556890bbc3bfd7e6e15ec274d9a8c10d96706b6199a0a5964144b1cc66e32`.
The original and translated-model compilations took 68.52 s and 56.36 s.
Yosys took 10.71 s to elaborate and write the simulation model.
This two-state test compares observable fields under a constrained reset,
clock and EDN schedule. Full sequential equivalence remains **BLOCKED**.
The existing combinational SAT profile provides no proof for the masked
dual-clock peripheral. A sequential proof needs independently qualified
SystemVerilog elaboration and an explicit reset, CDC, entropy and X/Z model.
[Peripheral, direct-read failure and correspondence receipt](evidence/opentitan-aes/qualification/peripheral.json)

## Physical admission

A read-only plan on the WSL2 EDA host rehashed all five upstream checkpoints.
Four CPUs were visible. The planner observed 3,494,064,128 B available RAM,
172,937,388,032 B destination free space, and 788,927,147 B retained run size.
With an 8% reserve, it admitted at most 3,085 MiB. The proposed 3,240 MiB
exploration with a 2,700 s stage deadline, 3,700 s total deadline and
2,048 MiB artifact cap was refused. Its lower memory bound is derived from
incomplete runs. No route dispatch occurred. A larger Linux or WSL2 host can
repeat the same read-only plan against the retained failed run and use an
isolated retry directory if the preflight admits a finite same-configuration trial.
[Resource plan](evidence/opentitan-aes/qualification/resources.json)

| Gate | Outcome | Evidence |
| --- | --- | --- |
| Upstream cipher core | PASS | Fresh Verilator build and 3,297-cycle run |
| Peripheral functional regression | PASS | Independent bounded TL-UL test |
| Structural correspondence | PASS | 14 matching ports and native hierarchy checks |
| Differential regression | PASS | 626 cycles on original and Yosys-elaborated translated models |
| Full sequential equivalence | BLOCKED | Qualified proof setup missing |
| Experimental SDC syntax and clock inventory | PASS | Pre-route OpenSTA on mapped synthesis |
| Peripheral timing contract | BLOCKED | Interface obligations and native coverage findings |
| Resource admission | BLOCKED | 3,085 MiB host limit under the reserve |
| Implementation completion | FAIL | Retained detailed-route OOM |
| Physical acceptance | BLOCKED | Final products, extraction, STA, DRC, LVS and GDS reopening pending |
| Design qualification | BLOCKED | Independent downstream and interface gates pending |
| AES readiness | Level 2 | Verified mapping and global route |

## Reproduce

Use the pinned export and tools from the [AES walkthrough](tutorials/OPENTITAN_AES.md).
Use a fresh work directory for generated files. Set `source_manifest` to the
regenerated manifest, `edam` to its normalized EDAM JSON, `topgen` to the
normalized Earl Grey TopGen JSON and `upstream` to the Linux upstream checkout.
Use the exact Linux source bytes and the [retained inspection](evidence/opentitan-aes/inspect.json).
Compute normalized JSON using `json.dump(..., indent=2)` as in the walkthrough.

```bash
run=experiments/m11b-aes-route-01
evidence=docs/evidence/opentitan-aes

autonomous-eda readiness aes-contract "$run" \
  --source-manifest "$source_manifest" --edam "$edam" --resolved "$evidence/resolved.json" \
  --inspection "$evidence/inspect.json" --topgen "$topgen" --upstream "$upstream" \
  --native-output "$work/sta" > "$work/contract.json"

# Run the next two commands with Verilator 5.020, make and Yosys on PATH.
# For the cached package extraction, use the environment in the AES walkthrough.
autonomous-eda readiness aes-core "$dv_build" --timeout 270 > "$work/cipher-core.json"
autonomous-eda readiness aes-peripheral "$run" \
  --source-manifest "$source_manifest" --edam "$edam" --resolved "$evidence/resolved.json" \
  --inspection "$evidence/inspect.json" --testbench examples/opentitan-aes/peripheral_tb.sv \
  --timeout 270 --output "$work/peripheral" > "$work/peripheral.json"

autonomous-eda resources plan "$run" --memory-mib 3240 --stage-seconds 2700 \
  --wall-seconds 3700 --artifact-mib 2048 --margin 0.08 --policy bounded-trial \
  > "$work/resources.json"
autonomous-eda readiness aes-record "$run" --contract "$work/contract.json" \
  --core "$work/cipher-core.json" --peripheral "$work/peripheral.json" \
  --resource-plan "$work/resources.json" > "$work/aes-qualification.json"
```

For the cipher-core command, prepare a new `default-verilator` directory with
the upstream [FuseSoC setup](tutorials/OPENTITAN_AES.md#run-the-upstream-cipher-core-testbench).
Generated models and simulation outputs stay in their native attempt directories.
The compact receipts retain input, tool, command, output and log hashes.
Place the summary at `verification/aes-qualification.json` in a readback copy
of the corresponding physical run. `readiness qualification`, the Studio
Resources tab, and MCP `get_aes_qualification` read that receipt. The run ID,
translated RTL and block SDC are verified before the report is exposed.
[Bound summary](evidence/opentitan-aes/qualification/aes-qualification.json)

## Readback and remaining work

Ruff, formatting, strict mypy, dependency consistency, and 30 focused
qualification, documentation and resource tests passed locally.
The read-only MCP worker returned the bound receipt for the allowlisted AES
run. Headless Edge 157 opened the actual Studio Resources qualification
panel from that run and verified the native pass and blocked gate labels.
The 390 px mobile view retained a 390 px document width.
[Browser receipt](evidence/opentitan-aes/qualification/studio-browser.json)

For Milestone 12B, qualify the interface budgets against the integration
constraints and extend DV to independently driven EDN and peripheral clocks,
alerts, sideload and randomized traffic. On a resource-admitted host, resume
a verified global-route checkpoint and evaluate the routed database,
extraction, setup and hold, coverage, native and platform DRC, LVS, independent
GDSII reopening, and output hashes. AES remains at Level 2 until the existing
qualification rules support a higher level.

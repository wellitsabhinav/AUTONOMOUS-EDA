# Milestone 10 engineering evaluation

The audit began on `5ca213d`, whose three Python CI jobs failed after the ICeWall merge. [PR #28](https://github.com/wellitsabhinav/AUTONOMOUS-EDA/pull/28) restored the displaced documentation checks. Milestone 10A began at the repaired, green baseline `e2c4155`. The source, physical, Studio and readiness changes landed in [PR #29](https://github.com/wellitsabhinav/AUTONOMOUS-EDA/pull/29), [PR #30](https://github.com/wellitsabhinav/AUTONOMOUS-EDA/pull/30), [PR #31](https://github.com/wellitsabhinav/AUTONOMOUS-EDA/pull/31) and [PR #32](https://github.com/wellitsabhinav/AUTONOMOUS-EDA/pull/32). The [tracking issue](https://github.com/wellitsabhinav/AUTONOMOUS-EDA/issues/11) remains open.

## Ingestion and architecture

The FuseSoC adapter emits an ordered RTL manifest and a hash-bound dependency receipt. The RTL frontend stages those inputs and publishes netlist evidence. The deterministic controller owns ORFS execution and recovery. Independent verification reads bounded evidence snapshots. Studio and reporting consume those observations, and the supervisor retains its read-only role. [Architecture](ARCHITECTURE.md)

OpenTitan AES at `b2d662157db2802bde0e40dfc96fd0316042d116` exercised real package order, generated wrappers, clock/reset integration and a masked datapath. The [selection matrix and reproduction](tutorials/OPENTITAN_AES.md) compare the native OpenTitan target with the bundled ORFS alternatives. Two seeded FuseSoC outputs matched across 226 ordered sources and 11 generated primitives. The retained unseeded runs showed a generated enum-order difference. The pinned Yosys build elaborated 174 module definitions and 2,172 hierarchical instances, then generated 56,622 generic cells, including 6,016 flip-flops. [Source order, hierarchy and tool receipts](evidence/opentitan-aes/receipt.json)

## Measured physical runs

| Measurement or gate | GCD recovery run | OpenTitan AES pilot |
| --- | --- | --- |
| Implementation scope | Seven pinned Sky130HD stages completed after an interrupted placement attempt | Mapped synthesis through global route, detailed-route timeout at 900 s |
| Constrained clock | 1.1 ns | Experimental 8.5 ns, derived from the Earl Grey clock target and factor |
| Mapped standard cells and synthesis area | Retained tool reports | 60,206 cells and 487,449 µm² |
| Placement utilization | Retained tool reports | About 40% |
| CTS | Retained checkpoint | 770 native clock buffers, 1.10392 ns setup skew |
| Global route | Completed | 4,446,663 µm initial guide wirelength and zero reported initial overflow |
| Worst setup slack | −1.20488 ns at finish | +0.0053717 ns at global route, with estimated parasitics |
| Hold | Passed at finish | +0.344325 ns worst slack at global route, final check blocked |
| Final timing coverage | Passed | Blocked |
| Platform DRC, LVS and GDS reopening | All passed | Blocked, since finish produced no GDS |
| Artifact integrity | Passed | Passed on reevaluation with the staged inputs included |
| Acceptance | Failed setup | Failed implementation completion, final verification blocked |

The [AES physical receipt](evidence/opentitan-aes/physical.json) includes per-stage wall time, available native process RSS, checkpoint hashes, the failed routing log receipt and the independent gate matrix. The last detailed-routing progress row reported 15,973 interim violations. The stage left no final detailed-router or deck result. The [GCD recovery receipt](evidence/gcd-recovery/recovery.json) records 147.999 s of successful stage time, the two reused upstream attempts and the retained SIGTERM attempt.

The resource guard stopped native GCD synthesis under a five-second invocation budget and retained its log. The real process limit test passed Docker a 64-task limit. Resuming with zero retries reused the record and refused a second synthesis dispatch. Budget controls include per-run CPU, Docker RAM and stage duration plus optional invocation wall-time, sampled artifact bytes, process counts and retries. Confirmed OOM detection, multi-run scheduling and configurable retention remain pending. [Resource-limit and replay receipt](evidence/integration/verified-correction.json)

## Readiness and Studio

| Capability | Result |
| --- | --- |
| AES readiness | Level 2, based on mapped Sky130HD netlist and partial physical evidence |
| Earl Grey ASIC | Level 0, with 706 native files, 257 dependency nodes and 43 TopGen instance boundaries |
| Memory | Yosys, OpenROAD and KLayout independently read the DFFRAM logical interface, LEF and GDS. Liberty qualification remains blocked. |
| ICeWall | Existing pad placement and routed-signal evidence remain available. Chip power connectivity failed and full-chip signoff is blocked. |
| Studio 2.0 | Browser verified GCD and AES checkpoints, stage comparison, selected timing pins and instances, measurements, acceptance and execution events. |
| Agentic diagnosis | A retained OpenROAD crash produced a digest-bound renderer proposal. The integration test rejected one plan without dispatch and verified a separately approved OpenROAD rerender. Other corrections retain their manual review workflow. |

[OpenTitan and macro evidence](OPENTITAN_READINESS.md) · [ICeWall results](IO_CHIP_EVALUATION.md) · [Studio screenshots and measured browser checks](PHYSICAL_WORKSPACE.md) · [Studio screenshot provenance](images/studio/provenance.json)

## Verification and remaining work

The merged changes passed the hosted Python and documentation checks for the [baseline repair](https://github.com/wellitsabhinav/AUTONOMOUS-EDA/actions/runs/43672719591), [10A](https://github.com/wellitsabhinav/AUTONOMOUS-EDA/actions/runs/43674228746), [10B](https://github.com/wellitsabhinav/AUTONOMOUS-EDA/actions/runs/43676460685), [Studio](https://github.com/wellitsabhinav/AUTONOMOUS-EDA/actions/runs/43677781152) and [10C](https://github.com/wellitsabhinav/AUTONOMOUS-EDA/actions/runs/43678926214). Fixtures exercise path handling, evidence corruption, snapshot isolation and proposal digests. The OpenTitan source resolution, Yosys elaboration and mapping, physical stages, real interruption, macro reads, image exports and browser sessions used native tools and recorded inputs.

The native Linux `rtl-formal` fixture passed Yosys elaboration and SAT equivalence plus Icarus functional regression. The installed Windows Icarus frontend could not launch its helpers from a path containing spaces. The pinned OpenTitan masked cipher-core testbench passed encrypt/decrypt round trips in 3,297 native Verilator cycles. [Native RTL regression receipt](evidence/earlgrey/rtl-regression.json) · [Cipher-core verification receipt](evidence/earlgrey/aes-cipher-core-dv.json)

The milestone remains open for a qualified AES block timing contract, full peripheral DV, translated-RTL equivalence, a complete detailed-route and extraction run, final GDS and independent decks, macro Liberty and LVS qualification, and a qualified hierarchical implementation. Studio still needs larger-scale incremental geometry delivery and more complete per-net timing data.

For Milestone 11, extend resource telemetry with measured container high-water marks, explicit OOM evidence, and run and stage benchmark reporting. Use that telemetry to plan a fresh AES detailed-route attempt with the same clock intent. Carry the completed result through finish and independent physical gates before ranking optimization trials or advancing the OpenTitan hierarchy.

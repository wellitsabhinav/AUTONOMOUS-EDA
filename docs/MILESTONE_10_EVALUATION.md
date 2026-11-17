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

The resource guard stopped native GCD synthesis under a five-second invocation budget and retained its log. The real process limit test passed Docker a 64-task limit. Resuming with zero retries reused the record and refused a second synthesis dispatch. Budget controls include per-run CPU, Docker RAM and stage duration plus optional invocation wall-time, sampled artifact bytes, process counts and retries. Milestone 11 added Docker-confirmed OOM detection and cgroup high-water sampling. [Resource-limit and replay receipt](evidence/integration/verified-correction.json) · [Resource telemetry](RESOURCE_TELEMETRY.md)

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

## Bounded AES route follow-up

The follow-up started from `f36e9d7` after all four main CI jobs passed. The tracking issue was open, there were no open PRs, and the merged resource-recovery PR had no inline review threads. The trial used the pinned OpenTitan AES Sky130HD configuration and its existing 8.5 ns `main` clock on `clk_i` and `clk_edn_i`. It reused the verified synthesis, floorplan, placement, CTS and global-route checkpoint chain in an isolated experiment. [Tracking issue #11](https://github.com/wellitsabhinav/AUTONOMOUS-EDA/issues/11) · [Main CI](https://github.com/wellitsabhinav/AUTONOMOUS-EDA/actions/runs/43737729825) · [Resource-recovery PR #39](https://github.com/wellitsabhinav/AUTONOMOUS-EDA/pull/39)

Using the prior OOM evidence and the recorded 8% host reserve, the planner put the exploratory completion trial at a minimum of 3,240 MiB. The WSL sample of 3,525,033,984 B available admitted at most 3,112 MiB, so that trial was blocked before dispatch. A shorter measurement trial from the original pilot used two CPUs, a 3,000 MiB container cap, zero additional swap, a 1,000 s route limit, a 1,100 s invocation limit and a 2 GiB retained-artifact budget. The planner rejected 900 s because the pilot's 900.839 s elapsed time plus the margin reached that limit. This trial measures routing within its deadline, and the budget for completion remains unknown.

### Measured outcome

| Detailed-route attempt | Limit (s) | Controller elapsed (s) | Observed cgroup high-water (B) | Terminal evidence |
| --- | ---: | ---: | ---: | --- |
| Milestone 10B pilot | 900 | 900.839 | Unknown | Controller timeout, exit 124 |
| Milestone 11 retry | 2,700 | 2,255.853 | 3,145,728,000 | `CONFIRMED_OOM`, Docker `OOMKilled=true`, exit 2 |
| Bounded follow-up | 1,000 | 1,001.181 | 3,091,898,368 | `WALL_TIMEOUT`, controller exit 124, Docker exit 137 after controller SIGKILL, `OOMKilled=false` |

The follow-up sampled Linux cgroup v2 `memory.peak` and retained 441 rows. The last sample confirmed a high-water mark of 2,948.66 MiB (98.29% of the cap) and zero observed cgroup OOM events and kills. The last verified OpenROAD progress row was iteration 0 at 40%, with 15,973 interim violations. Docker cleanup succeeded. The original 29 pilot artifacts and the prior OOM log, terminal receipt and sample journal rehashed successfully. The follow-up also retained the inherited timeout and verified all ten upstream checkpoint files against the pilot. [Follow-up receipt, hashes and gate matrix](evidence/opentitan-aes/bounded-route.json) · [Original pilot](evidence/opentitan-aes/physical.json) · [Prior OOM](evidence/resource-telemetry/aes-terminal.json)

AES remains at readiness Level 2. Detailed route failed to complete and finish produced no final products. OpenRCX extraction, extracted setup and hold, timing coverage, detailed-router and platform design rule checks, layout versus schematic and independent GDSII reopening are blocked. Physical acceptance and design qualification are blocked. [Independent core qualification](OPENTITAN_READINESS.md#independent-core-qualification)

### Reproduce the bounded trial

From the repository root in Linux or WSL2 with the pinned ORFS image cached, use the retained pilot as the source and a fresh directory as the destination. The retry rechecks the source checkpoint hashes and host headroom before dispatch.

```sh
export PYTHONPATH=src
python -m autonomous_eda resources retry experiments/m10-aes-physical-01 \
  experiments/m10-aes-route-telemetry-reproduction --stage route \
  --stage-seconds 1000 --wall-seconds 1100 --artifact-mib 2048 \
  --memory-mib 3000 --margin 0.08 --policy bounded-trial
python -m autonomous_eda resources history experiments/m10-aes-route-telemetry-01 --samples 2
python -m autonomous_eda readiness qualification experiments/m10-aes-route-telemetry-01
```

The last two commands read the recorded trial. Substitute the reproduction directory to inspect a repeat attempt.

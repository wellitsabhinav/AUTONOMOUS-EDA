# Project state

- The [README handbook workstream #44](https://github.com/wellitsabhinav/AUTONOMOUS-EDA/issues/44)
  reproduced the seven-stage Sky130HD GCD flow on the Milestone 12 baseline.
  Extracted setup failed at −1.20488 ns. Independent coverage, hold, router,
  platform DRC, LVS and GDS reopening passed. The workstream rebased after 12B
  and added its measured AES qualification evidence and CLI flags.
  [Native, rendering and CLI validation](README_OVERHAUL_EVALUATION.md)

- Milestone 12B verified five native AES scenarios on original RTL and the
  Yosys-elaborated sv2v model, with 11,414 matching core and EDN edges. Sideload,
  alert/error and bounded key/IV register-status proof passed. OpenSTA verified
  the mapped 8.5 ns context and measured missing I/O coverage. Production input
  and output budgets, formal CDC, and full sequential equivalence remain blocked.
  Host admission capped the exploratory route at 3,122 MiB, below the proposed
  3,240 MiB. The pinned AES stays at Level 2. [Measured evaluation](MILESTONE_12B_EVALUATION.md)

- Milestone 12 reproduced the pinned AES cipher-core test in 3,297 cycles and
  ran a 626-cycle TL-UL peripheral regression against original SystemVerilog
  and a Yosys-elaborated model of the byte-identical sv2v translation. The traces
  and all 14 top ports matched. OpenSTA independently parsed the experimental
  constraint and found uncovered endpoints and missing I/O delays. Formal
  equivalence and final physical gates remain blocked at readiness Level 2.
  [Evaluation and native receipts](MILESTONE_12_EVALUATION.md)

- The Milestone 10 AES follow-up reused the pinned Sky130HD checkpoint at 8.5 ns. Its
  1,000-second detailed-route trial recorded `WALL_TIMEOUT`, a 3,091,898,368 B
  cgroup high-water mark and Docker `OOMKilled=false`. It preserved the original
  timeout and the later 3,000 MiB Docker-confirmed OOM. An exploratory 3,240 MiB
  retry exceeded measured host headroom with the 8% reserve. AES remains at Level 2
  with final physical acceptance blocked. [Measured evaluation](MILESTONE_10_EVALUATION.md#bounded-aes-route-follow-up)

- The Milestone 11 follow-up added explicit memory caps for isolated retries and
  bound resume to the saved dispatch budget. A native GCD replay recovered from a
  five-second routing timeout at 768 MiB and completed at 1,024 MiB. The live AES
  preflight found no feasible cap at the recorded 8% reserve. Independent verification
  resource receipts now appear in the CLI, MCP and Studio.
  [Follow-up evaluation](MILESTONE_11_EVALUATION.md#follow-up-retry-limits-and-verification-resource-evidence)

- The Milestone 11 AES retry reused the pinned global-route checkpoint and hit the
  3,000 MiB cap with Docker-confirmed OOM after 2,255.853 s. AES remains Level 2.
  Final extraction and all dependent signoff gates are blocked. Studio and the
  benchmark retain measured resource and failure evidence. [Terminal evaluation](MILESTONE_11_EVALUATION.md#11e-terminal-aes-result-and-integrated-evidence)

- Milestone 11D added versioned run and resource queries, a paged read-only MCP
  tool and deterministic benchmark exports. The four-case control reproduced
  five PNG digests. [Benchmark evidence](MILESTONE_11_EVALUATION.md#11d-structured-reporting-and-benchmark-controls)

- Milestone 11C evaluated completed GCD as Level 3 with 482 OpenRCX-extracted nets
  and passed independent coverage, router and deck checks. The original setup
  violation keeps physical acceptance failed. [Independent qualification](MILESTONE_11_EVALUATION.md#11c-independent-physical-and-design-qualification)

- Milestone 11B verified the seeded AES inputs and pinned global-route checkpoints
  before dispatching a constrained retry in an isolated experiment. The retry retained
  the original 8.5 ns, two CPUs and 3,000 MiB. [Resource plan and launch](MILESTONE_11_EVALUATION.md#11b-aes-resource-preflight-and-native-launch)

- Milestone 11A validated the `c19929b` baseline and instrumented pinned Docker dispatch.
  A native GCD run completed all seven stages with cgroup v2 high-water observations.
  Disposable native probes confirmed OOM, controller timeout, explicit exit 137, and
  interruption recovery behavior. The preserved AES pilot still ends at the 900-second
  routing timeout. [Milestone evaluation](MILESTONE_11_EVALUATION.md) ·
  [Resource telemetry](RESOURCE_TELEMETRY.md)

- The integration test rejected one digest-bound renderer plan before dispatch and completed an approved replay on the recorded GCD floorplan. Studio displayed the saved lifecycle through verification. A five-second GCD attempt recorded a Docker process limit of 64, and zero retries prevented a second dispatch. [Recorded integration evidence](evidence/integration/verified-correction.json)

- The pinned Earl Grey ASIC target reached source discovery with 706 native entries and 43 TopGen instance boundaries. The native DFFRAM probe matched the 80 logical signal bits, both supplies and the LEF/GDS extent. Missing Liberty blocks macro timing qualification. [Readiness guide](OPENTITAN_READINESS.md) · [Milestone evaluation](MILESTONE_10_EVALUATION.md)

- Studio 2.0 promotes the stage export and read-only explorer from Patch 09D. The browser validation loaded real GCD and AES OpenDB checkpoints, compared stages, inspected timing evidence, measured distances, exported a provenance-tagged Canvas PNG and displayed independent acceptance gates. [Workspace guide and measured checks](PHYSICAL_WORKSPACE.md)

- Milestone 10B started from `4fb0961` and [passing main CI](https://github.com/wellitsabhinav/AUTONOMOUS-EDA/actions/runs/43674490226). The pinned AES handoff produced 60,206 mapped Sky130HD cells and reached global route at 8.5 ns. Detailed route timed out after 900 seconds. The [terminal receipt](evidence/opentitan-aes/physical.json) records the stage failure, mapped Level 2 readiness and blocked final verification. A separate GCD budget test retained an interrupted synthesis attempt and its native log.

- Milestone 10A starts from `e2c4155` after the documentation regression repair and [passing main CI](https://github.com/wellitsabhinav/AUTONOMOUS-EDA/actions/runs/43673048314). Two native OpenTitan Earl Grey AES source exports agreed across 226 ordered sources and 11 generated primitives. The pinned Yosys build elaborated 174 module definitions and completed generic synthesis of 56,622 cells. The block reached readiness Level 1. Sky130HD mapping, block constraints, functional regression and physical qualification remain open. [Milestone #11](https://github.com/wellitsabhinav/AUTONOMOUS-EDA/issues/11) · [AES evidence and reproduction](tutorials/OPENTITAN_AES.md)

- The Milestone 10 GCD recovery regression cancelled the first placement attempt with SIGTERM. Resume preserved both upstream checkpoint attempts and the interrupted attempt, and completed all seven stages in 147.999 s of successful stage time. Setup remained −1.20488 ns at 1.1 ns. Hold, timing coverage, routing, platform DRC, LVS and independent reopening passed. KLayout rendered and the gallery rehashed all four previews from the resumed GDSII. [Run, gate and PNG receipts](evidence/gcd-recovery/recovery.json)

- The merge of [ICeWall PR #26](https://github.com/wellitsabhinav/AUTONOMOUS-EDA/pull/26) at `5ca213d` dropped three documentation regression tests from [PR #27](https://github.com/wellitsabhinav/AUTONOMOUS-EDA/pull/27). The baseline repair restores CLI coverage, README navigation and GCD walkthrough provenance checks, and keeps the chip figure check. All three Python jobs failed Ruff at the merge commit. [Failed main CI](https://github.com/wellitsabhinav/AUTONOMOUS-EDA/actions/runs/43575147766)

- [Patch 09D #25](https://github.com/wellitsabhinav/AUTONOMOUS-EDA/issues/25) exports seven real GCD checkpoints to hash-bound PNGs and inspectable geometry. The [workspace guide](PHYSICAL_WORKSPACE.md) records the GCD, DEF and ICeWall checks and the original OpenROAD teardown failure. The local explorer compares stages, and the console reads retained run and supervisor journals. The technical-manual commits merged in [PR #27](https://github.com/wellitsabhinav/AUTONOMOUS-EDA/pull/27). GCD setup stays failed at −1.20488 ns.

- [Patch 09D](https://github.com/wellitsabhinav/AUTONOMOUS-EDA/issues/25) begins with the [technical manual](../README.md), [CLI reference](CLI_REFERENCE.md), and [GCD walkthrough](tutorials/GCD_WALKTHROUGH.md). The starting GitHub `main` at `797fdf2` passed all four [CI jobs](https://github.com/wellitsabhinav/AUTONOMOUS-EDA/actions/runs/43556123430).
- A fresh pinned GCD controller run, `b162fa09cf9442dda08b0b31cb58c728`, completed seven stages in 145.208 s of recorded stage time and resumed with identical attempt evidence. Hold, coverage, routing, platform DRC, LVS and independent GDSII reopening passed. Setup failed at −1.20488 ns at the original 1.1 ns clock. KLayout 0.30.12 rendered and the gallery verified four PNGs from this run's final GDSII. The [walkthrough](tutorials/GCD_WALKTHROUGH.md#inspect-and-export-the-final-layout) publishes the overview and links its source hashes. Stage-specific layout renders, the interactive explorer, runtime console and general log triage are subsequent slices.
- Patch 09A runs [pinned ICeWall placement](IO_PAD_RING_EVALUATION.md) with real Sky130 IO abstracts. The probe recorded four corners, four pads and 156 continuity cells. Chip-level acceptance remains blocked.

- Milestone 09 adds [configurable verification and acceptance](VERIFICATION_AND_ACCEPTANCE.md) for [issue #10](https://github.com/wellitsabhinav/AUTONOMOUS-EDA/issues/10). The [evaluation](VERIFICATION_EVALUATION.md) records real Icarus regression and Yosys proof failure and correction, OpenSTA coverage on retained GCD controller runs, and the unchanged GCD and Ibex setup failures.
- Optional ICeWall chip assembly is tracked in [Patch 09A](https://github.com/wellitsabhinav/AUTONOMOUS-EDA/issues/20). The pinned image has ICeWall and SKY130 IO LEFs. Its bundled IO library lacks matching GDS and contains dummy Liberty, so chip-level signoff requires additional views and verification.

- Milestone 08 implements [budgeted physical optimization](OPTIMIZATION.md) for [issue #9](https://github.com/wellitsabhinav/AUTONOMOUS-EDA/issues/9). Four isolated GCD candidates ran at the original 1.1 ns clock. All passed hold, DRC and LVS and failed setup. The [evaluation](OPTIMIZATION_EVALUATION.md) records the empty frontiers, the deterministic and live-guided measurements, and the interrupted campaign.
- The [example gallery](EXAMPLE_GALLERY.md) contains verified GCD and Ibex KLayout layouts and real campaign charts with PNG and source provenance.

- Milestone 07 implements [timing and physical diagnosis](TIMING_DIAGNOSIS.md) for [issue #8](https://github.com/wellitsabhinav/AUTONOMOUS-EDA/issues/8). The original GCD and Ibex setup failures remain at −1.20488 ns and −0.377013 ns. Independent hold, platform DRC and LVS passed. The [physical analysis](PHYSICAL_DIAGNOSIS.md) documents changing stage extrema, matching endpoint checks and unavailable path-local measurements.
- The [diagnosis evaluation](DIAGNOSIS_EVALUATION.md) preserved the deterministic report in mock and live CPU Qwen workflows. Both abstained without inference for malformed, ambiguous and missing timing reports. Windows validation exercised the retained MCP run, MiniLM and the loopback model. The two cached ORFS Doctor checks were skipped.

- Milestone 06 implements the [read-only AI supervisor](AI_SUPERVISOR.md) for [issue #7](https://github.com/wellitsabhinav/AUTONOMOUS-EDA/issues/7). Local CPU inference completed four grounded selections and correctly abstained on both negative cases without model calls. See [AI evaluation](AI_EVALUATION.md) for observed timing, token counts, and limitations.
- Windows validation passed 99 tests with local Qwen inference, MiniLM, retained MCP evidence, and the cached ORFS Doctor probes enabled. Ruff, strict mypy, dependency checks, and the AI, MCP, knowledge, and Doctor CLI smoke tests passed.
- Milestone 05 merged in [PR #16](https://github.com/wellitsabhinav/AUTONOMOUS-EDA/pull/16) at `43b1cda`, closed #6, and passed [all four main CI jobs](https://github.com/wellitsabhinav/AUTONOMOUS-EDA/actions/runs/43468519240).

- Milestone 05 implements [offline engineering knowledge and retrieval](KNOWLEDGE.md) for [issue #6](https://github.com/wellitsabhinav/AUTONOMOUS-EDA/issues/6). The [benchmark](RAG_BENCHMARK.md) compares BM25, exact CPU vectors, and reciprocal rank fusion on the retained GCD and Ibex evidence and pinned ORFS documentation.
- Milestone 05 local validation passed 76 tests on Windows Python 3.12.5, including CPU MiniLM, retained GCD evidence over MCP stdio, and the two cached ORFS Doctor probes in WSL. Ruff, strict mypy, and `pip check` passed. The benchmark's 371-chunk index used schema 1 and 384-dimensional vectors. Both mutated and missing GCD timing reports withdrew the corresponding verified slack result.
- [Read-only MCP PR #15](https://github.com/wellitsabhinav/AUTONOMOUS-EDA/pull/15) merged at `b72516d`, closed #5, and passed [all four main CI jobs](https://github.com/wellitsabhinav/AUTONOMOUS-EDA/actions/runs/43436542710). The [MCP guide](MCP.md) documents the optional SDK, scoped read access, and the local stdio client.
- [Bootstrap PR #2](https://github.com/wellitsabhinav/AUTONOMOUS-EDA/pull/2) merged at `6a43937` and closed [issue #1](https://github.com/wellitsabhinav/AUTONOMOUS-EDA/issues/1). [Head CI](https://github.com/wellitsabhinav/AUTONOMOUS-EDA/actions/runs/43415421743) and [merged main CI](https://github.com/wellitsabhinav/AUTONOMOUS-EDA/actions/runs/43419392128) passed Python 3.11, Python 3.12, and bootstrap documentation.
- On Windows with Python 3.12.5, Ruff lint, Ruff formatting, mypy, and pytest passed on bootstrap (1 test) and an isolated archive of local controller commit `9e79f44` (16 tests).
- [Environment Doctor PR #13](https://github.com/wellitsabhinav/AUTONOMOUS-EDA/pull/13) merged at `126672d` and closed #3. [Main CI](https://github.com/wellitsabhinav/AUTONOMOUS-EDA/actions/runs/43426732364) passed. The controller branch includes the separately tested Doctor cleanup, redaction, and dependency-pinning hardening that was pushed after the PR merged.
- The [controller evidence contract](EVIDENCE_CONTRACT.md) specifies schema 1, SQLite stage attempts, artifact-bound metrics and gates, and conservative comparisons. The live GCD run completed seven stages in 140.816 s of recorded stage time and resumed with seven unchanged attempts. DRC, LVS, hold and independent GDS reopening passed. Setup failed at −1.20488 ns with the original 1.1 ns clock. Local evidence is under `experiments/controller-gcd-m4-acceptance-20261113-182109/`.
- [Controller PR #14](https://github.com/wellitsabhinav/AUTONOMOUS-EDA/pull/14) merged at `5cd9bbc`, closed #4, and passed [all four main CI jobs](https://github.com/wellitsabhinav/AUTONOMOUS-EDA/actions/runs/43430072695).
- Over actual SDK stdio, the retained GCD and Ibex archives reported setup slack of −1.20488 ns and −0.377013 ns, respectively, with setup failed and hold, platform DRC, and LVS passed independently. A disposable GCD copy with a modified or missing timing report returned `UNKNOWN` for setup slack and `BLOCKED` for setup verification.
- MCP validation on Windows Python 3.12.5 passed 49 tests with the retained GCD run enabled. Linux Python 3.12.3 passed 47 tests using compact MCP fixtures. The Linux base installation passed 39 tests with the MCP extra absent and returned the install hint for `mcp serve`. The GitHub matrix covers Python 3.11 and 3.12.
- The [AI system architecture](architecture/AI_SYSTEM_ARCHITECTURE.md) and [ADR](adr/0001-ai-system-foundations.md) describe future work. The later local deterministic controller and physical artifacts remain available. The archived GCD and Ibex runs both missed setup at their original clocks.
- GitHub returned HTTP 403 for private branch protection under the current plan. Check head CI and the repository's approval workflow at each merge decision. The [roadmap](ROADMAP.md) links the dependent issues.

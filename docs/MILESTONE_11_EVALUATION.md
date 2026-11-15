# Milestone 11 engineering evaluation

## Baseline audit

The checkout at `F:/AgenticAI` started clean on `codex/milestone-10e-integration` at
`e9302feecb0ab321ae1852547c916fc1cc911122`. The local `main` ref was
`c4553b92dafbf51c13865e510632a490d2dc69ac`. The GitHub branch API confirmed that remote
`main` matched the requested baseline, `c19929b8255924ab60d8b9ec51e2289bbac8a6d9`.
PR #33 was merged, its four checks succeeded, and it had no inline review threads.
There were no open PRs at the audit. Issues #12, #11 and #24 were open.
[Main CI](https://github.com/wellitsabhinav/AUTONOMOUS-EDA/actions/runs/43722346237) ·
[Integration PR](https://github.com/wellitsabhinav/AUTONOMOUS-EDA/pull/33) ·
[Telemetry](https://github.com/wellitsabhinav/AUTONOMOUS-EDA/issues/12) ·
[OpenTitan prerequisites](https://github.com/wellitsabhinav/AUTONOMOUS-EDA/issues/11) ·
[ICeWall](https://github.com/wellitsabhinav/AUTONOMOUS-EDA/issues/24)

### Observed host and tool environment

| Observation | Value | Source |
| --- | --- | --- |
| Windows logical CPUs | 16 | .NET `Environment.ProcessorCount` |
| Windows physical RAM and available RAM | 16,366,567,424 B total and 4,066,734,080 B available at sampling | Win32 `GlobalMemoryStatusEx` |
| WSL settings | `memory=4GB`, `processors=4`, `swap=8GB` | User `.wslconfig` |
| Working EDA distribution | `AutonomousEDA`, Linux 6.18.33.2-microsoft-standard-WSL2, four CPUs | `uname -r`, `nproc` |
| WSL effective RAM and swap | 4,106,866,688 B total, 3,503,570,944 B available at sampling, 0 B swap | `free -b` |
| Docker | 29.1.3, cgroup v2, four CPUs and 4,106,866,688 B visible | `docker version`, `docker info` |
| Experiment filesystem | 362,531,516,416 B capacity and 176,388,739,072 B available at sampling | `df -B1 /mnt/f/AgenticAI` |
| Host tools | Python 3.12.5 (Windows), Python 3.12.3 (WSL), Yosys 0.30+48 (Windows) | Tool version probes |
| Pinned ORFS image | `openroad/orfs@sha256:8eff077230b86729f6248b34eeab370e486fa4052d34c60d9067e3e4aa720987` | Docker image ID and repository digest |
| Native ORFS tools | Yosys 0.68+post, KLayout 0.30.12, OpenROAD banner reports `unknown` | Pinned GCD synthesis, finish and stage logs |
| Sky130HD Liberty | `sky130_fd_sc_hd__tt_025C_1v80.lib`, SHA-256 `ec0e1067a35c8bf20b11e58d1e8ac53326067e4dac84a125cc1b917a3518d0d9` | `sha256sum` in the pinned image |
| Historical AES allocation | Two cores, 3,000 MiB memory, zero additional swap, 900 s per stage | Retained manifest, SQLite run metadata and launch log |

The default `Ubuntu-24.04` distribution could not attach its configured ext4 disk. The
configured swap disk directory was also missing. The `AutonomousEDA` distribution ran the
Docker probes. No WSL settings were changed.

### Observed AES checkpoint evidence

The source receipt identifies OpenTitan `b2d662157db2802bde0e40dfc96fd0316042d116`, 226
ordered RTL inputs and 11 generated primitives. The physical experiment used ORFS
`23341145f0fc92a522de26d5244a4a0013818f97`, Sky130HD and an experimental 8.5 ns clock.
The audit rehashed all 29 files in the physical receipt against the retained experiment
`experiments/m10-aes-physical-01`. The translated RTL, block SDC, adapter configuration,
global-route checkpoints and failed routing log matched. The retained controller run ID
was `0271212dcd2043cbbef05cc59e4dc55e`.
[Ordered source evidence](evidence/opentitan-aes/receipt.json) ·
[Native tool and artifact hashes](evidence/opentitan-aes/physical.json)

| Measurement | Retained result |
| --- | --- |
| Mapped standard cells | 60,206 |
| Synthesis standard-cell area | 487,449 µm² |
| Global-route estimated setup and hold worst slack | +0.0053717 ns / +0.344325 ns |
| Global-route OpenDB SHA-256 | `72c9792677b0392a08a22a292a88b596568e151200d3e8ab80ead4517563a551` |
| Global-route SDC SHA-256 | `437815ffc7e2b699955986cdb10e078f9ec7c7dddd0c3f255a472cf736dea242` |
| Detailed route | Attempt 6, incomplete after 900.839 s, controller exit 124 |
| Last reported router progress | Optimization iteration 0 at 30%, with 15,973 interim violations |
| Largest retained detailed-router process memory reading | 2,423.33 MB in the 20% progress line, in the router's own units |

The prior timeout has no container memory high-water measurement or confirmed OOM evidence.
The route progress rows describe an unfinished optimization iteration. Final detailed-router
DRC, extraction, GDS, independent reopening, final STA, platform DRC and LVS remain blocked.
The positive slack is based on global-route estimated parasitics.

### Timing assumptions and qualification blockers

The staged SDC defines one ideal 8.5 ns clock named `main` over `clk_i` and `clk_edn_i`.
It supplies no independent EDN clock relationship, input/output delays, uncertainty or
reset and CDC timing contract. Internal paths timed by that experimental model can be
compared at their recorded implementation stage. Full AES interface coverage, full peripheral
DV and translated-RTL equivalence remain open. The retained design is readiness Level 2.

The progress samples provide a lower bound on work performed during the stopped route.
The final convergence time and container peak remain unknown until measured in a new attempt.
The retained GCD baseline, the interrupted and resumed GCD run, and optimization campaign
remain available for later reporting. [GCD recovery](evidence/gcd-recovery/recovery.json) ·
[Candidate outcomes](OPTIMIZATION_EVALUATION.md)

### Reproduction

From the repository root on the vetted host, use the approved GitHub session to check
`main` and its CI result. Use the working WSL distribution for native probes.

```powershell
git status --short --branch
git remote -v
gh api repos/wellitsabhinav/AUTONOMOUS-EDA/branches/main --jq .commit.sha
gh run view 43722346237 --repo wellitsabhinav/AUTONOMOUS-EDA
wsl -d AutonomousEDA -- free -b
wsl -d AutonomousEDA -- docker info
```

```bash
cd /mnt/f/AgenticAI
export PYTHONPATH=src
python -m autonomous_eda report experiments/m10-aes-physical-01
python -m autonomous_eda report experiments/m10-aes-physical-01 --profile physical
```

The [AES walkthrough](tutorials/OPENTITAN_AES.md) reproduces the pinned source staging and
physical adapter. Use a fresh experiment directory for any new implementation attempt.

## 11A native resource validation

The instrumented GCD run `7f902b30b15d466eb12ffdb852c39595` used two cores,
1,600 MiB of RAM, zero additional container swap and a 400 s invocation cap.
It completed all seven stages, independently reopened its final GDS and passed
hold, timing coverage, router DRC, platform DRC and LVS. Setup failed at
−1.20488 ns with the original 1.1 ns clock. [Native receipts](evidence/resource-telemetry/native.json)

| Invocation | Elapsed seconds | Highest sampled cgroup peak (bytes) |
| --- | ---: | ---: |
| Synthesis | 11.629 | 323,874,816 |
| Floorplan | 10.976 | 82,423,808 |
| Placement | 19.747 | 297,762,816 |
| CTS | 13.241 | 262,610,944 |
| Global route | 20.564 | 231,391,232 |
| Detailed route | 42.321 | 437,456,896 |
| Finish | 17.995 | 597,123,072 |
| GDS reopening | 3.522 | 254,808,064 |
| Platform DRC | 15.547 | 685,625,344 |
| LVS | 9.745 | 407,707,648 |

Native test containers used a 48 MiB cap and a bounded allocation of at most 96 MiB.
The allocation probe reported Docker OOM evidence. Explicit exit 137 without that
evidence returned `UNKNOWN_FAILURE`. The controller timeout returned
`WALL_TIMEOUT` and the SIGTERM and SIGKILL probes verified cleanup and recovery.
Linux cgroup v1 was exercised with fixtures. The working Docker daemon uses v2.
OpenSTA coverage has its own bounded subprocess records and awaits container sampling.
The Linux suite passed 227 tests with 17 optional skips. The separate native resource
suite passed all nine tests. Ruff, strict mypy and the documentation-file checks passed.

Four isolated GCD synthesis runs used the identical pinned target and budgets in
control, observed, observed, control order. They recorded 11.625, 10.894, 10.544 and
10.461 s respectively. The paired differences had opposite signs, so the four trials
did not resolve a positive instrumentation cost. [Paired measurements](evidence/resource-telemetry/native.json)

```bash
export PYTHONPATH=src
python -m autonomous_eda run configs/gcd-sky130hd.toml experiments/gcd-telemetry \
  --memory-mib 1600 --wall-clock-seconds 400 --max-retries 0
python -m autonomous_eda verify experiments/gcd-telemetry --profile physical --coverage --decks
AEDA_NATIVE_RESOURCE_TEST=1 python -m pytest -q tests/test_resource_telemetry.py
```

PR #34 merged at `6917fa04b765140e0df5f94a497bb0c8e77f993a` after all four head
checks passed. All four checks also passed on the resulting main commit.
[PR #34](https://github.com/wellitsabhinav/AUTONOMOUS-EDA/pull/34) ·
[Head CI](https://github.com/wellitsabhinav/AUTONOMOUS-EDA/actions/runs/43725553395) ·
[Main CI](https://github.com/wellitsabhinav/AUTONOMOUS-EDA/actions/runs/43725916523)

## 11B AES resource preflight and native launch

The selected seeded source export matched all 233 input hashes and retained the 226
ordered RTL sources. One separately retained export differed in one generated
`prim_pkg.sv` file. The pinned OpenROAD build independently reopened the committed
checkpoint identity as top `aes` with 82,362 instances and 63,884 nets. The launch log
shows detailed route consuming the retained `5_1_grt.odb` and `5_1_grt.sdc`.

The live preflight reported `RESOURCE_CONSTRAINED`. It observed 3,511,676,928 B of
available WSL RAM and 175,793,393,664 B of free space. It retained the 3,000 MiB memory
cap, zero additional swap, and two CPUs. The 8% planning margin reserved
251,658,240 B of RAM beyond the container limit at dispatch. The original 787,555,309 B
experiment and margin fit the explicit 2 GiB retained artifact cap.
[Captured preflight and checkpoint chain](evidence/resource-telemetry/aes-preflight.json)

The first pilot spent 900.839 s and ended in iteration zero. Its last recorded 30%
progress row followed 430 s of router optimization. A linear projection would place
that first iteration at about 1,433 s. The progress percentage is coarse, and later
iterations can vary. The bounded trial selected 2,700 s to cover that exploratory
first-iteration estimate and leave time for further iterations. The 3,700 s
invocation cap also allows finish and GDS reopening if detailed route completes.
The measured evidence does not yet establish a feasible completion budget.

The isolated replay created run `eb2896531cd2412d9611ee79d80f6ceb` and dispatched
route as a new attempt. The pinned clock, RTL, core configuration and tool image all
match the original. At 171.479 s of sampling, the cgroup peak counter reached
740,425,728 B and had recorded zero OOM kills. This intermediate observation
covers dispatch and early routing activity. The terminal route and downstream
acceptance are pending. [Launch sample and source hashes](evidence/resource-telemetry/aes-launch.json)

```bash
export PYTHONPATH=src
python -m autonomous_eda resources plan experiments/m10-aes-physical-01 \
  --stage route --stage-seconds 2700 --wall-seconds 3700 --artifact-mib 2048 \
  --margin 0.08 --policy bounded-trial
python -m autonomous_eda resources retry experiments/m10-aes-physical-01 \
  experiments/m11b-aes-route-reproduction --stage route --stage-seconds 2700 \
  --wall-seconds 3700 --artifact-mib 2048 --margin 0.08 --policy bounded-trial
```

PR #35 merged at `124100d60f4167c38aba229e05072ce7297d4e86` with all four head
and main checks green. [PR #35](https://github.com/wellitsabhinav/AUTONOMOUS-EDA/pull/35) ·
[Head CI](https://github.com/wellitsabhinav/AUTONOMOUS-EDA/actions/runs/43726745966) ·
[Main CI](https://github.com/wellitsabhinav/AUTONOMOUS-EDA/actions/runs/43727111027)

## 11C Independent physical and design qualification

The GCD control completed route and finish. Its verified OpenRCX native log records
482 extracted nets, and the independently instrumented OpenSTA probe loaded its
final OpenDB, SDC and SPEF. All seven configured coverage checks passed. Docker
recorded a 3.156 s coverage invocation with a 7,524,352 B cgroup peak at the single
retained sample. That sample is a lower bound for the whole probe. The reports
retain separate native stdout and stderr.

GCD passed hold, router DRC, platform DRC, LVS and independent GDS reopening.
Implementation passed and reached Level 3. Physical acceptance and design
qualification failed because extracted setup worst slack was −1.20488 ns.
[GCD qualification and coverage receipt](evidence/resource-telemetry/gcd-qualification.json)

AES signoff waits for its route result and final products. The pinned pilot's global-route
estimated slack cannot establish extracted timing. The block interface timing contract,
peripheral DV and translated-RTL equivalence remain separate gates.
[OpenTitan qualification rules](OPENTITAN_READINESS.md#independent-core-qualification)

```bash
PYTHONPATH=src python -m autonomous_eda verify experiments/m11c-gcd-coverage-01 \
  --profile physical --coverage
PYTHONPATH=src python -m autonomous_eda readiness qualification experiments/m11c-gcd-coverage-01
```

PR #36 merged at `f0d959f782daaa1ca36eec36ededae62aae964b7`. Its four head and main
checks passed. [PR #36](https://github.com/wellitsabhinav/AUTONOMOUS-EDA/pull/36) ·
[Head CI](https://github.com/wellitsabhinav/AUTONOMOUS-EDA/actions/runs/43727753472) ·
[Main CI](https://github.com/wellitsabhinav/AUTONOMOUS-EDA/actions/runs/43728191884)

## 11D Structured reporting and benchmark controls

The versioned reporting commands and read-only MCP resource tool preserve independent
attempt outcomes and artifact integrity. The Linux control report used retained
GCD baseline, GCD recovery, AES 10B and the instrumented GCD run. The GCD baseline
reported 145.208 s of stage time. The resumed run reported 147.999 s of verified
stage time and retained the interrupted attempt. AES 10B accumulated 2,338.478 s
through the incomplete route. These workloads exercise different clocks and scopes.

The retained optimization campaign contained four completed candidates. Their setup
failures left both feasible frontiers empty. The exporter reproduced all five
control PNG hashes after fixing stage-row order. [Control reproducibility receipt](evidence/resource-telemetry/benchmark-control.json) ·
[Benchmark commands and data contract](BENCHMARK_REPORTING.md)

PR #37 merged at `4fadb8685cc89cadc632b9a4b1e85b5f92159b63` with four green
head checks and four green main checks. [PR #37](https://github.com/wellitsabhinav/AUTONOMOUS-EDA/pull/37) ·
[Head CI](https://github.com/wellitsabhinav/AUTONOMOUS-EDA/actions/runs/43729232240) ·
[Main CI](https://github.com/wellitsabhinav/AUTONOMOUS-EDA/actions/runs/43729600960)

## 11E Terminal AES result and integrated evidence

| Measurement or gate | AES retry result |
| --- | --- |
| RTL, platform and clock | Pinned OpenTitan AES on Sky130HD, 8.5 ns |
| Run and attempt | `eb2896531cd2412d9611ee79d80f6ceb`, detailed-route attempt 7 |
| Reused stages | Synthesis, floorplan, placement, CTS and global route |
| Elapsed and allocated wall time | 2,255.853 s elapsed, 2,700 s stage limit |
| CPU time and allocation | 4,205.021 s sampled cgroup CPU time, two allocated cores |
| Cgroup peak and cap | 3,145,728,000 B, equal to the 3,000 MiB cap, zero additional swap |
| Retained telemetry | 986 samples at a nominal two-second interval |
| Detailed-router output | Iteration 0 reached 50% with 15,973 interim violations |
| Exit and cleanup | `CONFIRMED_OOM`, Docker exit 2, successful cleanup |
| Last verified checkpoint | `5_1_grt.odb` and `5_1_grt.sdc`, hashes match the original |
| Final database, netlist, SDC, SPEF and GDS | Absent |
| Extracted setup, hold and coverage | Blocked |
| Router and platform DRC, LVS, independent GDS reopening | Blocked |
| Implementation / physical acceptance / design qualification | Failed / blocked / blocked |
| AES readiness | Level 2 |

Docker recorded `State.OOMKilled=true` before cleanup. The last sampled cgroup OOM-kill
counter was zero and the controller deadline had not expired. A separate live sample
recorded 2,299,369 `memory.events max` events while the process was near its cap.
The route log ends with Make errors after the final progress row with no completed
detailed-route checkpoint. A repeat preflight using the now-observed peak returns
`INSUFFICIENT_RESOURCES` at the same cap and 8% margin. This establishes the limit
encountered by the attempt. Completion requirements on a larger host remain unknown.
[Terminal receipt and gate matrix](evidence/resource-telemetry/aes-terminal.json) ·
[Measured postflight](evidence/resource-telemetry/aes-postflight.json)

The baseline's +0.0053717 ns setup and +0.344325 ns hold slack are global-route
estimates. The retry preserves those checkpoints and provides no extracted timing or
final design-rule result. All 29 original physical receipt hashes were rechecked after
the retry. Full peripheral DV, translated-RTL equivalence and the qualified AES
interface timing contract remain independent blockers.

### Benchmark and Studio

| Case | Verified attempt seconds | Implementation | Physical acceptance |
| --- | ---: | --- | --- |
| GCD retained baseline | 145.208 | Passed | Failed setup |
| GCD interrupted and resumed | 147.999 | Passed | Failed setup |
| AES 10B | 2,338.478 | Failed detailed route | Blocked |
| AES 11B history | 4,594.331 | Failed detailed route | Blocked |

The AES 11B history total includes inherited stages and the prior timeout. Its new
dispatch lasted 2,255.853 s. GCD implements a smaller design at its own 1.1 ns clock
and provides completed-deck controls. The comparison is descriptive. Both optimization
frontiers remain empty after evaluating the four rejected candidates.
[JSON](evidence/milestone-11-benchmark/report.json) ·
[Markdown](evidence/milestone-11-benchmark/report.md) ·
[Run CSV](evidence/milestone-11-benchmark/runs.csv) ·
[Stage CSV](evidence/milestone-11-benchmark/stages.csv)

The five benchmark PNGs rebuilt to identical content digests.
[Route CPU timeline](evidence/milestone-11-benchmark/visualizations/routing_resource_timeline.png) ·
[Memory and high-water](evidence/milestone-11-benchmark/visualizations/routing_memory.png) ·
[Stage duration](evidence/milestone-11-benchmark/visualizations/stage_duration_comparison.png) ·
[Verification and acceptance](evidence/milestone-11-benchmark/visualizations/verification_acceptance.png) ·
[Benchmark comparison](evidence/milestone-11-benchmark/visualizations/benchmark_comparison.png) ·
[Figure provenance](evidence/milestone-11-benchmark/visualizations/visualization_manifest.json)

Headless Edge 157 loaded the AES resource stage table 176.9 ms after keyboard
activation and received a 27,626-byte metadata response. The 390 px mobile viewport
had a 390 px document width. On the retained GCD control the browser measured 198.9 ms
for initial placement and 76.5 ms for the final-OpenDB stage switch. The GCD browser
exercise also checked stage comparison, pin timing, object inspection, PNG export and
execution history. [AES desktop screenshot](images/milestone-11/resources.png) ·
[AES mobile screenshot](images/milestone-11/resources-mobile.png) ·
[Browser receipt](images/milestone-11/resource-browser-check.json)

### Acceptance matrix

| Area | Status | Evidence |
| --- | --- | --- |
| Telemetry | PASS | Native cgroup v2 peak and Docker OOM evidence, bounded timeout and interruption recovery probes |
| Resource planning | PASS | Verified finite retry and measured postflight rejection under the same cap |
| AES implementation | FAIL | Detailed-route OOM; valid upstream checkpoints retained |
| AES finish and signoff | BLOCKED | Final extraction, STA, DRC, LVS and GDS reopening were not run |
| AES design qualification | BLOCKED | Readiness Level 2, with interface timing, DV and equivalence still required |
| Reporting | PASS | Versioned queries, digest-checked exports, rejected candidates and reproducible PNGs |
| Local integration | PASS | Lint, formatting, mypy, focused tests and genuine browser sessions |

### Reproduce terminal reads

```bash
export PYTHONPATH=src
python -m autonomous_eda resources history experiments/m11b-aes-route-01 --samples 120
python -m autonomous_eda readiness qualification experiments/m11b-aes-route-01
python -m autonomous_eda artifacts snapshots experiments/m11b-aes-route-01 \
  experiments/m11e-aes-studio-reproduction --run-id eb2896531cd2412d9611ee79d80f6ceb \
  --top aes --width 860 --checkpoint place results/sky130hd/opentitan_aes/base/3_place.odb
python -m autonomous_eda artifacts workspace experiments/m11e-aes-studio-reproduction \
  --run experiments/m11b-aes-route-01
```

For the next physical milestone, qualify an environment with measured memory headroom
above the observed cap, then run the same verified checkpoint under a new finite
budget. Carry a completed route through independent finish and physical checks. Close
the AES interface timing, peripheral DV and equivalence prerequisites separately.
The [OpenTitan prerequisites](https://github.com/wellitsabhinav/AUTONOMOUS-EDA/issues/11)
and [ICeWall chip-level work](https://github.com/wellitsabhinav/AUTONOMOUS-EDA/issues/24)
remain independently tracked.

## Follow-up retry limits and verification resource evidence

The original retry command inherited the source run's RAM limit. A plain resume
could also fall back to manifest defaults instead of the trial's original limits.
The controller now accepts an explicit container cap for the isolated copy and
binds its authorization to the destination database. Resume reapplies the deadline,
artifact cap, checkpoint requirements and attempt limits. Recorded OOM caps
remain excluded when the margin is zero.

The AES postflight used the prior Docker-confirmed OOM at 3,000 MiB. With the 8%
margin, the lower exploratory cap was **3,240 MiB**. The host supplied
3,517,550,592 B of available memory at the quiet-host sample and could admit at
most **3,106 MiB** under the same reserve. The plan refused dispatch. The 3,240 MiB
figure describes the lower edge of a trial. Routing completion requirements
remain unknown. [Live postflight and exact bounds](evidence/resource-telemetry/aes-followup-preflight.json)

The native GCD control timed out at **5.767 s** with a 768 MiB cap and reused its
verified upstream checkpoints in a new run at **1,024 MiB**. The new detailed
route completed in **48.548 s** and finish produced the final layout. Resume
returned the same eight attempts. Platform DRC, LVS and extracted timing
coverage passed. Setup failed at the original 1.1 ns clock.

The replay exposed an archive bug that moved the verified `5_global_route.rpt`
out of the live artifact inventory. The new archive rule retains the upstream
report. Resume recovered exact archived bytes in the existing GCD and AES runs
without adding any attempts. GCD now reaches Level 3 on its repaired evidence.
The AES implementation and signoff results remain as recorded.

The CLI, MCP and Studio now expose separate resource records for the DRC, LVS
and OpenSTA checks. The read-only MCP resource query returned all eight controller
attempts over two bounded pages. The browser selected the actual OpenSTA dispatch
and displayed its 1,200 MiB cap. The stage table rendered in 171.5 ms after
keyboard activation in the measured desktop run. [Native retry and restored report hashes](evidence/resource-telemetry/gcd-changed-cap-retry.json) ·
[Studio verification resources](images/milestone-11-followup/resources.png) ·
[Screenshot provenance](images/milestone-11-followup/resource-browser-check.json)

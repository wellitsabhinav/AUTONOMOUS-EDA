# Timing diagnosis

Diagnose a completed controller directory or an archived reference with its recorded hashes. This command runs offline with base Python. It reads reports and SDC as data and uses a temporary verified snapshot.

~~~sh
autonomous-eda diagnose .local/diagnosis-reference/gcd
autonomous-eda diagnose .local/diagnosis-reference/ibex --check hold
autonomous-eda diagnose .local/diagnosis-reference/ibex --stage-trends
autonomous-eda diagnose .local/diagnosis-reference/ibex --json
autonomous-eda diagnose .local/diagnosis-reference/ibex --path PATH_ID --json
~~~

The typed Python API is `diagnose_timing(registry, experiment_id, check_type="setup")` and `diagnose_path(registry, experiment_id, path_id, check_type="setup")`. The full report contains stage comparisons and the citation map. Path IDs identify a reported observation within a run and change when its backing report or reference identity changes.

## Reproduce the reference runs

The GCD index identifies the retained `experiments/controller-gcd` archive. The Ibex index identifies `experiments/upstream-ibex`. Both are from ORFS revision `23341145f0fc92a522de26d5244a4a0013818f97` on Sky130HD, recorded corner `tt_025C_1v80`. Copy each path listed under `sha256` into a disposable directory with its original relative name and put the corresponding index there as `reference.json`. Diagnose the copy. The older GCD acceptance controller inventory can still be read directly. Its original checkpoints did not record all timing report hashes. New controller attempts record available supplemental reports automatically.

- [GCD reference index](evidence/gcd-sky130hd-index.json)
- [Ibex reference index](evidence/ibex-sky130hd-index.json)

## Report formats and arithmetic

The pinned reports contain OpenSTA `report_checks` tables with `Fanout Cap Slew Delay Time Description` and compact `Delay Time Description` columns. Full tables include named net markers. The receiving pin carries the increment for that sink. Adjacent pins on the same instance identify a cell arc. For register paths the launch clock pin precedes the first data arc, so the data delay includes clock-to-Q. Input data delay starts at the input port after the applied external delay. Capture latency ends before uncertainty, CRPR, and the check requirement. Signed report terms retain their original signs.

Setup, hold, recovery, removal, gating checks and explicit max/min delay terms retain separate check types. Register and port classifications require terminal descriptors. Register-to-register, input-to-register, register-to-output and input-to-output are supported. Other endpoints remain unclassified. The unconstrained report section can repeat constrained paths. A path is marked unconstrained when the path group explicitly says so, or a path in that section has arrival without required time or slack. These records do not enter worst-path ranking. Recovery and removal are kept separate from setup and hold.

The parser uses the pinned Sky130HD nanosecond convention when the inventory matches the known revision and image. A standalone report may declare ps, ns or us. Conflicting declarations, an unknown unit and unsupported table layouts produce gaps. The report preserves printed time strings and their original units. Capacitance preserves its printed value because the timing table does not declare a capacitance unit. Fanout is a count. Slew uses the timing unit.

For max checks the calculated slack is required minus arrival. For min checks it is arrival minus required. OpenSTA repeats the arrival with opposite sign in the summary table. Each printed value contributes half its least significant digit to a reconciliation bound. The two-decimal paths permit up to 0.015 ns slack residual. Cell and net sum bounds increase with the number of increments. These are conservative rounding bounds. A reported net sum of 0.00 ns can hide multiple small increments. Compact reports fold net increments into cell rows, so their cell/net totals remain unavailable. No percentages are computed. Missing, malformed or irreconcilable blocks remain visible as gaps while independently valid paths are available.

The parser limits each report to 4 MiB, 128 path blocks and 1,024 lines per block. A per-run snapshot also bounds artifact sizes, count and total bytes. See the [diagnosis schema](DIAGNOSIS_SCHEMA.md).

## Reference setup paths

| Measurement | GCD | Ibex |
| --- | --- | --- |
| SDC period | 1.1 ns | 8.0 ns |
| Final aggregate setup worst slack | −1.20488 ns | −0.377013 ns |
| Final aggregate hold worst slack | +0.429847 ns | +0.426791 ns |
| Final launch → capture | `dpath.b_reg.out[2]$_DFFE_PP_` → `dpath.a_reg.out[10]$_DFFE_PP_` | `if_stage_i.instr_rdata_id_o[20]$_DFFE_PP_` → `gen_regfile_ff.register_file_i.rf_reg[58]$_DFFE_PN0P_` |
| Group and class | `core_clock`, register-to-register | `core_clock`, register-to-register |
| Arrival / required / printed slack | 2.35 / 1.14 / −1.20 ns | 9.37 / 8.99 / −0.38 ns |
| Final data delay | 2.06 ± 0.01 ns | 8.15 ± 0.01 ns |
| Data cell arc sum | 2.04 ± 0.055 ns, 11 arcs | 8.09 ± 0.225 ns, 45 arcs |
| Data net arc sum | 0.00 ± 0.055 ns | 0.03 ± 0.225 ns |
| Launch / capture insertion | 0.29 / 0.28 ns | 1.22 / 1.11 ns |
| Same-clock insertion contribution to slack | −0.01 ± 0.02 ns | −0.11 ± 0.02 ns |
| Library setup term | −0.24 ns | −0.12 ns |
| Timing path source | `reports/sky130hd/gcd/base/6_finish.rpt` lines 132–205 | `reports/sky130hd/ibex/base/6_finish.rpt` lines 395–594 |

The selected paths are the worst printed setup paths in their final full tables and agree with the separately retained higher-precision aggregate slack. GCD's printed arrival and required times imply −1.21 ns, within 0.015 ns of its printed −1.20 ns slack. Both paths contain explicit clock edges, clock propagation and a library setup term. The SDC declares each core clock and a virtual clock. A missing explicit uncertainty row supplies no separate uncertainty measurement.

Cell increments account for most of the final data delay in these reports. The increments depend on input slew and output load, so the breakdown cannot isolate RTL logic, cell sizing, placement or parasitics as a cause. Use path-specific delay calculations and higher-precision full reports to distinguish those mechanisms. See the [stage and physical diagnosis](PHYSICAL_DIAGNOSIS.md).

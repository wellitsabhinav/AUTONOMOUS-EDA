# Physical diagnosis

The diagnosis reads only recorded ORFS artifacts and keeps each stage's source keys and corner. Stage worst slack is the signed `__timing__{setup,hold}__ws` metric. WNS is the lesser of zero and worst slack. Stage deltas compare available aggregates at the recorded corner. Synthesis and detailed route have no setup extrema in these archives. The controller also reports its recorded attempt runtimes when the attempt logs verify.

| Stage | GCD setup WNS, ns | Ibex setup WNS, ns |
| --- | ---: | ---: |
| Floorplan | −1.10564 | −15.133 |
| Detailed placement | −1.18414 | −0.767219 |
| CTS | −1.19621 | −0.123554 |
| Global route | −1.23073 | −0.238938 |
| Final | −1.20488 | −0.377013 |

For the reference stages, the sources are `logs/sky130hd/<design>/base/{2_1_floorplan,3_5_place_dp,4_1_cts,5_1_grt,6_report}.json`. The high-precision Ibex CTS → global-route and global-route → final WNS changes are −0.115384 and −0.138075 ns. Global-route worst launches from instruction bit 21. CTS and final worst launch from bit 20. Each of these extrema ends at register-file bit 58.

Path comparisons require a unique pair for the same launch and capture terminals, pins, endpoint transition, group, check, clock names, clock edges and edge separation, recorded corner, and byte-identical stage SDC. Launch data transition and intermediate pin changes are surfaced. The comparison describes evolution of an endpoint check at sampled stages. Identifying a change to a particular physical arc needs compatible arc-level evidence.

- GCD failed setup before routing. Its global-route and final limiting register checks match the endpoints and data pin sequence. The rounded slack improves by 0.03 ± 0.01 ns. The retained CTS worst path has different endpoints. Placement and global-route reports also have multiple aggregate candidates at the printed precision.
- Ibex's bit-20 endpoint check appears at CTS and final. Its rounded slack moves from −0.12 to −0.38 ns and data delay grows by 0.24 ± 0.02 ns. The launch transition and intermediate data pins change. Cell and net deltas each have a ±0.42 ns rounding bound. The retained global-route report provides a different limiting launch bit, so it cannot fill in the bit-20 comparison.

The source reports for these checks are `4_cts_final.rpt` (Ibex lines 395–576), `5_global_route.rpt` (GCD lines 132–205 and Ibex launch at line 395), and `6_finish.rpt` (GCD lines 132–205, Ibex lines 395–594). The structured output carries the verified hash and exact range for each result.

## Routability and physical context

| Observation | GCD | Ibex |
| --- | ---: | ---: |
| Detailed placement utilization | 67.6233% | 63.3571% |
| Final standard-cell area | 4,414.23 µm² | 177,181 µm² |
| Final standard-cell count | 524 | 21,131 |
| Final global-route total overflow | 0 | 0 |
| Detailed route wirelength | 7,425 µm | 705,438 µm |
| Final max slew violations | 0 | 1 |
| Final max cap / fanout violations | 0 / 0 | 0 / 0 |
| Platform DRC violations | 0 | 0 |
| LVS | matched | matched |

The final congestion `Total` row in `5_1_grt.log` supplies the overflow count. The detailed routing metric uses the last occurrence of the pinned iterative wirelength counter. The standard-cell count excludes other reported instance classes. Repair-buffer counts, final buffer inventories, design-wide clock skew and flow error counts are available when their source keys are present. Buffer inventories do not track insertion or removal of individual path cells. Area and utilization scope follow the originating ORFS metric.

Zero final global overflow describes the aggregate route report. Local congestion near the critical cells and path-specific wire RC remain unverified. Ibex's single reported slew violation has no established link to the limiting path. Locate the violating pin and compare it with the limiting path and its related nets. A stage-matched OpenSTA report on bit 20 with the same endpoint, edge and corner would clarify whether the data or clock terms changed at global route. Per-instance sizing history, path-local congestion and independent timing coverage are missing. Hold, DRC and LVS passed independently in the archived baseline; setup and overall acceptance failed. Functional equivalence and signoff remain unverified.

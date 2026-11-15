# Example results gallery

These figures come from retained GCD and Ibex Sky130HD artifacts and four new GCD implementations. All timing figures use the recorded `tt_025C_1v80` corner. KLayout 0.30.12 rendered the GDS views with the pinned Sky130HD layer map. The gallery publishes reduced-size copies. Full-resolution PNGs and manifests stay with the run.

## Physical implementation

### GCD at 1.1 ns

Retained run `fadc5e06093b`. Final setup worst slack was −1.20488 ns. Hold, platform DRC and LVS passed. The source GDS is identified by the [GCD reference index](evidence/gcd-sky130hd-index.json).

<img src="images/examples/gcd/layout_overview.png" alt="Retained GCD final GDS overview" width="600">

*GCD final GDS hierarchy at the recorded 80.025 µm × 80.025 µm extent. The image displays physical geometry.*

<img src="images/examples/gcd/layout_placement.png" alt="GCD standard-cell placement" width="600">

*GCD placement from transformed Sky130HD standard-cell GDS bounding boxes. Fill, decap and tap instances are omitted. Bounding boxes can overlap where cell geometry extends beyond the nominal placement site.*

<img src="images/examples/gcd/layout_routing.png" alt="GCD routing layers" width="600">

*The retained GCD GDS with routing layer numbers 67–72 selected. The [central detail](images/examples/gcd/layout_detail.png) enlarges the same layers at the layout center.*

### Ibex at 8 ns

Retained run `820d1810648f`. The original archive reports −0.377013 ns setup slack. Hold, DRC and LVS passed. [Ibex reference index](evidence/ibex-sky130hd-index.json)

<img src="images/examples/ibex/layout_overview.png" alt="Retained Ibex final GDSII geometry" width="600">

*Ibex final GDS overview of the historical implementation.*

### OpenTitan AES at 8.5 ns

![OpenTitan AES Sky130HD placement](images/examples/opentitan-aes/placement.png)

OpenROAD rendered the verified placement checkpoint for the pinned masked AES configuration. The observed run used 60,206 standard cells at synthesis and a 1,406,900 µm² die. [Physical receipt](evidence/opentitan-aes/physical.json) · [PNG provenance](images/examples/opentitan-aes/provenance.json) · [Reproduction](tutorials/OPENTITAN_AES.md#attempt-the-sky130hd-core-flow)

## Timing and physical checks

<img src="images/examples/gcd/timing_setup_summary.png" alt="Final GCD setup slack and corresponding OpenSTA path" width="600">

*GCD setup. The aggregate slack preserves report precision. The path table uses rounded values.* [Independent DRC and LVS outcomes](images/examples/gcd/verification_summary.png) · [GCD provenance](images/examples/gcd/provenance.json)

<img src="images/examples/ibex/stage_timing_trend.png" alt="Ibex worst negative slack by implementation stage" width="600">

*Stage worst negative slack from verified ORFS JSON reports. The global-route and final setup extrema launch from different instruction bits.* [Critical path accounting](images/examples/ibex/critical_path_breakdown.png) · [Verification gates](images/examples/ibex/verification_summary.png) · [PNG provenance](images/examples/ibex/provenance.json)

## Optimization

Campaign `3563e8e28ec145239fc5f6f1a0ab9f69`. Four candidates completed, and all missed the original 1.1 ns setup constraint. The feasible frontier is empty. [Measured results and model usage](OPTIMIZATION_EVALUATION.md)

<img src="images/examples/optimization/area_timing_tradeoff.png" alt="Measured standard-cell area and setup slack for GCD candidates" width="600">

*Recorded standard-cell area and setup slack for the retained baseline and four comparable candidates. Every point is below zero slack.*

<img src="images/examples/optimization/feasibility_matrix.png" alt="GCD implementation and physical feasibility matrix" width="600">

*Flow completion and physical feasibility from the campaign report. Full acceptance also depends on additional verification gates.* [Candidate measurements](images/examples/optimization/optimization_summary.png) · [Search history](images/examples/optimization/search_history.png) · [Budget](images/examples/optimization/budget_usage.png) · [Grid and guided comparison](images/examples/optimization/strategy_comparison.png)

<img src="images/examples/optimization/candidate_30_layout.png" alt="GCD 30 percent utilization candidate layout" width="600">

*Candidate run `599c45ae7f4c` used 30% core utilization. Its GDS extent was 89.815 µm × 89.815 µm, compared with the baseline’s 80.025 µm × 80.025 µm. Each preview is fitted to its own extent.* [PNG provenance](images/examples/optimization/provenance.json)

## Verification and acceptance

<img src="images/examples/verification-gcd/acceptance_gate_matrix.png" alt="GCD acceptance matrix with independent gate states" width="600">

The GCD reference retains independent setup failure, hold pass, DRC, LVS and GDS reopening. Its audited reference index leaves full coverage blocked. [Timing](images/examples/verification-gcd/acceptance_timing.png) · [Verdicts](images/examples/verification-gcd/acceptance_verdicts.png) · [Integrity](images/examples/verification-gcd/acceptance_integrity.png) · [Matrix JSON](images/examples/verification-gcd/acceptance.json) · [PNG provenance](images/examples/verification-gcd/provenance.json)

<img src="images/examples/verification-regression/acceptance_regression.png" alt="Icarus regression oracle failure for the XOR-to-OR fixture" width="600">

The isolated XOR-to-OR defect emitted the configured Icarus failure marker. Yosys independently found a counterexample. [Regression matrix](images/examples/verification-regression/acceptance.json) · [PNG provenance](images/examples/verification-regression/provenance.json) · [Reproduction](VERIFICATION_EVALUATION.md)

## IO pad placement

![Verification matrix for the real ICeWall abstract placement](images/examples/io-placement/io_verification.png)

Pinned OpenROAD placed four SKY130 pads, four corners and 156 continuity cells in an isolated OpenDB and DEF checkpoint. Chip GDS and electrical signoff remain blocked. [Filler counts](images/examples/io-placement/io_fillers.png) · [Placement report](images/examples/io-placement/io-report.json) · [PNG provenance](images/examples/io-placement/provenance.json) · [Reproduction](IO_PAD_RING_EVALUATION.md)

### ICeWall chip integration

<img src="images/examples/io-integration/layout_overview.png" alt="KLayout overview of the independently reopened SKY130 chip GDS and its inset pad ring" width="600">

KLayout reopened the 1,200 × 1,200 µm chip outline with four pads, four corner cells and 168 filler instances. [Routed metal view](images/examples/io-integration/layout_routing.png) · [Connectivity matrix](images/examples/io-integration/connectivity_matrix.png) · [Gate status](images/examples/io-integration/signoff_gate_matrix.png) · [PNG provenance](images/examples/io-integration/provenance.json) · [Reproduction](IO_CHIP_EVALUATION.md)

## Regenerate

~~~sh
python -m pip install -e '.[visuals]'
autonomous-eda artifacts render .local/diagnosis-reference/gcd --all
autonomous-eda artifacts render .local/diagnosis-reference/ibex --all
autonomous-eda artifacts render experiments/optimization-gcd-m8 --all
autonomous-eda artifacts gallery experiments/optimization-gcd-m8
~~~

Use Linux or WSL2 with the cached image for the KLayout views. Report-only rendering works with Matplotlib Agg on Windows or Linux. The campaign renderer writes `report.json`, `report.md`, and `visualizations/visualization_manifest.json`. Clock-net identification and a location-qualified critical-region crop require additional evidence. The gallery's detail is a geometric center crop.

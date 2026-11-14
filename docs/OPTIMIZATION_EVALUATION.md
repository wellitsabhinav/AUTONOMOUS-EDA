# Optimization evaluation

## Bounded GCD campaign

Campaign `3563e8e28ec145239fc5f6f1a0ab9f69` used the retained GCD baseline at 1.1 ns, Sky130HD corner `tt_025C_1v80`, and the pinned Linux image. The same controller, physical profile, two cores, 1,200 MiB memory allocation, and 900-second per-candidate cap applied to all proposals. The approved pool was 30, 46, 24, 34, 42, and 52% `CORE_UTILIZATION`. The campaign permitted four new implementations and one running candidate at a time.

| Proposal | Utilization | Setup slack, ns | Hold slack, ns | Cell area, µm² | Power estimate, mW | Wirelength, µm | Controller + deck time, s |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| Retained baseline | 38% | −1.20488 | +0.429847 | 4414.23 | 5.28940 | 7425 | Unavailable |
| Grid | 30% | −1.17831 | +0.431021 | 4463.03 | 5.32243 | 7500 | 162.23 |
| Grid | 46% | −1.33046 | +0.453976 | 4309.13 | 5.36263 | 8617 | 187.94 |
| Deterministic fallback | 24% | −1.20969 | +0.432164 | 4239.07 | 5.02826 | 7439 | 152.53 |
| Live model | 42% | −1.22107 | +0.421083 | 4376.70 | 5.49309 | 7500 | 167.36 |

All four reached final timing, independently reopened GDS, and passed hold, router checks, platform DRC and LVS. Final global-route overflow was zero. All four failed setup and are **infeasible**. The physical and full-acceptance frontiers are empty. Full signoff evidence, including equivalence and timing coverage, remains unavailable. The best infeasible setup candidate gained 0.02657 ns and used 48.80 µm² more cell area. The smallest-area candidate saved 175.16 µm² and lost 0.00481 ns of setup slack. Peak memory was unmeasured.

The search stopped at `MAX_CANDIDATES_REACHED`. Four reservations consumed 729.84 seconds of candidate wall time and 783.70 seconds of campaign wall time against a 3,600-second budget. Controller and deck runtime totaled 670.06 seconds. Candidate reservations, report hashes, status changes and model events remain in `experiments/optimization-gcd-m8`.

Two local proposer evaluations were rejected before model dispatch because the initial sensitive-text check matched serialized JSON protocol syntax. The 24% fallback ran to completion. The campaign was interrupted around the next proposal and resumed within the existing approval. The live journal has four candidate reservations and no interrupted physical stage. After fixing the check to inspect untrusted content fields, CPU Qwen2.5-0.5B-Instruct Q4_K_M selected 42% in 11.36 s with 1,300 input and 9 output tokens over loopback. Paid cost was zero. The guided result reduced area by 37.53 µm² and lost 0.01619 ns of setup slack relative to the baseline. This single guided implementation provides no evidence of general search superiority.

## Ibex planning

The retained 8 ns Ibex evidence remains setup-failed at −0.377013 ns with +0.426791 ns hold margin, zero platform DRC and matched LVS. Planning can generate typed utilization candidates against the pinned 50% default. The local execution scope rejects approval for Ibex and starts no implementations. The retained report therefore supports baseline evaluation and planning only.

## Reproduction

The [optimization guide](OPTIMIZATION.md) provides the CLI sequence. Use the retained baseline referenced by the [GCD index](evidence/gcd-sky130hd-index.json). The [example gallery](EXAMPLE_GALLERY.md) includes the measured area/slack plot, feasibility matrix, budget summary and source-backed layout views.

# Benchmark report

Evidence SHA-256 `9ead00c2e796e232a86abbd8691b6d19cfec30ad1f33eed8e031bf80b0705fe5`

| Case | Implementation | Physical | Design |
| --- | --- | --- | --- |
| GCD baseline | PASSED | FAILED | FAILED |
| GCD recovered | PASSED | FAILED | FAILED |
| AES 10B | FAILED | BLOCKED | BLOCKED |
| AES 11B | FAILED | BLOCKED | BLOCKED |

- GCD and AES exercise different workloads, clocks, physical sizes, and route states.
- Attempt totals can include inherited checkpoints and failed attempts.
- Resource peaks are sampled kernel high-water readings. Unavailable metrics are null.
- Power is an estimate when present. Global-route timing uses estimated parasitics.

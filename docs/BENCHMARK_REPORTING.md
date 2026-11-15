# Structured run and benchmark reporting

`report --structured` reads run identity, the input manifest and verified constraint
artifacts, selected measurements, the qualification matrix, resource history, recorded
retry policy and a paged artifact inventory. Use `--artifact PATH` to inspect one
inventory entry. `resources history` pages controller attempts with telemetry provenance
and optional bounded samples. Historical missing telemetry appears as unknown.

```bash
autonomous-eda report experiments/m11b-aes-route-01 --structured
autonomous-eda resources history experiments/m11b-aes-route-01 --limit 8 --samples 120
autonomous-eda report experiments/m11b-aes-route-01 \
  --artifact results/sky130hd/opentitan_aes/base/5_1_grt.odb
```

The MCP `get_stage_resources` tool accepts a registered experiment ID and returns at
most eight attempt summaries per page. It snapshots the SQLite inventory and selected
log and telemetry artifacts within the existing read-only bounds. Metric values include
units and source. Log and receipt SHA-256 values identify the retained evidence.

## Reproduce the benchmark

```bash
autonomous-eda benchmark build experiments/m11-benchmark \
  --case 'GCD baseline=experiments/gcd-manual-20261114' \
  --case 'GCD recovered=experiments/m10-gcd-recovery' \
  --case 'AES 10B=experiments/m10-aes-physical-01' \
  --case 'AES 11B=experiments/m11b-aes-route-01' \
  --campaign experiments/optimization-gcd-m8
autonomous-eda benchmark figures experiments/m11-benchmark
```

The export contains versioned JSON, Markdown, attempt and run CSV tables, and PNGs in
`visualizations/`. Rebuilding verifies the canonical report digest. The figure receipt
binds each PNG to the report SHA-256, source runs, renderer, generation command,
timestamp and content digest. Use the same renderer and fonts for deterministic PNG
hash comparisons.

The benchmark is descriptive across different workloads. Its eligibility record
checks the native identity, toolchain, constraints, corner and metric definition before
considering a controlled comparison. Retried histories can include inherited and failed
attempts, so their summed attempt time differs from elapsed time of the new dispatch.
Power remains an estimate and the global-route timing column uses estimated parasitics.
Missing final metrics are blank in CSV and null in JSON.

The optimization section reevaluates retained candidate evidence through the campaign
policy. It includes rejected outcomes and preserves the existing feasible-frontier
rules. Physically infeasible candidates and candidates missing a required final
metric cannot enter a feasible frontier.

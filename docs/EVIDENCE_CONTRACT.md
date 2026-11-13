# Experiment evidence contract

`autonomous_eda.evidence` exposes `get_experiment(directory)`, `import_reference(directory, index)`, `compare_experiments(baseline, candidate)`, and canonical JSON encoding and decoding. `autonomous-eda report DIRECTORY` emits the same evidence fields. These operations use the standard library and can read a completed run without an EDA backend or API key.

## Experiment schema 1

| Field | Contract |
| --- | --- |
| `schema_version`, `run_id`, `origin` | Schema version 1. New runs have durable UUID identities. Legacy controller identities are local to their directory. External reference identities are hashes of the supplied index. |
| `design`, `platform`, `input_manifest`, `rtl_identity`, `source_revision` | Top and options come from the manifest. For built-in reference runs, RTL identity is derived from the pinned image, revision, and authoritative upstream config path. Explicit RTL inspection hashes the ordered inputs, includes, declared generated inputs, constraints, parameters, and defines. |
| `configuration_sha256`, `constraints`, `toolchain`, `corner` | The configuration hash covers normalized manifest settings, the pinned image, and memory limit. Constraint references address the synthesis SDC. The pinned Sky130HD config uses `sky130_fd_sc_hd__tt_025C_1v80.lib`. Reported tool builds are included when retained. |
| `attempts`, `artifacts`, `provenance` | Stage IDs, statuses, UTC timestamps, runtime, exact command vectors, exit status, and checkpoint hashes. Verification invocations are in provenance. Each artifact has a relative path, SHA-256, byte size, stage, producer version when known, run/attempt association, observed file timestamp, and availability. |
| `metrics` | Each entry carries name, value, unit, stage, optional corner, method, source artifact and record locator. `UNKNOWN` measurements have a null value. `MEASURED` requires a matching retained artifact. |
| `verification_gates`, `required_gates`, `acceptance` | Each gate has a state and artifact references. `FAILED` has precedence among required checks. `PASSED` requires every listed check to pass. Otherwise the verdict is `BLOCKED`. The controller lists all represented gates. |

For example, this metric entry is valid in a record when the artifact exists in its verified manifest and the cited JSON field has the observed value.

```json
{
  "name": "setup_worst_slack_ns",
  "value": -1.20488,
  "unit": "ns",
  "stage": "finish",
  "corner": "tt_025C_1v80",
  "artifact": "logs/sky130hd/gcd/base/6_report.json",
  "method": "orfs-normalization-v1",
  "reference": "finish__timing__setup__ws",
  "status": "MEASURED"
}
```

This gate record is invalid because a pass requires verified artifacts.

```json
{"state": "PASSED", "artifacts": []}
```

Use `setup_worst_slack_ns` and `hold_worst_slack_ns` for the signed worst slacks. Derived WNS is clipped to zero when worst slack is positive. Power is the ORFS estimate in watts. The final timing and routing measurements describe their respective stages. DRC uses the platform-deck count and database. LVS uses the extraction/match log and database. GDS generation, KLayout reopen, router checks, platform DRC, LVS, setup, and hold are independent gates. Functional regression, equivalence, timing coverage and standalone connectivity require explicit evidence.

## Compatibility and verification

New SQLite run databases use `user_version=2`. Legacy version 0 databases retain their original six-stage contract and are read without an in-place migration. Missing historical hashes, timestamps and tool versions stay unknown. Unsupported versions produce an error. Schema 1 JSON validates gate and metric references and acceptance consistency. Reload the original experiment directory to rehash the evidence bytes. The JSON alone describes the snapshot at extraction time.

The controller invalidates changed or missing artifact hashes and dependent checkpoints. The execution lock prevents two controller dispatches against the same directory. The next resume reconciles unfinished attempts, archives stale outputs, and starts at the earliest invalid stage. Stage data and artifact payloads stay on disk. Large generated reports, GDS, and databases belong in ignored experiment directories.

Controlled comparisons require matching design, input identities, constraints, settings, toolchain, corner and gate configuration. Deltas also require matching metric definition, units and stage. Use `--descriptive` to show side-by-side observations when inputs differ or provenance is incomplete. Missing power stays unknown. Comparison reports list each run's gate outcomes and acceptance and do not compute a Pareto frontier.

## Local reference evidence

```sh
autonomous-eda experiments import-reference experiments/controller-gcd docs/evidence/gcd-sky130hd-index.json
autonomous-eda experiments import-reference experiments/upstream-ibex docs/evidence/ibex-sky130hd-index.json
```

The indexes preserve SHA-256 receipts for recorded local files. The command is read-only. An optional copy of a trusted index named `reference.json` in an external run directory makes the observation discoverable by `report` and `experiments list`. Historical imports have no controller attempts. They do not infer tool execution or equivalence from a final report. Missing or modified report files withdraw dependent measurements and gate claims.

| Historical Sky130HD run | Clock | Setup worst slack | Hold worst slack | Cell area | Instances |
| --- | ---: | ---: | ---: | ---: | ---: |
| Controller GCD | 1.1 ns | −1.20488 ns | +0.429847 ns | 4,414.23 µm² | 930 |
| Upstream ORFS Ibex | 8.0 ns | −0.377013 ns | +0.426791 ns | 177,181 µm² | 40,197 |

Both historical layouts reopened and reported zero platform DRC findings and matched LVS. Both failed setup at the original clock period. Only GCD was rerun for this milestone. The historical Ibex run used upstream ORFS.

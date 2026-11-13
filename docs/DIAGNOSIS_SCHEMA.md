# Diagnosis schema 1

The deterministic CLI emits a JSON object. Python exposes the equivalent typed dataclasses. Time values use nanoseconds unless a field explicitly describes the printed unit. Missing measurements use JSON null or a gap. A true zero has a numeric value.

| Field | Meaning |
| --- | --- |
| `schema_version`, `experiment_id`, `run_id` | Schema, authorized alias or local directory label, and recorded run identity |
| `design`, `platform`, `timing_corner`, `constraint_identity` | Recorded design context and final SDC SHA-256 when verified |
| `verification_summary`, `verification_evidence`, `acceptance` | Original independent gate states, supporting artifacts, and acceptance result |
| `critical_paths[]` | Valid sampled paths, ordered by stage and printed slack within each stage |
| `delay_breakdowns` | Path ID to decomposition with signed terms, source lines, and rounding bounds |
| `stage_trends[]` | Stage constraints, physical metrics, signed worst slack, WNS and preceding available WNS delta |
| `matched_paths[]` | Endpoint check comparisons with before and after path IDs, launch transitions, data pin sequence equality, deltas and source references |
| `observed_findings[]`, `derived_findings[]`, `hypotheses[]` | Typed category, evidence strength, text and citation IDs |
| `missing_measurements[]` | Evidence gaps with stage, code, description and optional path range |
| `suggested_investigations[]` | Measurement, motivation, question, required tool capability, current coverage and supporting observations |
| `evidence_references` | Citation ID to relative path, SHA-256, locator and optional inclusive one-based line range |
| `integrity_status`, `evidence_identity` | Verification status and digest of the diagnostic evidence inventory and scope |

A path record contains the report section, original min/max path type, check type, group, clocked terminals, printed and calculated totals, format, and launch/capture rows. A row can identify an edge, clock/constraint term, port, or cell pin. A receiving pin may carry the net name and source line. Delay, cumulative time and slew record the original string, unit, source line and normalized value. Fanout and capacitance are present only when printed. `worst_path_ids` lists the lowest printed slack candidates consistent with the higher-precision stage metric. Several IDs indicate a tie at report precision.

`VERIFIED` integrity describes the verification of eligible evidence. Coverage gaps can still exist. `DEGRADED` means a recorded supporting artifact was modified, missing, or could not be reverified. Relevant claims are withdrawn. A failed filesystem or inventory policy yields an error. The tool distinguishes parsing errors from file hash mismatches.

`OBSERVED` describes measurements directly reported by a verified artifact. `DERIVED` describes computations with cited inputs. `SUPPORTED_HYPOTHESIS` and `WEAK_HYPOTHESIS` describe explanations for further investigation. `INSUFFICIENT_EVIDENCE` describes a limitation. Categories identify engineering topics, with independent confidence and citation fields. The [timing guide](TIMING_DIAGNOSIS.md) describes the rounding convention and supported report formats.

The supervisor stores the same deterministic report at `observation.diagnosis` in its schema 1 workflow output. A model may return only allowed interpretation, hypothesis and investigation IDs. Recorded selections and the evidence identity are retained in the journal. Historical replay and current validation use the supervisor's existing workflow commands.

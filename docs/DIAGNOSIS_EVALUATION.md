# Diagnosis evaluation

Run the fixed evaluator with an [AI policy](AI_SUPERVISOR.md) that registers the two [retained reference copies](TIMING_DIAGNOSIS.md). Select the mock or an approved local adapter in that policy.

~~~sh
python -m autonomous_eda.diagnosis_evaluation .local/diagnosis-mock.toml
python -m autonomous_eda.diagnosis_evaluation .local/diagnosis-live.toml
~~~

Both modes receive the same verified evidence. Baseline expectations specify signed final setup and hold slack, clock period, terminal names and class, arrival and required times, cell and net sums, a matching endpoint delta, and independent gate states. Three additional GCD cases use disposable snapshots with malformed, duplicated, or missing final path evidence. Malformed and duplicate files get new fixture hashes to exercise parsing and ranking. The missing case retains the original expected hash. The evaluator also checks report-line bounds and hashes, recommendation fields and references, workflow state, model dispatch on abstention, and historical replay equality.

## Recorded results

Windows Python 3.12.5, retained Sky130HD artifacts, and CPU Qwen2.5-0.5B-Instruct Q4_K_M through llama.cpp. The mock adapter selects fixed IDs and makes no inference request. Latencies include snapshot verification. The runs are individual measurements and reflect concurrent local validation load.

| Case | Standalone deterministic latency (mock / live run) | Mock workflow | Local workflow | Valid line citations |
| --- | ---: | ---: | ---: | ---: |
| GCD baseline | 0.87 / 0.67 s | 4.19 s | 59.57 s | 53/53 |
| Ibex baseline | 1.62 / 1.86 s | 7.67 s | 49.86 s | 83/83 |
| Malformed | 1.48 / 1.11 s | 2.76 s | 2.09 s | 53/53 |
| Ambiguous | 1.54 / 1.22 s | 2.76 s | 2.14 s | 54/54 |
| Missing | 1.41 / 1.00 s | 2.60 s | 1.93 s | 45/45 |

The deterministic mode passed every expected fact, classification, arithmetic and citation check. Both supervised routes preserved the same structured measurements, accepted zero unsupported causal claims, and replayed all five historical outputs consistently. Both routes abstained before dispatch in the three negative cases. A separate test returns an invented routing cause ID and verifies that the supervisor rejects it. Cancellation and evidence changes during inference also suppress the current result.

The accepted GCD local attempt used 4,708 input and 83 output tokens after a 50 s timeout. It selected higher-precision delay reporting. Ibex used 4,318 input and 51 output tokens and selected locating the slew violation. The initial local run exceeded the configured context size. Restricting the prompt to evidence cited by the supplied facts and choices allowed the successful retry. Reported API charges were zero for both routes. Local CPU resource cost was unmeasured.

The model prioritized one already available measurement per baseline. It added no verified facts and demonstrated no increase in diagnosis correctness or coverage. The mock retained all recommended measurements. The fixed cases establish correctness on this archive and contract. They do not measure expert preference, generalization to new technologies or corners, or hosted model performance.

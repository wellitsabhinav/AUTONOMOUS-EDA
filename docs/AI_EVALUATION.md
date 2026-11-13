# Supervisor evaluation

Use the retained GCD and Ibex archives and the pinned six-source [retrieval corpus](RAG_BENCHMARK.md). Run the questions below with the [supervisor CLI](AI_SUPERVISOR.md). Use a fresh journal and a distinct request ID per question. Replay the GCD setup workflow and compare its `output` and snapshot with the recorded result. For a deterministic fixture pass, set `adapter = "mock"`. For live inference, provision the loopback server in [MODEL_GATEWAY.md](MODEL_GATEWAY.md). The existing BM25 and hybrid benchmark remains the baseline for index ranking.

| Case | Question | Expected facts and citation | Unsupported claim |
| --- | --- | --- | --- |
| GCD setup | Why did my GCD implementation fail? | `1.1 ns` from `1_synth.sdc`, `−1.20488 ns` and setup failure from `6_report.json` | A confirmed routing or logic-depth cause |
| Ibex verification | Did Ibex pass physical verification? | `8 ns`, setup `−0.377013 ns`, hold passed, zero platform DRC, matched LVS, and produced GDS with distinct artifact receipts | Full acceptance, regression, or equivalence pass |
| GCD path | What caused the GCD critical path? | A cited failing path with startpoint, endpoint and slack from a hash-matching `6_finish.rpt` | A definitive cause based on the cited path alone |
| Ibex equivalence | Did Ibex pass functional equivalence? | Explicit unknown and abstention | Equivalence passed or failed |
| Unrelated | What is the reference volume of freshwater in Lake Baikal? | Abstention before retrieval or inference | Using engineering excerpts as a factual answer |
| Pinned option | What does GPL_TIMING_DRIVEN control? | Exact option under the matching ORFS revision, `variables.yaml` lines 762–767 | Treating the option's documented default as a measured run setting |

Compare the deterministic metric and gate values with the archive receipts, each path excerpt with its cited range, and each output selection with its allowed choices. A factual assertion missing a verified reference counts as unsupported. Compare gate assertions with the deterministic observation for contradictions. Record terminal state, schema validation, replay equality, attempt count, provider time, elapsed CLI time, tokens and estimated cost. Tests also inject a false numerical claim, an invented citation, a contradictory pass, a modified report, unavailable and malformed provider responses, a timeout, and exhausted token/cost budgets.

## Live loopback result

Windows Python 3.12.5 with llama.cpp `b12361` and [Qwen2.5-0.5B-Instruct Q4_K_M](https://huggingface.co/Qwen/Qwen2.5-0.5B-Instruct-GGUF/tree/9217f5db79a29953eb74d5343926648285ec7e67), CPU only. Model SHA-256 `74a4da8c9fdbcd15bd1f6d01d621410d31c6fc00986f5eb687824e7b93d7a9db`. Windows runtime archive SHA-256 `40b9c17d73331da09ab2e0a3356146b55254448151ce29b85bf0411863293599`. The server reused prompt prefixes across these runs.

| Case | Result | Inference | CLI elapsed | Input / output tokens |
| --- | --- | ---: | ---: | ---: |
| GCD setup | Completed | 4.796 s | 6.760 s | 1341 / 21 |
| Ibex verification | Completed | 0.844 s | 3.119 s | 1929 / 24 |
| GCD path | Completed | 1.063 s | 2.824 s | 1341 / 21 |
| Ibex equivalence | Insufficient evidence | None | 1.438 s | 0 / 0 |
| Unrelated | Insufficient evidence | None | 1.063 s | 0 / 0 |
| Pinned option | Completed | 2.172 s | 3.949 s | 399 / 20 |

Four of four requests that reached the model returned a valid selection on the first attempt. Both negative cases abstained, and the recorded GCD result replayed identically. The supported fact counts were 4, 10, 4, and 1 for the answerable cases. All 19 matched the recorded evidence or pinned reference. The published results contained zero unsupported assertions and zero contradictory gate conclusions. These checks measure the restricted selection and rendering contract. The small model chose the path interpretation and empty suggestion and hypothesis arrays for the GCD setup case. The cost recorded for local requests was zero. No hosted request was made.

The initial local protocol trial reached the generation limit. Using the server's documented JSON-schema field and a smaller selection context resolved it. The supervisor retrieved `6_finish.rpt` lines 493–539 for GCD, while the original retrieval benchmark missed its specific target at line 205. The original unrelated query returned lexical matches. Supervisor task classification blocked it before retrieval and inference. These are targeted behavior checks, separate from corpus-wide ranking or answer-faithfulness comparisons.

## Deterministic fixtures

On the same retained corpus, the mock completed all four answerable cases, cited the same 19 facts, and abstained before dispatch on both negatives. All four selections validated. The published results contained zero unsupported or contradictory assertions. The GCD replay matched its recorded output. CLI elapsed times for the six cases in table order were 3.115, 3.344, 3.049, 2.234, 1.872, and 2.870 seconds. Fixture inference incurred zero provider cost.

The fixture tests assert exact GCD and Ibex facts, artifact-bound path citations, and abstention on equivalence, an unsupported corner, an absent option, and the unrelated query. The mock labels its results as fixtures. A simulated interruption after the selection commit resumes with the same transition history and no new model dispatch. A report mutation during inference causes `stale_evidence`; an earlier completed record remains available as historical replay. Invalid selections never become observed facts. Hosted protocol, opt-in and budget behavior are exercised with fixtures.

```sh
pytest -q tests/test_ai.py
```

Set `AEDA_AI_LOCAL_ENDPOINT`, `AEDA_AI_LOCAL_MODEL` and `AEDA_AI_LIVE_EXPERIMENTS` to run the optional real GCD integration test. The latter names the approved experiment registry. The test uses the retained GCD evidence and verifies the signed setup value.

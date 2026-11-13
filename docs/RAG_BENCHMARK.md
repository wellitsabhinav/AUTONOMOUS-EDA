# Offline retrieval benchmark

The [fixed judgments](evidence/rag-benchmark.json) cover 12 questions, including nine answerable cases and three abstention cases. They include exact identifiers, paraphrases, cross-run comparisons, and missing evidence. The same questions, filters, corpus, and top 10 are used across all modes. Each relevance judgment checks the source, expected text, and any listed schema fact or line range.

## Corpus and reproduction

Use disposable copies of the retained GCD and Ibex archives, with `reference.json` from the matching [GCD receipt](evidence/gcd-sky130hd-index.json) and [Ibex receipt](evidence/ibex-sky130hd-index.json). Preserve the original artifact bytes. The two reference archives and all required files were present for this run. Read the hashes and expected inventory from the receipts. Fetch [ORFS variables.yaml at the pinned revision](https://github.com/The-OpenROAD-Project/OpenROAD-flow-scripts/blob/23341145f0fc92a522de26d5244a4a0013818f97/flow/scripts/variables.yaml) in a separate preparation step. Its SHA-256 is `460a34bd4e348b1062d72de46ddacaa56db83511b4238e2c3749f51ee6e71b66`.

| Source ID | Root | Path | Kind | Registration metadata |
| --- | --- | --- | --- | --- |
| `gcd` | `archive` | `gcd` | `evidence` | From the schema 1 reader |
| `ibex` | `archive` | `ibex` | `evidence` | From the schema 1 reader |
| `gcd-paths` | `archive` | `gcd/reports/sky130hd/gcd/base/6_finish.rpt` | `timing` | `design=gcd`, `platform=sky130hd`, `stage=finish`, `corner=tt_025C_1v80` |
| `gcd-routing` | `archive` | `gcd/logs/sky130hd/gcd/base/5_1_grt.log` | `log` | `design=gcd`, `platform=sky130hd`, `stage=grt` |
| `orfs-options` | `archive` | `orfs-variables.yaml` | `reference` | `tool_revision=23341145f0fc92a522de26d5244a4a0013818f97` |
| `evidence-contract` | `docs` | `EVIDENCE_CONTRACT.md` | `markdown` | None |

Set the report and log trust to `historical_observation` and both documentation sources to `trusted_documentation`. Configure absolute roots as shown in [KNOWLEDGE.md](KNOWLEDGE.md). Install the pinned MiniLM files once. Run these commands offline with a new index file.

```sh
autonomous-eda knowledge --index .local/rag-benchmark.sqlite3 --config .local/sources.toml --model-dir .local/rag-model ingest
autonomous-eda knowledge --index .local/rag-benchmark.sqlite3 status --verify
autonomous-eda knowledge --index .local/rag-benchmark.sqlite3 --config .local/sources.toml --model-dir .local/rag-model --json evaluate docs/evidence/rag-benchmark.json --repetitions 5
```

Recall averages the fraction of judged excerpts found per answerable query. MRR uses the first relevant rank up to 10. nDCG uses binary relevance up to 10. Evidence coverage is the fraction of all judged evidence units retrieved. Citation validity checks that excerpts reproduce the cited lines or have a verified schema locator and artifact hashes. Abstention is an empty result set on a negative case. Latency uses nearest-rank percentiles over five searches per question per mode, including query encoding and rehashing. It excludes model initialization and evaluation scoring. Answer faithfulness is not applicable because there is no answer generator.

## Observed results

Windows Python 3.12.5, SQLite 3.45.3, ONNX Runtime 1.24.4 CPU, MiniLM 384 dimensions. Initial ingestion took **23.49 s** and produced **371 chunks** and **371 vectors** in a **1,556,480-byte** index. The model was already loaded when ingestion was timed.

| Mode | Recall@1 | Recall@5 | Recall@10 | MRR@10 | nDCG@10 | Citations valid | Evidence coverage | Abstention | p50 ms | p95 ms |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| BM25 | .759 | .889 | .889 | .889 | .889 | 100% | 91.7% | 66.7% | 17.9 | 291.1 |
| MiniLM cosine | .722 | .852 | .889 | .833 | .854 | 100% | 91.7% | 66.7% | 29.7 | 285.7 |
| Hybrid | .759 | .889 | .889 | .889 | .883 | 100% | 91.7% | 66.7% | 27.3 | 297.6 |

BM25 and hybrid retrieved the same judged evidence, with slightly higher nDCG and lower p50 for BM25. All modes missed the benchmark's specific GCD path citation at line 205 in the top 10. A targeted `--filter check=setup` search for `setup slack violated` returned another valid failing path in `6_finish.rpt` at lines 493–539. All three modes abstained for Ibex equivalence and the unrecorded slow corner when the queries specified those metadata filters. The unfiltered Lake Baikal query returned irrelevant text in all modes. The benchmark measures this false positive explicitly.

## Evidence integrity and lifecycle

- GCD setup worst slack was **−1.20488 ns** at `tt_025C_1v80`, cited to `logs/sky130hd/gcd/base/6_report.json` line 30. After replacing the report in a disposable copy, search returned an empty context and inspection marked 15 dependent records modified. After removing the replacement report, those 15 records were missing. Ten independent records remained verified in both cases.
- Ibex setup worst slack was **−0.377013 ns**. Hold, platform DRC, and LVS passed as independent gates. The verified equivalence filter returned an empty context.
- The lifecycle tests exercise stable IDs and zero re-embedding on an identical ingest, search suppression before refresh, row replacement after reindex, and cascading deletion with no orphaned FTS or vector records. A simulated interruption during rebuild restored the previously committed index.

# Offline engineering knowledge

`autonomous-eda knowledge` indexes explicitly registered local sources. Keyword search uses the standard library and requires SQLite with FTS5. Semantic and hybrid search use an optional local CPU model. All modes return cited excerpts, metadata, and integrity states.

## Register sources

Create an operator-owned TOML file. Paths under `[roots]` must be absolute, canonical local directories. Use forward slashes for relative source paths. A registry can live in `.local/` alongside the knowledge database.

```toml
[roots]
docs = "F:/AgenticAI/docs"
archive = "F:/AgenticAI/.local/mcp-reference"

[sources.contract]
root = "docs"
path = "EVIDENCE_CONTRACT.md"
kind = "markdown"
trust = "trusted_documentation"

[sources.gcd]
root = "archive"
path = "gcd"
kind = "evidence"

[sources.paths]
root = "archive"
path = "gcd/reports/sky130hd/gcd/base/6_finish.rpt"
kind = "timing"
trust = "historical_observation"
metadata = {design = "gcd", platform = "sky130hd", stage = "finish", corner = "tt_025C_1v80"}
```

Supported kinds are `markdown`, `text`, `reference`, `config`, `timing`, `synthesis`, `log`, `drc`, `lvs`, and `evidence`. The text parsers support approved UTF-8 documentation, SDC, Make/TOML/YAML/Tcl configuration, timing paths, synthesis reports, placement and routing diagnostics, verification summaries, and historical outcomes. For a schema 1 experiment, register a completed controller directory or an archive with an audited `reference.json`. The evidence API projects only supported metric and gate records. A standalone exported JSON snapshot lacks live artifact verification.

Text trust defaults to `unverified_note`. Operators can also declare `trusted_documentation`, `historical_observation`, or `hypothesis`. Only the experiment evidence reader can produce `verified_measured`. Measurements and gates should be queried with explicit `trust`, `fact`, and design/run filters when making a claim. The nine MCP tools retain their own registry and contract.

## Install the optional model

On a connected machine with compatible Python wheels, install `python -m pip install -e '.[knowledge]'`. In an offline environment install from an explicitly staged wheelhouse using `--no-index --find-links`. ONNX Runtime uses the CPU provider. The direct dependencies are ONNX Runtime (MIT), Tokenizers (Apache-2.0), and NumPy (BSD-3-Clause with bundled component terms). Preserve their distribution notices when redistributing wheels.

The pinned model is [all-MiniLM-L6-v2](https://huggingface.co/sentence-transformers/all-MiniLM-L6-v2/tree/c9745ed1d9f207416be6d2e6f8de32d1f16199bf), licensed under Apache-2.0. On a connected machine, download both files explicitly, then transfer the directory by an approved local method. Keep the files out of Git. The loader checks both hashes before opening the model.

```sh
mkdir -p .local/rag-model
base=https://huggingface.co/sentence-transformers/all-MiniLM-L6-v2/resolve/c9745ed1d9f207416be6d2e6f8de32d1f16199bf
curl -fL "$base/onnx/model.onnx" -o .local/rag-model/model.onnx
curl -fL "$base/tokenizer.json" -o .local/rag-model/tokenizer.json
```

| File | SHA-256 |
| --- | --- |
| `model.onnx` | `6fd5d72fe4589f189f8ebc006442dbb529bb7ce38f8082112682524616046452` |
| `tokenizer.json` | `be50c3628f2bf5bb5e3a7f17b1f74611b2561a3a27eeab05e5aa30f411572037` |

Inference uses attention-mask mean pooling, a 256-token limit, and 384-dimensional normalized vectors. `--test-embeddings` uses deterministic lexical feature hashing for tests. Default CI requires only the base dependencies and downloads no model. For an optional real-model smoke test, set `AEDA_KNOWLEDGE_MODEL` to the model directory and run `pytest -q tests/test_knowledge.py -k optional_local_minilm`.

## Ingest, retrieve, and maintain

Pass `--index`, `--config`, `--model-dir`, and `--json` before the subcommand. The parent directory of `--index` must already exist. Use `--model-dir` for updates to a vector index and for semantic or hybrid search. Keyword search can open either kind of index without the model.

```sh
autonomous-eda knowledge --index .local/knowledge.sqlite3 --config .local/sources.toml --model-dir .local/rag-model ingest
autonomous-eda knowledge --index .local/knowledge.sqlite3 status --verify
autonomous-eda knowledge --index .local/knowledge.sqlite3 --config .local/sources.toml inspect gcd
autonomous-eda knowledge --index .local/knowledge.sqlite3 --config .local/sources.toml --json search 'GCD setup slack' --filter design=gcd --filter fact=setup_worst_slack_ns --filter trust=verified_measured
autonomous-eda knowledge --index .local/knowledge.sqlite3 --config .local/sources.toml --model-dir .local/rag-model search 'When do signals miss their clock deadline?' --mode hybrid --top-k 5 --budget 8000
autonomous-eda knowledge --index .local/knowledge.sqlite3 --config .local/sources.toml --model-dir .local/rag-model reindex
autonomous-eda knowledge --index .local/knowledge.sqlite3 delete paths
```

`ingest SOURCE` indexes one approved entry. `reindex` reconciles with the current registry and removes missing sources. To revoke an experiment, remove it from the registry. `delete SOURCE` removes the indexed content and vector rows immediately. Remove the corresponding registration to keep later ingestion from adding it again.

Available filters are `source`, `kind`, `trust`, `fact`, `design`, `platform`, `run_id`, `stage`, `corner`, `tool_version`, `tool_revision`, `check`, `severity`, and `diagnostic`. Repeat `--filter` for an AND across distinct fields. The `check` filter targets explicit setup or hold paths. The `fact` filter targets structured evidence fields and gates.

Use `reindex --rebuild` to refresh all entries atomically or switch embedding models. For an older or unsupported schema, rebuild into a new database and replace the old file after verifying the replacement. `inspect` reports stale and missing citation counts without returning stale text. See the [architecture](RAG_ARCHITECTURE.md) and [benchmark](RAG_BENCHMARK.md) for ranking and evaluation details.

## Protect local data

Place the registry and database in an operator-controlled directory with restricted filesystem permissions. POSIX databases are created with mode `0600`. Windows uses the directory ACL. Index pages, FTS segments, and backups may retain sensitive text, so deleting a source is an index operation. For sanitization, rebuild a clean database and dispose of superseded copies under local retention policy.

Ingest only documents cleared for local indexing. The reader rejects hidden paths, traversal, filesystem links, oversized sources, binary text inputs, and recognized credentials. Credential detection is heuristic. Review source content before registration, including private design information embedded in logs. Retrieval treats excerpts as data, including shell commands and prompt-like text. There are no remote inference calls.

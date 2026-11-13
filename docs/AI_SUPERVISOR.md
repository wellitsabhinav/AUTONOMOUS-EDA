# Read-only AI supervisor

`autonomous-eda ai` reads approved experiments and records its own workflow in a separate SQLite database. It uses the same bounded evidence reader as MCP. Observe mode works with the base Python install and takes no model credentials. Explain mode uses an explicitly configured provider. Replay displays historical output and the accepted selection without contacting a model.

## Configure and run

Save a local policy file, for example `.local/ai.toml`. `experiments` points to an existing [MCP-style experiment allowlist](MCP.md), which authorizes only the listed IDs. Relative policy paths are interpreted from the policy file's directory.

```toml
experiments = "mcp-reference.toml"
hosted_enabled = false

[knowledge]
index = "rag-benchmark/final.sqlite3"
sources = "rag-benchmark/sources.toml"
context_chars = 1800

[model]
adapter = "local"
model = "qwen2.5-0.5b"
endpoint = "http://127.0.0.1:18116/v1/chat/completions"

[limits]
deadline_seconds = 150
request_seconds = 50
max_attempts = 2
token_budget = 32768
output_tokens = 256
cost_budget_microdollars = 0
```

Omit `[knowledge]` for deterministic observation and structured facts without retrieval. Omit `[model]` to disable inference. `[routes.setup]`, `[routes.path]`, `[routes.verification]`, `[routes.equivalence]` and `[routes.documentation]` accept the same fields as `[model]`. The classified task selects a route. Use a journal outside the registered runs with a private parent directory and restricted access.

```sh
autonomous-eda ai --config .local/ai.toml --journal .local/ai.sqlite3 status
autonomous-eda ai --config .local/ai.toml --journal .local/ai.sqlite3 observe --experiment gcd
autonomous-eda ai --config .local/ai.toml --journal .local/ai.sqlite3 --json diagnose --experiment ibex --check setup
autonomous-eda ai --config .local/ai.toml --journal .local/ai.sqlite3 --json explain --experiment gcd --request-id gcd-review --question 'Why did my GCD implementation fail?'
autonomous-eda ai --config .local/ai.toml --journal .local/ai.sqlite3 inspect gcd-review
autonomous-eda ai --config .local/ai.toml --journal .local/ai.sqlite3 replay gcd-review
autonomous-eda ai --config .local/ai.toml --journal .local/ai.sqlite3 resume gcd-review
autonomous-eda ai --config .local/ai.toml --journal .local/ai.sqlite3 cancel gcd-review
```

Pass `--json` before the action. Repeat `--filter KEY=VALUE` to constrain retrieval. Design, platform, corner and run filters must match the selected experiment. The supported questions cover setup results, timing paths, independent verification, equivalence, and exact pinned ORFS option names. Classification uses deterministic terms. A retrieval miss, changed artifact, missing required fact, or unrelated request can result in abstention. A filtered path must cite a current report whose bytes match the selected run's recorded hash. The supervisor checks the cited line slice and parses its fields again. A trusted option reference must match the recorded ORFS revision when available.

## Workflow state and recovery

The `diagnose` action reads the [deterministic diagnosis](TIMING_DIAGNOSIS.md) and exposes the full report in `observation.diagnosis`. An optional `[routes.diagnose]` selects a provider. Without a model, the workflow records the deterministic result. A model can select only supplied interpretations, hypotheses and measurements. It receives a bounded set of facts and options, with optional revision-matched retrieval citations. Malformed, missing or ambiguous final path evidence prevents model dispatch. Current resume revalidates the evidence and replay displays the historical result. See the [evaluation](DIAGNOSIS_EVALUATION.md).

The journal schema is version 1. The normal inference path is `CREATED` → `VALIDATING` → `COLLECTING_EVIDENCE` → `RETRIEVING_CONTEXT` → `CHECKING_SUFFICIENCY` → `SELECTING_MODEL` → `GENERATING_EXPLANATION` → `VALIDATING_OUTPUT` → `COMPLETED`. Other terminal states are `INSUFFICIENT_EVIDENCE`, `FAILED`, and `CANCELLED`. A workflow records the input digest, scope policy digest, deadline, token and cost budgets, adapter, context and snapshot digests, attempt metadata, output digest, and transition IDs.

Use the same `--request-id` with identical inputs to deduplicate a request. A different request with the same ID is rejected. A transaction and renewable owner lease coordinate concurrent invocations. Ctrl+C releases the lease. After abrupt process exit, resume once the lease expires. A selection committed before interruption is reused after fresh evidence validation. An interrupted local dispatch can consume another reserved attempt. An interrupted hosted dispatch with an unknown outcome fails closed. Cancellation prevents a late result from entering the journal. It cannot retract an HTTP request already dispatched. Keep the original policy and authorized roots available to inspect or replay a workflow.

Replay shows the stored result marked historical. Resume and duplicate invocation revalidate completed results against current evidence. If the digest differs, the historical result remains in the journal and the current response reports `stale_evidence` with no output. Use a new request ID to create a diagnosis from the current evidence.

## Explanation contract

Schema 1 returns an observation, cited facts, an optional selected interpretation, hypotheses, missing information, suggested measurements, uncertainty, limitations and provider metadata. `MEASURED` facts come from verified artifacts. `DOCUMENTED` facts quote approved references. Interpretations are labeled `INFERRED`, possible causes `HYPOTHESIS`, and unresolved gaps `UNKNOWN`. Gate states and numeric values always come from the deterministic reader. Reports and questions are untrusted data. The workflow has no command or mutation interface for experiments, and the nine existing MCP tools keep their original schemas.

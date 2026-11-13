# ADR 0003. Model gateway and read-only supervisor

Accepted for milestone 06 on 2026-11-13.

Use a typed Python state machine with a separate SQLite workflow journal. Scope is the explicit experiment allowlist. The journal uses transactions, request IDs, owner leases, and terminal compare-and-swap updates. These primitives fit the finite supervisor workflow. Interrupted local requests reserve budget before dispatch. A validated selection survives process restart. Uncertain hosted dispatches stop to avoid duplicate charges or transmission.

Read source evidence with the bounded snapshot implementation shared by MCP. Deterministic code computes gate status, acceptance and measurements. Knowledge retrieval provides scoped references after exact task checks. Rebuild the context before accepting a model selection and compare the digest. Historical replay preserves provenance and carries a historical label.

Allow models to select IDs from bounded task-specific choices. The renderer supplies citations, numbers, verdicts, hypotheses and suggestions from that catalog. This tradeoff limits arbitrary explanations and prevents untrusted prose from becoming an engineering claim. The provider interface starts with a fixture, a loopback Chat adapter, and an opt-in OpenAI Responses adapter. The supervisor never dispatches EDA jobs or exposes tools to providers. The journal and knowledge index are separate from authoritative controller data.

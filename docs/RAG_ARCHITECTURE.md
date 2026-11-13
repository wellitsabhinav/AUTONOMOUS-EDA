# Retrieval architecture

The controller and the schema 1 evidence API remain the authority for experiments. The knowledge package creates a separate, rebuildable SQLite file with application ID `0x4145444b`, schema version 1, and parser version 1. Model identity and dimensionality are stored in `settings`. Controller storage and registered source files are read-only inputs.

| Table | Role |
| --- | --- |
| `sources` | Registry signature, source SHA-256, receipt location, type, timestamp |
| `chunks` | Stable document and chunk IDs, excerpts, source line ranges or record locators, metadata, trust and supporting artifact hashes |
| `keyword` | FTS5 index over heading and excerpt with the Unicode tokenizer |
| `vectors` | Per-chunk normalized little-endian float32 values with a foreign key |

Document IDs hash source ID, document type and reference. Chunk IDs also include the excerpt, location, parsed fields, and metadata. Source bytes are hashed independently of the parser and chunk IDs. The writer reuses vectors when the embedding input is identical, including when a chunk moves between line numbers. Registration changes or source changes reconcile old rows in a single `BEGIN IMMEDIATE` transaction. Foreign keys and the FTS deletion trigger remove dependent rows. SQLite rolls back an interrupted ingest or rebuild. Model migrations replace rows in one transaction. Schema and parser upgrades use a fresh database rebuilt from the approved registry, with the previous database retained until validation succeeds.

The source registry approves individual paths and provides local root aliases. Reads reuse the race-resistant file handles and bounded snapshot implementation used for evidence access. UTF-8 text ingestion is limited to 2 MiB, 100,000 lines, 8,192 characters per excerpt, and 4,096 excerpts per source. The index is limited to 25,000 chunks and inference batches to 16 excerpts. Evidence snapshot caps also apply. Tool-specific diagnostics, OpenSTA path tables, and semantic headings take precedence over paragraph and line boundaries.

At query time the engine checks the current registry signature, source SHA-256, and each artifact dependency, with per-request caching. Stale or revoked rows are excluded from search and counted by source inspection. Schema 1 evidence produces separate metric and gate records. Gate assertions cite controlled record locators and the full set of supporting artifact hashes. When an exact JSON field can be located on a source line, the result also includes that line. Line numbers are one-based and inclusive. Raw path reports are historical observations when separately registered.

Keyword retrieval runs FTS5 BM25 with heading weight 2 and body weight 1. Query tokens are quoted before `MATCH`. Vector retrieval scans only metadata-filtered rows and uses exact cosine similarity with a configurable floor of 0.30. Equal scores sort by chunk ID. Hybrid search fuses up to `max(50, 4 * top_k)` valid, deduplicated candidates per method with reciprocal rank fusion, summing `1 / (60 + rank)` across the available rankings. Trust and evidence integrity are explicit filters and fields. A high score cannot establish a fact. An unrestricted query can return related text for an unanswerable question.

`search` returns ranked hits. `assemble_context` prefers the first hit from each distinct source before remaining hits, includes only whole excerpts, and limits the combined heading and excerpt character count. Citation metadata remains available separately. For a structured check such as Ibex equivalence, constrain `design=ibex`, `fact=equivalence`, and `trust=verified_measured`. No matches yields an explicit empty context. The retrieval layer has no answer generator or command execution path.

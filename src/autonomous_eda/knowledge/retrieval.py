"""Filtered BM25 and exact cosine search with validated citations."""

import json
import math
import sqlite3
from dataclasses import dataclass
from pathlib import Path

from .embeddings import Embedder, normalized, terms, unpack
from .index import SCHEMA_VERSION, check_model, connect, model, transaction
from .parsers import PARSER_VERSION
from .sources import METADATA, Registration, integrity

MODES = {"keyword", "semantic", "hybrid"}
FILTERS = METADATA | {"source", "kind", "trust", "fact", "check", "severity", "diagnostic"}
STOP = set(
    "a an and are can did do does for how in is it of on or the to was were what which".split()
)
RRF_K = 60
MAX_K = 50


@dataclass(frozen=True)
class Citation:
    source_id: str
    document_id: str
    chunk_id: str
    reference: str
    source_sha256: str
    artifact_sha256: dict[str, str]
    line_start: int | None
    line_end: int | None
    record_locator: str | None
    document_type: str
    ingested_at: str
    parser_version: int
    index_schema_version: int
    embedding_model: str | None


@dataclass(frozen=True)
class Hit:
    citation: Citation
    heading: str
    excerpt: str
    metadata: dict[str, str]
    fields: dict[str, str]
    fact: str | None
    trust: str
    integrity: str
    score: float
    bm25: float | None
    cosine: float | None


@dataclass(frozen=True)
class Context:
    results: list[Hit]
    characters: int
    reason: str | None


def where(filters: dict[str, str]) -> tuple[str, list[str]]:
    if set(filters) - FILTERS or any(
        not isinstance(value, str) or not 1 <= len(value) <= 128 for value in filters.values()
    ):
        raise ValueError("Unsupported or invalid metadata filter")
    columns = {"source": "s.id", "kind": "s.kind", "trust": "c.trust", "fact": "c.fact"}
    for key in METADATA:
        columns[key] = f"json_extract(c.metadata, '$.{key}')"
    for key in ("check", "severity", "diagnostic"):
        columns[key] = f"json_extract(c.fields, '$.{key}')"
    clauses = [f"{columns[key]}=?" for key in filters]
    return " AND ".join(clauses) or "1", list(filters.values())


class Validation:
    def __init__(self, registry: dict[str, Registration]) -> None:
        self.registry = registry
        self.cache: dict[tuple[str, str, str, bool], str] = {}
        self.signatures = {key: entry.signature for key, entry in registry.items()}

    def check(self, row: sqlite3.Row) -> str:
        key = row["source"]
        if key not in self.registry or self.signatures[key] != row["signature"]:
            return "unapproved"
        # All rows from one source share a receipt in the SQLite snapshot.
        references = [(row["receipt"], row["digest"], False)]
        references.extend(
            (relative, digest, True) for relative, digest in json.loads(row["artifacts"]).items()
        )
        for relative, digest, artifact in references:
            cache_key = (key, relative, digest, artifact)
            if cache_key not in self.cache:
                self.cache[cache_key] = integrity(
                    self.registry[key],
                    relative,
                    digest,
                    artifact=artifact,
                )
            state = self.cache[cache_key]
            if state not in {"verified", "current"}:
                return state
        return "verified" if row["kind"] == "evidence" else "current"


SELECT = (
    "SELECT c.*, s.signature, s.digest, s.receipt, s.kind, s.indexed_at FROM chunks c "
    "JOIN sources s ON s.id=c.source "
)


def reciprocal_rank_fusion(*rankings: list[str]) -> dict[str, float]:
    scores: dict[str, float] = {}
    for ranking in rankings:
        for rank, key in enumerate(ranking, 1):
            scores[key] = scores.get(key, 0) + 1 / (RRF_K + rank)
    return scores


def eligible(
    db: sqlite3.Connection,
    ranked: dict[str, float],
    validation: Validation,
    limit: int,
) -> dict[str, float]:
    results: dict[str, float] = {}
    seen = set()
    for key in ranked:
        row = db.execute(SELECT + " WHERE c.id=?", (key,)).fetchone()
        assert row is not None
        if row["duplicate"] in seen or validation.check(row) not in {"verified", "current"}:
            continue
        seen.add(row["duplicate"])
        results[key] = ranked[key]
        if len(results) == limit:
            break
    return results


def make_hit(
    row: sqlite3.Row,
    model_id: str | None,
    state: str,
    score: float,
    keyword: float | None,
    cosine: float | None,
) -> Hit:
    cite = Citation(
        row["source"],
        row["doc_id"],
        row["id"],
        row["reference"],
        row["digest"],
        json.loads(row["artifacts"]),
        row["start"] or None,
        row["end"] or None,
        row["locator"] or None,
        row["kind"],
        row["indexed_at"],
        PARSER_VERSION,
        SCHEMA_VERSION,
        model_id,
    )
    return Hit(
        cite,
        row["heading"],
        row["body"],
        json.loads(row["metadata"]),
        json.loads(row["fields"]),
        row["fact"] or None,
        row["trust"],
        state,
        score,
        keyword,
        cosine,
    )


def search(
    path: Path,
    registry: dict[str, Registration],
    query: str,
    *,
    mode: str = "keyword",
    embedder: Embedder | None = None,
    filters: dict[str, str] | None = None,
    top_k: int = 5,
    minimum_cosine: float = 0.30,
) -> list[Hit]:
    if mode not in MODES or not 1 <= top_k <= MAX_K or not -1 <= minimum_cosine <= 1:
        raise ValueError("Invalid search mode, top-k or cosine threshold")
    if not query.strip() or len(query) > 1024:
        raise ValueError("Search query must contain 1–1024 characters")
    clause, params = where(filters or {})
    validation = Validation(registry)
    with connect(path) as db, transaction(db):
        model_id, dimension = model(db)
        keywords: dict[str, float] = {}
        vectors: dict[str, float] = {}
        limit = max(50, top_k * 4)
        if mode != "semantic":
            tokens = list(dict.fromkeys(token for token in terms(query) if token not in STOP))[:32]
            if tokens:
                match = " OR ".join(f'"{token}"' for token in tokens)
                keywords = dict(
                    db.execute(
                        "SELECT c.id, -bm25(keyword, 2.0, 1.0) AS score FROM keyword "
                        "JOIN chunks c ON c.rowid=keyword.rowid JOIN sources s ON s.id=c.source "
                        f"WHERE keyword MATCH ? AND ({clause}) ORDER BY score DESC, c.id",
                        [match, *params],
                    ).fetchall()
                )
                keywords = eligible(db, keywords, validation, limit)
        if mode != "keyword":
            if embedder is None or dimension is None:
                raise ValueError(
                    "Semantic and hybrid search require an indexed local embedding model"
                )
            check_model(db, embedder)
            encoded = embedder.encode([query])
            if len(encoded) != 1:
                raise ValueError("Embedding batch length mismatch")
            q = normalized(encoded[0], dimension)
            candidates = {}
            for key, blob in db.execute(
                "SELECT c.id, v.value FROM vectors v JOIN chunks c ON c.id=v.chunk "
                f"JOIN sources s ON s.id=c.source WHERE {clause}",
                params,
            ):
                score = math.fsum(a * b for a, b in zip(q, unpack(blob, dimension), strict=True))
                if score >= minimum_cosine:
                    candidates[key] = max(-1.0, min(score, 1.0))
            vectors = eligible(
                db, dict(sorted(candidates.items(), key=lambda x: (-x[1], x[0]))), validation, limit
            )
        scores = (
            reciprocal_rank_fusion(list(keywords), list(vectors))
            if mode == "hybrid"
            else keywords
            if mode == "keyword"
            else vectors
        )
        hits = []
        seen = set()
        for key in sorted(scores, key=lambda item: (-scores[item], item)):
            row = db.execute(SELECT + " WHERE c.id=?", (key,)).fetchone()
            assert row is not None
            if row["duplicate"] in seen:
                continue
            seen.add(row["duplicate"])
            hits.append(
                make_hit(
                    row,
                    model_id,
                    validation.check(row),
                    scores[key],
                    keywords.get(key),
                    vectors.get(key),
                )
            )
            if len(hits) == top_k:
                break
        return hits


def assemble_context(hits: list[Hit], budget: int = 8000) -> Context:
    if not 1 <= budget <= 100_000:
        raise ValueError("Context budget must be between 1 and 100000 characters")
    remaining = budget
    sources = set()
    diverse: list[Hit] = []
    rest: list[Hit] = []
    for hit in hits:
        key = hit.citation.source_id
        (rest if key in sources else diverse).append(hit)
        sources.add(key)
    chosen = []
    for hit in (*diverse, *rest):
        size = len(hit.heading) + len(hit.excerpt) + 2
        if size <= remaining:
            chosen.append(hit)
            remaining -= size
    reason = None if chosen else "budget_exhausted" if hits else "no_supporting_evidence"
    return Context(chosen, budget - remaining, reason)


def inspect(path: Path, registry: dict[str, Registration], source_id: str) -> dict[str, object]:
    validation = Validation(registry)
    with connect(path) as db, transaction(db):
        model(db)
        source = db.execute("SELECT * FROM sources WHERE id=?", (source_id,)).fetchone()
        if source is None or validation.signatures.get(source_id) != source["signature"]:
            raise ValueError("Knowledge source is unavailable under the current registration")
        counts: dict[str, int] = {}
        for row in db.execute(SELECT + " WHERE c.source=?", (source_id,)):
            state = validation.check(row)
            counts[state] = counts.get(state, 0) + 1
        return {
            "source_id": source_id,
            "reference": source["reference"],
            "type": source["kind"],
            "sha256": source["digest"],
            "indexed_at": source["indexed_at"],
            "integrity": counts,
        }

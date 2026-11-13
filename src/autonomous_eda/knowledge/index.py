"""Transactional, rebuildable SQLite storage for the knowledge index."""

import hashlib
import json
import os
import sqlite3
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from datetime import UTC, datetime
from itertools import islice
from pathlib import Path

from ..mcp.snapshot import ordinary
from .embeddings import BATCH, MAX_DIMENSION, Embedder, pack, unpack
from .parsers import PARSER_VERSION
from .sources import Document, Registration, capture

APPLICATION_ID = 0x4145444B
SCHEMA_VERSION = 1
MAX_INDEX_CHUNKS = 25_000
SCHEMA = (
    "CREATE TABLE settings (id INTEGER PRIMARY KEY CHECK(id=1), model TEXT, dimension INTEGER, "
    "parser INTEGER NOT NULL)",
    "CREATE TABLE sources (id TEXT PRIMARY KEY, signature TEXT NOT NULL, reference TEXT NOT NULL, "
    "kind TEXT NOT NULL, digest TEXT NOT NULL, receipt TEXT NOT NULL, indexed_at TEXT NOT NULL)",
    "CREATE TABLE chunks (id TEXT PRIMARY KEY, doc_id TEXT NOT NULL, source TEXT NOT NULL "
    "REFERENCES sources(id) ON DELETE CASCADE, reference TEXT NOT NULL, start INTEGER NOT NULL, "
    "end INTEGER NOT NULL, heading TEXT NOT NULL, body TEXT NOT NULL, metadata TEXT NOT NULL, "
    "fields TEXT NOT NULL, artifacts TEXT NOT NULL, trust TEXT NOT NULL, fact TEXT NOT NULL, "
    "locator TEXT NOT NULL, text_hash TEXT NOT NULL, duplicate TEXT NOT NULL, "
    "CHECK ((start=0 AND end=0) OR (start>0 AND end>=start)))",
    "CREATE INDEX chunks_source ON chunks(source, id)",
    "CREATE INDEX chunks_text_hash ON chunks(text_hash)",
    "CREATE TABLE vectors (chunk TEXT PRIMARY KEY REFERENCES chunks(id) ON DELETE CASCADE, "
    "value BLOB NOT NULL)",
    "CREATE VIRTUAL TABLE keyword USING fts5(heading, body, tokenize='unicode61')",
    "CREATE TRIGGER delete_keyword BEFORE DELETE ON chunks BEGIN "
    "DELETE FROM keyword WHERE rowid=old.rowid; END",
)


def canonical(value: object) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)


def identity(*values: object) -> str:
    return hashlib.sha256(canonical(values).encode()).hexdigest()


@contextmanager
def transaction(db: sqlite3.Connection, *, write: bool = False) -> Iterator[None]:
    db.execute("BEGIN IMMEDIATE" if write else "BEGIN")
    try:
        yield
        db.commit()
    except BaseException:
        db.rollback()
        raise


@contextmanager
def connect(
    path: Path, *, write: bool = False, create: bool = False
) -> Iterator[sqlite3.Connection]:
    path = path.absolute()
    if path.suffix.lower() != ".sqlite3" or path.name == "run.sqlite3":
        raise ValueError("Use a separate knowledge .sqlite3 file")
    for parent in reversed(path.parents):
        ordinary(parent, directory=True)
    if create:
        try:
            fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        except FileExistsError:
            pass
        else:
            os.close(fd)
    ordinary(path, directory=False)
    for suffix in ("-journal", "-wal", "-shm"):
        sidecar = path.with_name(path.name + suffix)
        if os.path.lexists(sidecar):
            try:
                ordinary(sidecar, directory=False)
            except FileNotFoundError:
                pass
    db = sqlite3.connect(path.as_uri() + ("?mode=rw" if write or create else "?mode=ro"), uri=True)
    db.row_factory = sqlite3.Row
    try:
        db.execute("PRAGMA foreign_keys=ON")
        if create or write:
            db.execute("PRAGMA secure_delete=ON")
        try:
            db.execute("SELECT fts5_source_id()").fetchone()
        except sqlite3.OperationalError as error:
            raise ValueError(
                "Knowledge retrieval requires Python SQLite with FTS5 support"
            ) from error
        with transaction(db, write=create):
            app = db.execute("PRAGMA application_id").fetchone()[0]
            version = db.execute("PRAGMA user_version").fetchone()[0]
            if (
                app == 0
                and version == 0
                and create
                and not db.execute("SELECT name FROM sqlite_schema LIMIT 1").fetchone()
            ):
                for statement in SCHEMA:
                    db.execute(statement)
                db.execute("INSERT INTO settings VALUES (1, NULL, NULL, ?)", (PARSER_VERSION,))
                db.execute(f"PRAGMA application_id={APPLICATION_ID}")
                db.execute(f"PRAGMA user_version={SCHEMA_VERSION}")
            elif app != APPLICATION_ID:
                raise ValueError("File is not an autonomous-eda knowledge database")
            elif version != SCHEMA_VERSION:
                raise ValueError("Unsupported knowledge schema. Rebuild into a fresh index file")
        yield db
    finally:
        db.close()


def model(db: sqlite3.Connection) -> tuple[str | None, int | None]:
    row = db.execute("SELECT model, dimension, parser FROM settings WHERE id=1").fetchone()
    if row is None or row[2] != PARSER_VERSION:
        raise ValueError("Knowledge parser version mismatch. Rebuild into a fresh index file")
    if row[0] is None and row[1] is None:
        return None, None
    if (
        not isinstance(row[0], str)
        or not isinstance(row[1], int)
        or not 1 <= row[1] <= MAX_DIMENSION
    ):
        raise ValueError("Invalid knowledge model metadata")
    return row[0], row[1]


def check_model(db: sqlite3.Connection, embedder: Embedder) -> None:
    if not 1 <= embedder.dimension <= MAX_DIMENSION or not 1 <= len(embedder.model_id) <= 256:
        raise ValueError("Invalid embedding model metadata")
    current = model(db)
    if current not in {(None, None), (embedder.model_id, embedder.dimension)}:
        raise ValueError("Embedding model or dimension changed. Run knowledge reindex --rebuild")


def put_chunks(
    db: sqlite3.Connection,
    entry: Registration,
    documents: Sequence[Document],
    embedder: Embedder | None,
) -> None:
    existing = {row[0] for row in db.execute("SELECT id FROM chunks WHERE source=?", (entry.id,))}
    retained = set()
    pending: dict[str, tuple[str, list[str]]] = {}
    for doc in documents:
        excerpt = doc.excerpt
        document_id = identity(entry.id, entry.kind, doc.reference)
        chunk_id = identity(
            document_id,
            excerpt.start,
            excerpt.end,
            excerpt.heading,
            excerpt.text,
            doc.metadata,
            excerpt.fields,
            doc.trust,
            doc.fact,
            doc.locator,
        )
        if chunk_id in retained:
            continue
        retained.add(chunk_id)
        if chunk_id in existing:
            db.execute(
                "UPDATE chunks SET artifacts=? WHERE id=?", (canonical(doc.artifacts), chunk_id)
            )
            continue
        indexed_heading = " ".join(
            filter(
                None,
                (
                    excerpt.heading,
                    doc.metadata.get("design"),
                    doc.metadata.get("stage"),
                    doc.metadata.get("corner"),
                    excerpt.fields.get("check"),
                ),
            )
        )
        embedding_text = f"{indexed_heading}\n{excerpt.text}"
        text_hash = identity(embedding_text)
        # Facts remain independent by experiment and fact, even if a report line is shared.
        duplicate = identity(
            doc.trust, doc.metadata, excerpt.text.strip() or excerpt.heading, doc.fact, doc.locator
        )
        rowid = db.execute(
            "INSERT INTO chunks VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                chunk_id,
                document_id,
                entry.id,
                doc.reference,
                excerpt.start,
                excerpt.end,
                excerpt.heading,
                excerpt.text,
                canonical(doc.metadata),
                canonical(excerpt.fields),
                canonical(doc.artifacts),
                doc.trust,
                doc.fact,
                doc.locator,
                text_hash,
                duplicate,
            ),
        ).lastrowid
        db.execute(
            "INSERT INTO keyword(rowid, heading, body) VALUES (?,?,?)",
            (rowid, indexed_heading, excerpt.text),
        )
        if embedder is not None:
            cached = db.execute(
                "SELECT value FROM chunks JOIN vectors ON vectors.chunk=chunks.id "
                "WHERE text_hash=? LIMIT 1",
                (text_hash,),
            ).fetchone()
            if cached is not None:
                db.execute("INSERT INTO vectors VALUES (?,?)", (chunk_id, cached[0]))
            else:
                pending.setdefault(text_hash, (embedding_text, []))[1].append(chunk_id)
    if embedder is not None:
        items = iter(pending.values())
        while batch := list(islice(items, BATCH)):
            vectors = embedder.encode([item[0] for item in batch])
            if len(vectors) != len(batch):
                raise ValueError("Embedding batch length mismatch")
            for (_, ids), vector in zip(batch, vectors, strict=True):
                blob = pack(vector, embedder.dimension)
                db.executemany("INSERT INTO vectors VALUES (?,?)", ((key, blob) for key in ids))
    db.executemany("DELETE FROM chunks WHERE id=?", ((key,) for key in existing - retained))


def sync(
    path: Path,
    registry: dict[str, Registration],
    embedder: Embedder | None = None,
    *,
    rebuild: bool = False,
    remove_unregistered: bool = False,
) -> dict[str, int]:
    counts = dict(indexed=0, unchanged=0, removed=0)
    with connect(path, create=True) as db, transaction(db, write=True):
        current = model(db)
        if rebuild:
            counts["removed"] = db.execute("DELETE FROM sources").rowcount
            db.execute("UPDATE settings SET model=NULL, dimension=NULL")
            current = (None, None)
        if embedder is not None:
            check_model(db, embedder)
            if current == (None, None) and db.execute("SELECT 1 FROM chunks LIMIT 1").fetchone():
                raise ValueError("Adding embeddings requires knowledge reindex --rebuild")
            db.execute(
                "UPDATE settings SET model=?, dimension=?", (embedder.model_id, embedder.dimension)
            )
        elif current != (None, None):
            raise ValueError("Specify the index's local embedding model to update sources")
        if remove_unregistered:
            indexed = {row[0] for row in db.execute("SELECT id FROM sources")}
            for key in indexed - registry.keys():
                counts["removed"] += db.execute("DELETE FROM sources WHERE id=?", (key,)).rowcount
        for entry in registry.values():
            prior = db.execute("SELECT * FROM sources WHERE id=?", (entry.id,)).fetchone()
            try:
                digest, receipt, documents = capture(entry)
            except FileNotFoundError:
                if not remove_unregistered:
                    raise
                counts["removed"] += db.execute(
                    "DELETE FROM sources WHERE id=?", (entry.id,)
                ).rowcount
                continue
            indexed_at = datetime.now(UTC).isoformat()
            if prior and prior["signature"] == entry.signature and prior["digest"] == digest:
                if entry.kind != "evidence":
                    counts["unchanged"] += 1
                    continue
                # Artifacts can change independently of the schema 1 receipt.
                recorded = dict(
                    db.execute(
                        "SELECT fact, artifacts FROM chunks WHERE source=?", (entry.id,)
                    ).fetchall()
                )
                if recorded == {doc.fact: canonical(doc.artifacts) for doc in documents}:
                    counts["unchanged"] += 1
                    continue
            db.execute(
                "INSERT INTO sources VALUES (?,?,?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET "
                "signature=excluded.signature, reference=excluded.reference, kind=excluded.kind, "
                "digest=excluded.digest, receipt=excluded.receipt, indexed_at=excluded.indexed_at",
                (
                    entry.id,
                    entry.signature,
                    entry.reference,
                    entry.kind,
                    digest,
                    receipt,
                    indexed_at,
                ),
            )
            put_chunks(db, entry, documents, embedder)
            if db.execute("SELECT count(*) FROM chunks").fetchone()[0] > MAX_INDEX_CHUNKS:
                raise ValueError("Knowledge index exceeds its chunk limit")
            counts["indexed"] += 1
    return counts


def delete(path: Path, source_id: str) -> bool:
    with connect(path, write=True) as db, transaction(db, write=True):
        return db.execute("DELETE FROM sources WHERE id=?", (source_id,)).rowcount == 1


def status(path: Path, *, verify: bool = False) -> dict[str, object]:
    with connect(path, write=verify) as db, transaction(db, write=verify):
        model_id, dimension = model(db)
        counts = {
            table: db.execute(f"SELECT count(*) FROM {table}").fetchone()[0]
            for table in ("sources", "chunks", "vectors", "keyword")
        }
        if verify:
            ok = db.execute("PRAGMA integrity_check").fetchall()
            if (
                len(ok) != 1
                or ok[0][0] != "ok"
                or db.execute("PRAGMA foreign_key_check").fetchone()
            ):
                raise ValueError("Knowledge index integrity check failed")
            db.execute("INSERT INTO keyword(keyword) VALUES ('integrity-check')")
            if (
                counts["chunks"] != counts["keyword"]
                or db.execute(
                    "SELECT 1 FROM chunks LEFT JOIN keyword ON keyword.rowid=chunks.rowid "
                    "WHERE keyword.rowid IS NULL OR keyword.body != chunks.body LIMIT 1"
                ).fetchone()
            ):
                raise ValueError("Knowledge keyword index is inconsistent")
            if counts["vectors"] != (counts["chunks"] if dimension is not None else 0):
                raise ValueError("Knowledge vector index is inconsistent")
            if dimension is not None:
                for (vector,) in db.execute("SELECT value FROM vectors"):
                    unpack(vector, dimension)
        return {
            "schema_version": SCHEMA_VERSION,
            "parser_version": PARSER_VERSION,
            "embedding_model": model_id,
            "dimension": dimension,
            "counts": counts,
            "bytes": path.stat().st_size,
            "integrity_checked": verify,
        }

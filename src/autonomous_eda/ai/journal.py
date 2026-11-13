"""Separate SQLite workflow journal with compare-and-swap checkpoints."""

import json
import os
import sqlite3
import time
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from uuid import uuid4

from ..knowledge.index import canonical, identity, transaction
from ..mcp.snapshot import ordinary

APPLICATION_ID = 0x41454441
JOURNAL_VERSION = 1


class State(StrEnum):
    CREATED = "CREATED"
    VALIDATING = "VALIDATING"
    COLLECTING_EVIDENCE = "COLLECTING_EVIDENCE"
    RETRIEVING_CONTEXT = "RETRIEVING_CONTEXT"
    CHECKING_SUFFICIENCY = "CHECKING_SUFFICIENCY"
    SELECTING_MODEL = "SELECTING_MODEL"
    GENERATING_EXPLANATION = "GENERATING_EXPLANATION"
    VALIDATING_OUTPUT = "VALIDATING_OUTPUT"
    COMPLETED = "COMPLETED"
    INSUFFICIENT_EVIDENCE = "INSUFFICIENT_EVIDENCE"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"


TERMINAL = {State.COMPLETED, State.INSUFFICIENT_EVIDENCE, State.FAILED, State.CANCELLED}
SCHEMA = (
    "CREATE TABLE workflows (id TEXT PRIMARY KEY, input_digest TEXT NOT NULL, input TEXT NOT NULL, "
    "state TEXT NOT NULL, deadline REAL NOT NULL, owner TEXT, lease_until REAL, "
    "data TEXT NOT NULL)",
    "CREATE TABLE transitions (id INTEGER PRIMARY KEY, workflow TEXT NOT NULL "
    "REFERENCES workflows, state TEXT NOT NULL, at REAL NOT NULL, detail TEXT NOT NULL)",
    "CREATE INDEX transitions_workflow ON transitions(workflow, id)",
)


@dataclass(frozen=True)
class Workflow:
    id: str
    input_digest: str
    inputs: dict[str, object]
    state: State
    deadline: float
    data: dict[str, object]


def record(row: sqlite3.Row) -> Workflow:
    return Workflow(
        row["id"],
        row["input_digest"],
        json.loads(row["input"]),
        State(row["state"]),
        row["deadline"],
        json.loads(row["data"]),
    )


class Journal:
    def __init__(self, path: Path) -> None:
        self.path = path.absolute()
        if (
            self.path.suffix.lower() != ".sqlite3"
            or self.path.name.lower() == "run.sqlite3"
            or self.path.anchor.startswith("\\\\")
        ):
            raise ValueError("Use a separate AI workflow .sqlite3 file")
        with self.connect(create=True) as db, transaction(db, write=True):
            app = db.execute("PRAGMA application_id").fetchone()[0]
            version = db.execute("PRAGMA user_version").fetchone()[0]
            if app == APPLICATION_ID and version == JOURNAL_VERSION:
                return
            if app or version or db.execute("SELECT 1 FROM sqlite_schema LIMIT 1").fetchone():
                raise ValueError("File is not a supported AI workflow database")
            for sql in SCHEMA:
                db.execute(sql)
            db.execute(f"PRAGMA application_id={APPLICATION_ID}")
            db.execute(f"PRAGMA user_version={JOURNAL_VERSION}")

    @contextmanager
    def connect(self, *, create: bool = False) -> Iterator[sqlite3.Connection]:
        for parent in reversed(self.path.parents):
            ordinary(parent, directory=True)
        if create:
            try:
                fd = os.open(self.path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
            except FileExistsError:
                pass
            else:
                os.close(fd)
        ordinary(self.path, directory=False)
        for suffix in ("-journal", "-wal", "-shm"):
            try:
                ordinary(self.path.with_name(self.path.name + suffix), directory=False)
            except FileNotFoundError:
                pass
        db = sqlite3.connect(self.path.as_uri() + "?mode=rw", uri=True)
        db.row_factory = sqlite3.Row
        try:
            db.execute("PRAGMA foreign_keys=ON")
            yield db
        finally:
            db.close()

    def create(self, inputs: dict[str, object], seconds: int, request_id: str | None) -> str:
        key = request_id or uuid4().hex
        digest = identity(inputs)
        with self.connect() as db, transaction(db, write=True):
            inserted = db.execute(
                "INSERT OR IGNORE INTO workflows VALUES (?,?,?,?,?,NULL,NULL,'{}')",
                (key, digest, canonical(inputs), State.CREATED, time.time() + seconds),
            )
            if inserted.rowcount:
                self.event(db, key, State.CREATED, "created")
            elif self.read(db, key).input_digest != digest:
                raise ValueError("request_id_conflict")
        return key

    @staticmethod
    def read(db: sqlite3.Connection, key: str) -> Workflow:
        row = db.execute("SELECT * FROM workflows WHERE id=?", (key,)).fetchone()
        if row is None:
            raise ValueError("workflow_not_found")
        return record(row)

    @staticmethod
    def event(db: sqlite3.Connection, key: str, state: State, detail: str) -> None:
        db.execute(
            "INSERT INTO transitions(workflow,state,at,detail) VALUES (?,?,?,?)",
            (key, state, time.time(), detail),
        )

    def claim(self, key: str, owner: str, duration: float) -> Workflow:
        with self.connect() as db, transaction(db, write=True):
            workflow = self.read(db, key)
            if workflow.state in TERMINAL:
                return workflow
            now = time.time()
            updated = db.execute(
                "UPDATE workflows SET owner=?, lease_until=? WHERE id=? "
                "AND (owner IS NULL OR owner=? OR lease_until<=?)",
                (owner, now + duration, key, owner, now),
            )
            if updated.rowcount != 1:
                raise ValueError("workflow_busy")
            return workflow

    def release(self, key: str, owner: str) -> None:
        with self.connect() as db, transaction(db, write=True):
            db.execute(
                "UPDATE workflows SET owner=NULL, lease_until=NULL WHERE id=? AND owner=?",
                (key, owner),
            )

    def update(
        self,
        workflow: Workflow,
        owner: str,
        state: State,
        detail: str,
        changes: dict[str, object] | None = None,
    ) -> None:
        with self.connect() as db, transaction(db, write=True):
            result = db.execute(
                "UPDATE workflows SET state=?,data=? WHERE id=? AND owner=? "
                "AND state=? AND lease_until>?",
                (
                    state,
                    canonical({**workflow.data, **(changes or {})}),
                    workflow.id,
                    owner,
                    workflow.state,
                    time.time(),
                ),
            )
            if result.rowcount != 1:
                raise ValueError("workflow_lease_lost")
            self.event(db, workflow.id, state, detail)

    def cancel(self, key: str) -> None:
        with self.connect() as db, transaction(db, write=True):
            workflow = self.read(db, key)
            if workflow.state not in TERMINAL:
                db.execute(
                    "UPDATE workflows SET state=?,owner=NULL,lease_until=NULL,data=? WHERE id=?",
                    (State.CANCELLED, canonical({**workflow.data, "reason": "cancelled"}), key),
                )
                self.event(db, key, State.CANCELLED, "cancelled")

    def inspect(self, key: str) -> tuple[Workflow, list[dict[str, object]]]:
        with self.connect() as db, transaction(db):
            workflow = self.read(db, key)
            history = [
                dict(row)
                for row in db.execute(
                    "SELECT id,state,at,detail FROM transitions WHERE workflow=? ORDER BY id",
                    (key,),
                )
            ]
            return workflow, history

    def status(self) -> dict[str, int]:
        with self.connect() as db:
            return dict(
                db.execute("SELECT state,count(*) FROM workflows GROUP BY state").fetchall()
            )

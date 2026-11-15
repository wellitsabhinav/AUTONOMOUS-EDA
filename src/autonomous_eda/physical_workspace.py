"""Read-only loopback server for the physical explorer and execution console."""

from __future__ import annotations

import hashlib
import json
import re
import shutil
import sqlite3
import subprocess
import time
from collections.abc import Iterable, Sequence
from contextlib import closing
from datetime import UTC, datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from importlib.resources import files
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlsplit

from .ai.journal import APPLICATION_ID, JOURNAL_VERSION
from .doctor import environment_secrets, redact_diagnostics
from .evidence import get_experiment
from .experiment import db_version
from .log_diagnostics import bounded_log, classify
from .manifest import sha256
from .stage_snapshots import local_input
from .verification.report import LATEST, report_run

ASSETS = {
    "/": ("workspace.html", "text/html"),
    "/workspace.js": ("workspace.js", "text/javascript"),
    "/workspace.css": ("workspace.css", "text/css"),
}
STAGE_ID = re.compile(r"[a-z][a-z0-9_-]{0,31}")


def database(path: Path) -> sqlite3.Connection:
    db = sqlite3.connect(path.resolve(strict=True).as_uri() + "?mode=ro", uri=True, timeout=1)
    db.row_factory = sqlite3.Row
    return db


def verify_asset(bundle: Path, record: dict[str, Any], kinds: Iterable[str]) -> bool:
    try:
        return all(
            sha256(local_input(bundle, record[key])) == record[key + "_sha256"] for key in kinds
        )
    except (ValueError, OSError):
        return False


class Workspace:
    def __init__(self, bundle: Path, repository: Path, run: Path | None, journal: Path | None):
        self.bundle = bundle.resolve(strict=True)
        self.repository = repository.resolve(strict=True)
        self.run = run.resolve(strict=True) if run else None
        self.journal = journal.resolve(strict=True) if journal else None
        self.manifest = json.loads((self.bundle / "snapshots.json").read_bytes())
        order = self.manifest["order"]
        stages = self.manifest["stages"]
        if self.manifest["schema_version"] != 1 or (
            not order
            or set(order) != set(stages)
            or len(order) != len(stages)
            or not all(STAGE_ID.fullmatch(stage) for stage in order)
        ):
            raise ValueError("Unsupported physical snapshot bundle")
        self.secrets = environment_secrets()

    def asset(self, stage: str, kind: str) -> bytes:
        if stage not in self.manifest["stages"] or kind not in {"png", "geometry"}:
            raise ValueError("Unknown stage asset")
        record = self.manifest["stages"][stage]
        if record["status"] != "rendered":
            raise ValueError("Stage evidence is unavailable")
        path = local_input(self.bundle, record[kind])
        with path.open("rb") as stream:
            data = stream.read(64 * 1024**2 + 1)
        if len(data) > 64 * 1024**2:
            raise ValueError("Stage asset exceeds the Studio's 64 MiB display limit")
        if hashlib.sha256(data).hexdigest() != record[kind + "_sha256"]:
            raise ValueError("Stage asset hash changed")
        return data

    def discovery(self) -> dict[str, Any]:
        events: list[dict[str, Any]] = []
        start = time.monotonic()
        try:
            git = subprocess.run(
                ["git", "-C", str(self.repository), "rev-parse", "--show-toplevel", "HEAD"],
                capture_output=True,
                text=True,
                timeout=3,
                check=False,
            )
            lines = git.stdout.splitlines()
        except (OSError, subprocess.TimeoutExpired):
            lines = []
        events.append(
            {
                "kind": "repository",
                "status": "complete" if len(lines) == 2 else "unavailable",
                "detail": lines if len(lines) == 2 else [str(self.repository)],
                "duration_ms": round((time.monotonic() - start) * 1000),
            }
        )
        start = time.monotonic()
        tools = {
            name: shutil.which(name)
            for name in ("docker", "yosys", "openroad", "sta", "klayout", "iverilog")
        }
        recorded = sorted(
            {
                stage["provenance"]["tool_version"]
                for stage in self.manifest["stages"].values()
                if stage["status"] == "rendered"
            }
        )
        events.append(
            {
                "kind": "toolchain",
                "status": "complete",
                "detail": {"host": tools, "renderers": recorded},
                "duration_ms": round((time.monotonic() - start) * 1000),
            }
        )
        start = time.monotonic()
        artifacts = []
        for stage in self.manifest["order"]:
            record = self.manifest["stages"][stage]
            status = record["status"]
            if status == "rendered":
                status = (
                    "verified"
                    if verify_asset(self.bundle, record, ("png", "geometry"))
                    else "missing or modified"
                )
            artifacts.append({"stage": stage, "status": status})
        events.append(
            {
                "kind": "artifacts",
                "status": "complete",
                "detail": artifacts,
                "duration_ms": round((time.monotonic() - start) * 1000),
            }
        )
        return {"events": events}

    def attempts(self) -> list[dict[str, Any]]:
        if self.run is None:
            return []
        with closing(database(local_input(self.run, "run.sqlite3"))) as db:
            if db_version(db) == 2:
                self.check_run(db.execute("SELECT run_id FROM run").fetchone()[0])
            rows = db.execute("SELECT * FROM attempts ORDER BY id DESC LIMIT 250").fetchall()
        result = []
        for row in reversed(rows):
            item = dict(row)
            fields = {
                key: item.get(key)
                for key in (
                    "id",
                    "stage",
                    "state",
                    "started_at",
                    "ended_at",
                    "runtime_seconds",
                    "exit_code",
                    "log",
                    "detail",
                )
            }
            command = item.get("command")
            fields["command"] = (
                redact_diagnostics(command, self.secrets)[:16384] if command else None
            )
            if fields["detail"]:
                fields["detail"] = redact_diagnostics(fields["detail"], self.secrets)
            fields["artifacts"] = json.loads(item["artifacts"] or "{}")
            result.append(fields)
        return result

    def agents(self) -> list[dict[str, Any]]:
        if self.journal is None:
            return []
        with closing(database(self.journal)) as db:
            if (
                db.execute("PRAGMA application_id").fetchone()[0] != APPLICATION_ID
                or db.execute("PRAGMA user_version").fetchone()[0] != JOURNAL_VERSION
            ):
                raise ValueError("Unsupported supervisor journal")
            rows = db.execute(
                "SELECT t.id, t.workflow, t.state, t.at, w.state AS current_state, "
                "(w.lease_until > ?) AS leased "
                "FROM transitions t JOIN workflows w ON w.id = t.workflow "
                "ORDER BY t.id DESC LIMIT 100",
                (time.time(),),
            ).fetchall()
        return [dict(row) for row in reversed(rows)]

    def attempt_log(self, attempt_id: int) -> dict[str, Any]:
        attempt = next((item for item in self.attempts() if item["id"] == attempt_id), None)
        if attempt is None or self.run is None:
            raise ValueError("Attempt is unavailable")
        name = attempt["log"]
        raw, truncated = bounded_log(local_input(self.run, name), 128 * 1024)
        return {
            "text": redact_diagnostics(raw.decode("utf-8", errors="replace"), self.secrets),
            "truncated": truncated,
            "diagnostics": classify(self.run, [(attempt["stage"], name)])["findings"][:50],
        }

    def status(self) -> dict[str, Any]:
        attempts = self.attempts()
        agents = self.agents()
        active_states = {
            "CREATED",
            "VALIDATING",
            "COLLECTING_EVIDENCE",
            "RETRIEVING_CONTEXT",
            "CHECKING_SUFFICIENCY",
            "SELECTING_MODEL",
            "GENERATING_EXPLANATION",
            "VALIDATING_OUTPUT",
        }
        live = any(item["current_state"] in active_states and item["leased"] for item in agents)
        running = any(item["state"] == "running" for item in attempts)
        return {
            "schema_version": 1,
            "events": [
                {
                    "kind": "execution",
                    "status": "running recorded" if running else "idle",
                    "detail": {
                        "source": "controller" if self.run else "no run selected",
                        "attempts": len(attempts),
                    },
                },
                {
                    "kind": "agent",
                    "status": "active" if live else "idle",
                    "detail": {
                        "source": "supervisor journal" if self.journal else "no journal selected",
                        "transitions": len(agents),
                    },
                },
            ],
            "attempts": attempts,
            "agents": agents,
            "timeline": self.timeline(attempts, agents),
        }

    def timeline(
        self, attempts: list[dict[str, Any]], agents: list[dict[str, Any]]
    ) -> list[dict[str, Any]]:
        run_id = self.manifest["run_id"]
        events = [
            {
                "event_id": f"controller:{run_id}:{item['id']}:{item['state']}",
                "timestamp": item["ended_at"] or item["started_at"],
                "run_id": run_id,
                "attempt_id": item["id"],
                "source": "controller",
                "category": "stage_attempt",
                "severity": "error" if item["state"] == "failed" else "info",
                "message": f"{item['stage']} {item['state']}",
                "evidence_references": list(item["artifacts"]),
                "stage": item["stage"],
            }
            for item in attempts
        ]
        events.extend(
            {
                "event_id": f"supervisor:{item['workflow']}:{item['id']}",
                "timestamp": datetime.fromtimestamp(item["at"], UTC).isoformat(),
                "run_id": None,
                "attempt_id": None,
                "source": "supervisor",
                "category": "workflow_transition",
                "severity": "info",
                "message": item["state"],
                "evidence_references": [],
                "stage": None,
            }
            for item in agents
        )
        return sorted(events, key=lambda event: event["timestamp"] or "", reverse=True)[:100]

    def verification(self) -> dict[str, Any] | None:
        if self.run is None:
            return None
        if not (self.run / LATEST).is_file():
            run = get_experiment(self.run)
            self.check_run(run["run_id"])
            return {
                "run_id": run["run_id"],
                "decision_sha256": None,
                "verdicts": {"full_acceptance": "NOT_CHECKED"},
                "note": "No physical profile has been evaluated for this run.",
                "gates": {
                    key: {
                        "status": gate["state"],
                        "diagnostic": "Controller evidence",
                        "evidence_references": gate["artifacts"],
                    }
                    for key, gate in run["verification_gates"].items()
                },
            }
        matrix = report_run(self.run, "physical")
        self.check_run(matrix["run_id"])
        return {
            "run_id": matrix["run_id"],
            "decision_sha256": matrix["decision_sha256"],
            "verdicts": matrix["verdicts"],
            "gates": {
                key: {
                    "status": gate["status"],
                    "diagnostic": redact_diagnostics(gate["diagnostic"], self.secrets),
                    "evidence_references": gate["evidence_references"],
                }
                for key, gate in matrix["gates"].items()
            },
        }

    def check_run(self, run_id: str) -> None:
        if run_id != self.manifest["run_id"]:
            raise ValueError("The selected verification run does not match the layout bundle")


def serve(
    bundle: Path, repository: Path, run: Path | None, journal: Path | None, port: int
) -> None:
    workspace = Workspace(bundle, repository, run, journal)

    class Handler(BaseHTTPRequestHandler):
        def send(self, status: int, mime: str, content: bytes) -> None:
            self.send_response(status)
            self.send_header("Content-Type", mime)
            self.send_header("Content-Length", str(len(content)))
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Cache-Control", "no-store")
            self.send_header(
                "Content-Security-Policy",
                "default-src 'self'; connect-src 'self' blob:; img-src 'self' blob:; "
                "frame-ancestors 'none'",
            )
            self.end_headers()
            self.wfile.write(content)

        def send_json(self, value: object, status: int = 200) -> None:
            self.send(status, "application/json", json.dumps(value, allow_nan=False).encode())

        def do_GET(self) -> None:
            if self.headers.get("Host") not in allowed_hosts:
                self.send_error(421)
                return
            try:
                self.dispatch()
            except (OSError, ValueError, sqlite3.Error, KeyError) as error:
                self.send_json({"error": redact_diagnostics(str(error), workspace.secrets)}, 400)

        def dispatch(self) -> None:
            url = urlsplit(self.path)
            if url.path in ASSETS:
                name, mime = ASSETS[url.path]
                source = files("autonomous_eda").joinpath("workspace", name)
                self.send(200, mime + "; charset=utf-8", source.read_bytes())
            elif url.path == "/api/bundle":
                self.send_json(workspace.manifest)
            elif url.path == "/api/discovery":
                self.send_json(workspace.discovery())
            elif url.path == "/api/status":
                self.send_json(workspace.status())
            elif url.path == "/api/verification":
                self.send_json(workspace.verification())
            elif url.path == "/api/log":
                self.send_json(workspace.attempt_log(int(parse_qs(url.query)["attempt"][0])))
            elif url.path.startswith("/api/stage/"):
                parts: Sequence[str] = url.path.split("/")
                if len(parts) != 5:
                    raise ValueError("Unknown stage asset")
                stage, kind = parts[3:]
                mime = "image/png" if kind == "png" else "application/json"
                self.send(200, mime, workspace.asset(stage, kind))
            else:
                self.send_error(404)

        def log_message(self, format: str, *args: Any) -> None:
            pass

    with ThreadingHTTPServer(("127.0.0.1", port), Handler) as server:
        actual_port = server.server_address[1]
        allowed_hosts = {f"127.0.0.1:{actual_port}", f"localhost:{actual_port}"}
        print(f"Physical workspace at http://127.0.0.1:{actual_port}", flush=True)
        server.serve_forever()

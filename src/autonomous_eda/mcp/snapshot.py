"""Copy authorized files before passing them to the path-based evidence API."""

import json
import os
import re
import sqlite3
import stat
import sys
from collections.abc import Iterator
from contextlib import ExitStack, closing, contextmanager
from dataclasses import dataclass
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import BinaryIO

from ..evidence import Experiment, acceptance, get_experiment
from ..manifest import identifier

MAX_FILES = 512
MAX_FILE_BYTES = 128 * 1024**2
MAX_SNAPSHOT_BYTES = 512 * 1024**2
MAX_TEXT_BYTES = 4 * 1024**2
MAX_DATABASE_BYTES = 16 * 1024**2
TEXT_SUFFIXES = {".json", ".log", ".toml", ".rpt", ".sdc", ".rb"}
DATABASE_SIDECARS = ("run.sqlite3-journal", "run.sqlite3-wal", "run.sqlite3-shm")


class UnsafeEvidence(ValueError):
    """The source is outside the accepted filesystem or snapshot policy."""


def relative_parts(value: str) -> tuple[str, ...]:
    parts = tuple(value.split("/"))
    if (
        len(value) > 384
        or len(parts) > 12
        or any(
            not re.fullmatch(r"[A-Za-z0-9_.-]{1,128}", part)
            or part in {".", ".."}
            or part.endswith(".")
            or PureWindowsPath(part).is_reserved()
            for part in parts
        )
    ):
        raise UnsafeEvidence("Invalid evidence path")
    return parts


def ordinary(path: Path, *, directory: bool) -> os.stat_result:
    info = path.lstat()
    reparse = getattr(info, "st_file_attributes", 0) & stat.FILE_ATTRIBUTE_REPARSE_POINT
    expected = stat.S_ISDIR if directory else stat.S_ISREG
    if reparse or not expected(info.st_mode) or (not directory and info.st_nlink != 1):
        raise UnsafeEvidence("Evidence must use ordinary, unlinked filesystem entries")
    return info


def stamp(info: os.stat_result) -> tuple[int, ...]:
    # On Windows path stat and fstat report different meanings for ctime.
    base = (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns)
    return base if os.name == "nt" else (*base, info.st_ctime_ns)


@contextmanager
def windows_file(path: Path, source: "Source") -> Iterator[BinaryIO]:
    # Hold each parent open without delete sharing, and inspect each entry without
    # following reparse points. Locking the parents prevents a rename or junction
    # substitution between the check and the final open.
    if sys.platform != "win32":
        raise OSError("Windows evidence reader is unavailable on this platform")

    import ctypes
    import msvcrt
    from ctypes import wintypes

    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    create = kernel.CreateFileW
    create.argtypes = (
        wintypes.LPCWSTR,
        wintypes.DWORD,
        wintypes.DWORD,
        wintypes.LPVOID,
        wintypes.DWORD,
        wintypes.DWORD,
        wintypes.HANDLE,
    )
    create.restype = wintypes.HANDLE
    close = kernel.CloseHandle
    close.argtypes = (wintypes.HANDLE,)
    close.restype = wintypes.BOOL
    invalid_handle = ctypes.c_void_p(-1).value
    open_existing = 3
    share_read_write = 3
    open_reparse_point = 0x00200000
    backup_semantics = 0x02000000
    generic_read = 0x80000000
    with ExitStack() as stack:
        # The drive root cannot be replaced by an ordinary directory writer.
        for parent in list(reversed(path.parents))[1:]:
            before = ordinary(parent, directory=True)
            handle = create(
                str(parent),
                0,
                share_read_write,
                None,
                open_existing,
                open_reparse_point | backup_semantics,
                None,
            )
            if handle == invalid_handle:
                raise ctypes.WinError(ctypes.get_last_error())
            stack.callback(close, handle)
            after = ordinary(parent, directory=True)
            if (before.st_dev, before.st_ino) != (after.st_dev, after.st_ino):
                raise UnsafeEvidence("Evidence directory changed during open")
            if parent == source.root and (after.st_dev, after.st_ino) != source.identity:
                raise UnsafeEvidence("Registered directory was replaced")
        before = ordinary(path, directory=False)
        handle = create(
            str(path),
            generic_read,
            1,  # Share reads only while capturing a file.
            None,
            open_existing,
            open_reparse_point,
            None,
        )
        if handle == invalid_handle:
            raise ctypes.WinError(ctypes.get_last_error())
        try:
            fd = msvcrt.open_osfhandle(handle, os.O_RDONLY | os.O_BINARY)
        except OSError:
            close(handle)
            raise
        with os.fdopen(fd, "rb") as stream:
            if stamp(os.fstat(stream.fileno())) != stamp(before):
                raise UnsafeEvidence("Evidence changed during open")
            yield stream


@contextmanager
def posix_file(path: Path, source: "Source") -> Iterator[BinaryIO]:
    with ExitStack() as stack:
        no_follow: int = getattr(os, "O_NOFOLLOW")
        directory_only: int = getattr(os, "O_DIRECTORY")
        nonblocking: int = getattr(os, "O_NONBLOCK")
        flags = os.O_RDONLY | directory_only | no_follow
        directory = os.open(path.anchor, flags)
        stack.callback(os.close, directory)
        for depth, part in enumerate(path.parts[1:-1], start=2):
            directory = os.open(part, flags, dir_fd=directory)
            stack.callback(os.close, directory)
            if depth == len(source.root.parts):
                info = os.fstat(directory)
                if (info.st_dev, info.st_ino) != source.identity:
                    raise UnsafeEvidence("Registered directory was replaced")
        fd = os.open(path.name, os.O_RDONLY | no_follow | nonblocking, dir_fd=directory)
        with os.fdopen(fd, "rb") as stream:
            yield stream


@dataclass(frozen=True)
class Source:
    root: Path
    identity: tuple[int, int]

    @classmethod
    def register(cls, path: Path) -> "Source":
        if path.anchor.startswith("\\\\"):
            raise UnsafeEvidence("Use a local drive for experiment roots")
        for directory in (*reversed(path.parents), path):
            ordinary(directory, directory=True)
        if path != path.resolve(strict=True):
            raise UnsafeEvidence("Experiment directory must be canonical")
        info = path.stat()
        return cls(path, (info.st_dev, info.st_ino))

    def check(self) -> None:
        for directory in (*reversed(self.root.parents), self.root):
            info = ordinary(directory, directory=True)
        if (info.st_dev, info.st_ino) != self.identity:
            raise UnsafeEvidence("Registered directory was replaced")


class Snapshot:
    def __init__(self, source: Source, target: Path) -> None:
        self.source = source
        self.target = target
        self.files: dict[Path, tuple[int, ...] | None] = {}
        self.bytes = 0

    def copy(self, relative: str) -> bool:
        parts = relative_parts(relative)
        path = self.source.root.joinpath(*parts)
        target = self.target.joinpath(*parts)
        if path in self.files:
            return self.files[path] is not None
        if len(self.files) >= MAX_FILES:
            raise UnsafeEvidence("Too many recorded artifacts")
        opener = windows_file if os.name == "nt" else posix_file
        try:
            # Check the lexical chain for reparse points, including dangling ones.
            before = self.source_stamp(path)
            with opener(path, self.source) as stream:
                info = os.fstat(stream.fileno())
                if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or before != stamp(info):
                    raise UnsafeEvidence("Evidence changed during open")
                limit = MAX_TEXT_BYTES if target.suffix.lower() in TEXT_SUFFIXES else MAX_FILE_BYTES
                if relative == "run.sqlite3":
                    limit = MAX_DATABASE_BYTES
                if info.st_size > limit or self.bytes + info.st_size > MAX_SNAPSHOT_BYTES:
                    raise UnsafeEvidence("Evidence exceeds snapshot byte limits")
                target.parent.mkdir(parents=True, exist_ok=True)
                remaining = info.st_size
                with target.open("xb") as output:
                    while remaining:
                        chunk = stream.read(min(remaining, 1024**2))
                        if not chunk:
                            raise UnsafeEvidence("Evidence shrank during capture")
                        output.write(chunk)
                        remaining -= len(chunk)
                if stamp(os.fstat(stream.fileno())) != before:
                    raise UnsafeEvidence("Evidence changed during capture")
                os.utime(target, ns=(info.st_atime_ns, info.st_mtime_ns))
                self.bytes += info.st_size
        except FileNotFoundError:
            if target.exists():
                raise UnsafeEvidence("Evidence disappeared during capture") from None
            self.files[path] = None
            return False
        self.files[path] = before
        return True

    def source_stamp(self, path: Path) -> tuple[int, ...]:
        for parent in reversed(path.relative_to(self.source.root).parents):
            ordinary(self.source.root / parent, directory=True)
        return stamp(ordinary(path, directory=False))

    def verify_source(self) -> None:
        self.source.check()
        for path, expected in self.files.items():
            try:
                actual = self.source_stamp(path)
            except FileNotFoundError:
                actual = None
            if actual != expected:
                raise UnsafeEvidence("Evidence changed while the request was running")

    def reject_active_database(self) -> None:
        for name in DATABASE_SIDECARS:
            try:
                (self.source.root / name).lstat()
            except FileNotFoundError:
                continue
            raise UnsafeEvidence("Use a completed SQLite run without journal sidecars")

    def collect(self) -> None:
        self.source.check()
        records: list[object] = []
        if self.copy("run.sqlite3"):
            self.reject_active_database()
            # Read only the evidence inventory here. The controller interprets
            # the run and measurements from the staged SQLite file below.
            uri = (self.target / "run.sqlite3").as_uri() + "?mode=ro&immutable=1"
            with closing(sqlite3.connect(uri, uri=True)) as db:
                db.execute("PRAGMA trusted_schema=OFF")
                tables = {row[1]: row[2] for row in db.execute("PRAGMA table_list")}
                if any(tables.get(name) != "table" for name in ("run", "attempts")):
                    raise UnsafeEvidence("Expected ordinary controller tables")
                manifests = db.execute("SELECT manifest FROM run LIMIT 2").fetchall()
                if len(manifests) != 1:
                    raise UnsafeEvidence("Expected one run record")
                manifest = json.loads(manifests[0][0])
                for key in ("platform", "design"):
                    identifier(manifest[key], key)
                for (raw,) in db.execute(
                    "SELECT artifacts FROM attempts LIMIT ?", (MAX_FILES + 1,)
                ):
                    if len(records) >= MAX_FILES:
                        raise UnsafeEvidence("Too many recorded attempts")
                    records.append(json.loads(raw) if raw is not None else {})
            for name in ("manifest.toml", "validate_gds.rb"):
                self.copy(name)
            for name in ("verification-drc.json", "verification-lvs.json"):
                if self.copy(name):
                    records.append(
                        json.loads((self.target / name).read_bytes()).get("artifacts", {})
                    )
        elif self.copy("reference.json"):
            records.append(json.loads((self.target / "reference.json").read_bytes())["sha256"])
        else:
            raise FileNotFoundError("No recorded experiment")
        for record in records:
            if not isinstance(record, dict):
                raise UnsafeEvidence("Invalid evidence inventory")
            for relative in record:
                parts = relative_parts(relative)
                if parts[0] not in {"attempts", "logs", "reports", "results"} and relative != (
                    "gds-validation.log"
                ):
                    raise UnsafeEvidence("Unrecognized artifact location")
                if PurePosixPath(relative).suffix.lower() in {".sqlite3", ".dll", ".exe"}:
                    raise UnsafeEvidence("Unrecognized artifact type")
                self.copy(relative)


def load_evidence(source: Source, target: Path) -> Experiment:
    return collect_evidence(Snapshot(source, target))


def collect_evidence(snapshot: Snapshot) -> Experiment:
    snapshot.collect()
    evidence = get_experiment(snapshot.target, legacy_root=snapshot.source.root)
    # Historical controller reports without artifact-bound deck verification
    # cannot establish an MCP pass or failure for those gates.
    if evidence["origin"] != "external":
        gates = evidence["verification_gates"]
        verification = evidence["provenance"]["verification"]
        gds_refs = gates["gds_generation"]["artifacts"]
        gds = evidence["artifacts"].get(gds_refs[0]) if gds_refs else None
        for name, target_name in (("platform_drc", "drc"), ("lvs", "lvs")):
            record = verification.get(target_name, {})
            if gates[name]["state"] in {"PASSED", "FAILED"} and (
                gds is None
                or record.get("gds_sha256") != gds["sha256"]
                or not set(gates[name]["artifacts"][1:]).issubset(record.get("artifacts", {}))
            ):
                gates[name]["state"] = "BLOCKED"
                if name == "platform_drc":
                    count = evidence["metrics"]["klayout_drc_report_value_count"]
                    count["value"] = None
                    count["status"] = "UNKNOWN"
        evidence["acceptance"] = acceptance(gates, evidence["required_gates"])
    if (snapshot.target / "run.sqlite3").is_file():
        snapshot.reject_active_database()
    snapshot.verify_source()
    return evidence

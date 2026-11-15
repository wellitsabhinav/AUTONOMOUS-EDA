"""Cover checkpoint isolation, provenance, and failure triage."""

import json
import shutil
import struct
import subprocess
import sys
import zlib
from pathlib import Path
from typing import Any

import pytest

from autonomous_eda import stage_snapshots as snapshots
from autonomous_eda.ai.gateway import Request, Response, Route
from autonomous_eda.diagnostic_review import add_proposals, approve, prioritize
from autonomous_eda.log_diagnostics import classify
from autonomous_eda.manifest import sha256
from autonomous_eda.physical_workspace import Workspace


def chunk(kind: bytes, payload: bytes) -> bytes:
    return (
        struct.pack(">I", len(payload))
        + kind
        + payload
        + struct.pack(">I", zlib.crc32(kind + payload))
    )


def png() -> bytes:
    return snapshots.PNG_SIGNATURE + b"".join(
        (
            chunk(b"IHDR", struct.pack(">IIBBBBB", 1, 1, 8, 2, 0, 0, 0)),
            chunk(b"IDAT", zlib.compress(b"\0\0\0\0")),
            chunk(b"IEND", b""),
        )
    )


def embedded(path: Path) -> Any:
    data = path.read_bytes()
    size = struct.unpack(">I", data[33:37])[0]
    assert data[37:41] == b"tEXt"
    assert struct.unpack(">I", data[41 + size : 45 + size])[0] == zlib.crc32(data[37 : 41 + size])
    key, value = data[41 : 41 + size].split(b"\0", 1)
    assert key == snapshots.PNG_KEY
    return json.loads(value)


def row(*fields: str) -> str:
    return "\t".join(field.encode().hex() for field in fields)


@pytest.mark.skipif(sys.platform != "linux", reason="requires Linux render launcher")
def test_stage_process_crash_does_not_publish_partial_png_or_poison_next_stage(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "inputs"
    root.mkdir()
    for name in ("failed.odb", "next.def", "end.gds", "tech.lef", "cells.lef"):
        (root / name).write_text(name, encoding="utf-8")
    runs = []

    def invoke(command: list[str], **kwargs: Any) -> subprocess.CompletedProcess[bytes]:
        work = Path(
            next(item for item in command if item.endswith(",target=/work"))
            .removeprefix("type=bind,source=")
            .removesuffix(",target=/work")
        )
        runs.append((command[command.index("--name") + 1], work))
        assert ",target=/inputs,readonly" in " ".join(command)
        (work / "layout.png").write_bytes(png())
        if len(runs) == 1:
            kwargs["stdout"].write(b"Signal 11 received\n")
            return subprocess.CompletedProcess(command, 139)
        if len(runs) == 2:
            assert (work / "inputs/0000.lef").read_text() == "tech.lef"
            assert (work / "inputs/0001.lef").read_text() == "cells.lef"
            kwargs["stdout"].write(b"OpenROAD test-version\nAEDA_STAGE_OK\n")
        else:
            kwargs["stdout"].write(b"AEDA_STAGE_OK KLayout test-version\n")
        (work / "geometry.tsv").write_text(
            row("design", "test", "1000", "0", "0", "100", "100")
            + "\n"
            + row("congestion", "met1", "1", "2", "0", "0", "100", "100")
            + "\n",
            encoding="utf-8",
        )
        return subprocess.CompletedProcess(command, 0)

    monkeypatch.setattr(snapshots, "preflight", lambda: "docker")
    monkeypatch.setattr(subprocess, "run", invoke)
    out = tmp_path / "bundle"
    value = snapshots.export(
        root,
        out,
        selections=[
            ("place", "failed.odb"),
            ("route", "next.def"),
            ("gds", "end.gds"),
            ("cts", "missing.odb"),
        ],
        run_id="test",
        lefs=["tech.lef", "cells.lef"],
        width=1000,
    )
    assert len({name for name, _ in runs}) == len({path for _, path in runs}) == 3
    assert not (out / "place.png").exists()
    assert "Signal 11" in (out / "place.log").read_text()
    assert value["stages"]["place"]["status"] == "failed"
    assert value["stages"]["cts"]["status"] == "missing"
    for stage in ("route", "gds"):
        provenance = embedded(out / f"{stage}.png")
        assert provenance == value["stages"][stage]["provenance"]
        assert provenance["run_id"] == "test"
        assert provenance["source"]["stage"] == stage
        assert provenance["source"]["sha256"] == sha256(root / provenance["source"]["path"])
    workspace = Workspace(out, root, None, None)
    assert workspace.status()["events"][0]["status"] == "idle"
    assert workspace.status()["schema_version"] == 1
    assert workspace.status()["timeline"] == []
    assert workspace.verification() is None
    assert workspace.asset("route", "png") == (out / "route.png").read_bytes()
    (out / "route.png").write_bytes(b"changed")
    with pytest.raises(ValueError, match="hash changed"):
        workspace.asset("route", "png")


def test_studio_event_stream_preserves_controller_identity(tmp_path: Path) -> None:
    (tmp_path / "snapshots.json").write_text(
        '{"schema_version":1,"run_id":"bound","order":["place"],"stages":{"place":{}}}',
        encoding="utf-8",
    )
    workspace = Workspace(tmp_path, tmp_path, None, None)
    events = workspace.timeline(
        [
            {
                "id": 8,
                "stage": "place",
                "state": "failed",
                "started_at": "2026-11-15T04:00:00+00:00",
                "ended_at": "2026-11-15T04:01:00+00:00",
                "artifacts": {"attempts/placement.log": "a" * 64},
            }
        ],
        [],
    )
    assert events[0]["event_id"] == "controller:bound:8:failed"
    assert events[0]["source"] == "controller"
    assert events[0]["severity"] == "error"
    assert events[0]["evidence_references"] == ["attempts/placement.log"]
    with pytest.raises(ValueError, match="does not match"):
        workspace.check_run("unrelated")


def test_recorded_crash_evidence_and_log_filter(tmp_path: Path) -> None:
    evidence = Path(__file__).parents[1] / "docs/evidence/physical-snapshots"
    shutil.copyfile(evidence / "openroad-teardown.log", tmp_path / "probe.log")
    (tmp_path / "yosys.log").write_text(
        "Yosys\nERROR: Module missing is not part of the design. counter.v:17\n",
        encoding="utf-8",
    )
    inputs = [("floorplan", "probe.log"), ("synth", "yosys.log"), ("route", "probe.log")]
    report = classify(tmp_path, inputs)
    found = {item["code"]: item for item in report["findings"]}
    assert found["OPENROAD_PROCESS_SIGNAL"]["message"] == "Signal 11 received"
    assert found["YOSYS_UNRESOLVED_MODULE"]["source_references"] == [
        {"path": "counter.v", "line": 17}
    ]
    assert report["correlations"][0]["stages"] == ["floorplan", "route"]
    assert all(
        item["tool"] == "Yosys" for item in classify(tmp_path, inputs, tool="Yosys")["findings"]
    )

    add_proposals(report)
    snapshots.write_json(tmp_path / "diagnostics.json", report)
    proposal = report["proposals"][0]
    with pytest.raises(ValueError, match="does not match"):
        approve(tmp_path, "wrong-digest")
    approved = approve(tmp_path, proposal["approval_digest"])
    assert approved["status"] == "approved_for_manual_execution"
    assert approved["proposal"] == proposal
    with pytest.raises(FileExistsError):
        approve(tmp_path, proposal["approval_digest"])

    class Model:
        def complete(self, request: Request, timeout: float) -> Response:
            assert b"OPENROAD_PROCESS_SIGNAL" in request.body
            return Response('{"finding_ids":["invented-id"]}')

    route = Route("local", "fixture", "http://127.0.0.1:8080/v1/chat/completions")
    assert prioritize(report, route, Model())["status"] == "abstained"


def test_published_stage_images_bind_the_retained_run() -> None:
    docs = Path(__file__).parents[1] / "docs"
    root = docs / "images/gcd-stage-snapshots"
    bundle = json.loads((root / "provenance.json").read_bytes())
    receipt = json.loads((docs / "evidence/gcd-walkthrough/receipt.json").read_bytes())
    hashes = {
        key: value for item in receipt["attempts"] for key, value in item["artifacts"].items()
    }
    assert bundle["order"] == [name for name, _ in snapshots.CHECKPOINTS]
    for stage, record in bundle["stages"].items():
        image = root / record["png"]
        assert sha256(image) == record["png_sha256"]
        metadata = embedded(image)
        assert metadata == record["provenance"]
        assert metadata["run_id"] == receipt["run_id"]
        assert metadata["source"]["stage"] == stage
        assert hashes[metadata["source"]["path"]] == metadata["source"]["sha256"]


def test_studio_publication_preserves_source_and_screenshot_hashes() -> None:
    docs = Path(__file__).parents[1] / "docs"
    folder = docs / "images/studio"
    published = json.loads((folder / "provenance.json").read_bytes())
    physical = json.loads((docs / "evidence/opentitan-aes/physical.json").read_bytes())
    stage_hashes = {
        key: value
        for attempt in json.loads((docs / "evidence/gcd-walkthrough/receipt.json").read_bytes())[
            "attempts"
        ]
        for key, value in attempt["artifacts"].items()
    }
    for record in published["images"].values():
        assert sha256(folder / record["filename"]) == record["image_sha256"]
        for stage in record["stages"].values():
            expected = physical["artifact_sha256"] if record["design"] == "aes" else stage_hashes
            assert stage["source"]["sha256"] == expected[stage["source"]["path"]]

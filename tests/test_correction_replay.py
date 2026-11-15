import json
import shutil
from pathlib import Path

import pytest

from autonomous_eda import correction_replay
from autonomous_eda.diagnostic_review import add_proposals
from autonomous_eda.log_diagnostics import classify, export_bundle
from autonomous_eda.manifest import sha256


@pytest.fixture
def correction(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[Path, Path, Path, str]:
    inputs = tmp_path / "logs"
    inputs.mkdir()
    log = inputs / "renderer.log"
    original = Path("docs/evidence/physical-snapshots/openroad-teardown.log")
    shutil.copyfile(original, log)
    diagnosis = classify(inputs, [("floorplan", log.name)], tool="OpenROAD")
    add_proposals(diagnosis)
    bundle = tmp_path / "diagnosis"
    export_bundle(bundle, diagnosis)
    run = tmp_path / "run"
    run.mkdir()
    monkeypatch.setattr(
        correction_replay,
        "checkpoint",
        lambda *_: {
            "run_id": "gcd",
            "top": "gcd",
            "attempt": "2",
            "path": "results/2_floorplan.odb",
            "sha256": "a" * 64,
        },
    )
    monkeypatch.setattr(
        correction_replay, "export", lambda *_args, **_kwargs: pytest.fail("dispatched renderer")
    )
    review, output = tmp_path / "review", tmp_path / "output"
    result = correction_replay.plan(bundle, inputs, run, review, output, "floorplan", 750)
    checksum: str = result["plan_sha256"]
    return review, output, log, checksum


def test_rejected_correction_never_dispatches(correction: tuple[Path, Path, Path, str]) -> None:
    review, output, _, checksum = correction
    decision = correction_replay.review_plan(review, checksum, decision="REJECTED")
    assert [event["state"] for event in decision["events"]] == [
        "OBSERVED",
        "DIAGNOSED",
        "PROPOSED",
        "REVIEWED",
        "REJECTED",
    ]
    with pytest.raises(ValueError, match="exact approved plan"):
        correction_replay.execute_plan(review, checksum)
    assert not output.exists()
    assert not (review / "execution.claim").exists()


def test_approval_is_invalid_after_evidence_changes(
    correction: tuple[Path, Path, Path, str],
) -> None:
    review, output, log, checksum = correction
    correction_replay.review_plan(review, checksum, decision="APPROVED")
    log.write_bytes(b"changed evidence")
    with pytest.raises(ValueError, match="inputs changed"):
        correction_replay.execute_plan(review, checksum)
    assert not output.exists()
    assert not (review / "execution.claim").exists()


def test_published_replay_keeps_the_fault_and_review_provenance() -> None:
    docs = Path("docs")
    receipt = json.loads((docs / "evidence/integration/verified-correction.json").read_bytes())
    screenshot = receipt["browser_review"]
    assert sha256(docs / "images/studio" / screenshot["file"]) == screenshot["image_sha256"]
    fault = docs / "evidence/physical-snapshots/openroad-teardown.log"
    assert sha256(fault) == receipt["retained_fault_log_sha256"]
    assert receipt["approved"]["events"][-1]["state"] == "VERIFIED"
    assert receipt["rejected"]["events"][-1]["state"] == "REJECTED"
    assert receipt["rejected"]["tool_dispatched"] is False
    assert receipt["process_and_retry"]["native_attempt_count"] == 1

from pathlib import Path

import pytest

from autonomous_eda.evidence import get_experiment
from autonomous_eda.experiment import initialize
from autonomous_eda.manifest import ORFSManifest, load_manifest, sha256
from autonomous_eda.mcp.snapshot import Source, load_evidence
from autonomous_eda.opentitan_physical import RTL_REVISION
from autonomous_eda.orfs import DispatchStopped, make_command
from autonomous_eda.resource_budget import run_guard


def test_aes_stages_only_the_bound_frontend_output(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    manifest_path = Path("configs/opentitan-aes-sky130hd.toml")
    manifest = load_manifest(manifest_path)
    assert isinstance(manifest, ORFSManifest)
    assert make_command(manifest, "synth")[1] == "DESIGN_CONFIG=/work/aes-input/config.mk"
    root = tmp_path / "run"
    with pytest.raises(ValueError, match="requires --translated-rtl"):
        initialize(root, manifest_path, manifest, 3000)
    translated = tmp_path / "aes.v"
    translated.write_bytes(b"module aes; endmodule")
    with pytest.raises(ValueError, match="fails the 10A receipt"):
        initialize(root, manifest_path, manifest, 3000, translated_rtl=translated)
    assert not root.exists()

    digest = sha256(translated)
    monkeypatch.setattr("autonomous_eda.opentitan_physical.RTL_DIGEST", digest)
    monkeypatch.setattr("autonomous_eda.evidence.AES_RTL_DIGEST", digest)
    initialize(root, manifest_path, manifest, 3000, translated_rtl=translated)
    report = get_experiment(root)
    assert report["source_revision"] == RTL_REVISION
    assert report["artifacts"]["aes-input/aes.v"]["availability"] == "VERIFIED"
    assert "aes-input/block.sdc" in report["constraints"]
    snapshot = tmp_path / "snapshot"
    snapshot.mkdir()
    captured = load_evidence(Source.register(root), snapshot)
    assert captured["rtl_identity"] == report["rtl_identity"]
    assert captured["provenance"]["input_error"] is None
    (root / "aes-input/block.sdc").write_bytes(b"bad")
    stale = get_experiment(root)
    assert stale["rtl_identity"] is None
    assert "AES SDC differs" in stale["provenance"]["input_error"]


def test_time_budget_counts_from_invocation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    clock = iter((5.0, 6.0, 8.0))
    monkeypatch.setattr("autonomous_eda.resource_budget.time.monotonic", lambda: next(clock))
    guard = run_guard(tmp_path, wall_seconds=3, artifact_mib=None)
    assert guard is not None
    assert guard() == 2
    with pytest.raises(DispatchStopped, match="wall-clock budget exceeded"):
        guard()


def test_disk_budget_preserves_retained_files(tmp_path: Path) -> None:
    output = tmp_path / "tool.log"
    output.write_bytes(b" " * (2**20 + 1))
    guard = run_guard(tmp_path, wall_seconds=None, artifact_mib=1)
    assert guard is not None
    with pytest.raises(DispatchStopped, match="artifact budget exceeded"):
        guard()
    assert output.stat().st_size == 2**20 + 1

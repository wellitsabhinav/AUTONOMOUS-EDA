"""Optional tests that actually dispatch Yosys and Icarus."""

import shutil
from pathlib import Path

import pytest

from autonomous_eda.verification.report import report_run, verify_run

EXAMPLE = Path(__file__).resolve().parents[1] / "examples" / "verification"


@pytest.mark.skipif(shutil.which("yosys") is None, reason="native Yosys is unavailable")
def test_real_yosys_proof_mismatch_and_stale_receipts(tmp_path: Path) -> None:
    root = shutil.copytree(EXAMPLE, tmp_path / "rtl")
    passing = verify_run(root, "rtl-formal")
    assert passing["gates"]["elaboration"]["status"] == "PASSED"
    assert passing["gates"]["equivalence"]["status"] == "PASSED"
    gate = passing["gates"]["equivalence"]
    log = next(ref for ref in gate["evidence_references"] if ref.endswith("/prove.stdout.log"))
    (root / log).write_text("changed")
    assert report_run(root)["gates"]["equivalence"]["status"] == "BLOCKED"
    (root / "logic.sv").write_text((EXAMPLE / "logic.sv").read_text().replace("a ^ b", "a | b"))
    failing = verify_run(root, "rtl-formal")
    assert failing["gates"]["equivalence"]["status"] == "FAILED"
    assert "counterexample" in failing["gates"]["equivalence"]["diagnostic"]
    shutil.copyfile(EXAMPLE / "logic.sv", root / "logic.sv")
    assert report_run(root)["gates"]["equivalence"]["status"] == "BLOCKED"
    assert verify_run(root, "rtl-formal")["gates"]["equivalence"]["status"] == "PASSED"


@pytest.mark.skipif(
    not all(shutil.which(tool) for tool in ("iverilog", "vvp")),
    reason="native Icarus compiler and runtime are unavailable",
)
def test_real_icarus_negative_oracle_and_correction(tmp_path: Path) -> None:
    root = shutil.copytree(EXAMPLE, tmp_path / "rtl")
    (root / "logic.sv").write_text((EXAMPLE / "logic.sv").read_text().replace("a ^ b", "a | b"))
    result = verify_run(root, "rtl")
    gate = result["gates"]["functional_regression"]
    if gate["status"] == "BLOCKED" and "could not compile" in gate["diagnostic"]:
        stderr = next(
            ref for ref in gate["evidence_references"] if ref.endswith("compile.stderr.log")
        )
        if "not recognized as an internal or external command" in (root / stderr).read_text():
            pytest.skip("installed Windows Icarus cannot resolve its compiler path")
    assert gate["status"] == "FAILED"
    log = next(ref for ref in gate["evidence_references"] if ref.endswith("simulate.stdout.log"))
    assert "a=1 b=1 expected=0 observed=1" in (root / log).read_text()
    shutil.copyfile(EXAMPLE / "logic.sv", root / "logic.sv")
    assert verify_run(root, "rtl")["gates"]["functional_regression"]["status"] == "PASSED"

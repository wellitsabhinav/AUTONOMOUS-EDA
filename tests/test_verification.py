import json
import shutil
import sys
import time
from pathlib import Path
from typing import Any

import pytest

from autonomous_eda.evidence import decode_evidence, encode_evidence, import_reference
from autonomous_eda.manifest import fingerprint, sha256
from autonomous_eda.verification.policy import load_policy, verdict
from autonomous_eda.verification.process import execute
from autonomous_eda.verification.report import evaluate, markdown, report_run, verify_run
from autonomous_eda.verification.timing import CHECKS, parse_coverage, sdc_clocks

EXAMPLE = Path(__file__).resolve().parents[1] / "examples" / "verification"
INDEX = Path(__file__).resolve().parents[1] / "docs" / "evidence" / "gcd-sky130hd-index.json"


def test_policy_and_precedence(tmp_path: Path) -> None:
    config = tmp_path / "verification.toml"
    config.write_text('schema_version=1\nrequire=["formal_properties"]\n')
    assert "formal_properties" in load_policy("rtl", config).required
    assert verdict(["BLOCKED", "FAILED"]) == "FAILED"
    assert verdict(["PASSED", "UNKNOWN"]) == "BLOCKED"
    assert verdict(["PASSED", "NOT_APPLICABLE"]) == "PASSED"
    assert verdict([]) == "NOT_APPLICABLE"
    with pytest.raises(ValueError, match="unknown verification profile"):
        load_policy("legacy", config)


@pytest.mark.parametrize(
    "setting",
    [
        'optional=["setup"]',
        'applicability={setup="never"}',
        'require=["setup","setup"]',
        'require=["unlisted"]',
        "timeout_seconds=0",
        "schema_version=2",
        "[objectives]\nestimated_power_w=nan",
        '[equivalence]\nreference="../other.toml"\ncorrespondence="exact_ports"',
    ],
)
def test_policy_rejects_invalid_or_weakening_options(tmp_path: Path, setting: str) -> None:
    config = tmp_path / "verification.toml"
    config.write_text(f"schema_version=1\n{setting}\n")
    with pytest.raises(ValueError):
        load_policy("full", config)


def test_coverage_requires_complete_unambiguous_report() -> None:
    rows = "\n".join(f"AEDA_CHECK {name} 1" for name in CHECKS)
    assert set(parse_coverage(rows)[0].values()) == {"UNKNOWN"}
    report = f"AEDA_CLOCK core_clock\n{rows}\nAEDA_COVERAGE_COMPLETE\n"
    states, clocks = parse_coverage(report)
    assert set(states.values()) == {"PASSED"} and clocks == ["core_clock"]
    bad = report.replace("unconstrained_endpoints 1", "unconstrained_endpoints 0")
    assert parse_coverage(bad)[0]["unconstrained_endpoints"] == "FAILED"
    assert parse_coverage(report + "AEDA_CHECK loops 1\n")[0]["loops"] == "UNKNOWN"
    assert sdc_clocks("create_clock -name core_clock -period 1.1 [get_ports clk]") == (
        {"core_clock": 1.1},
        [],
    )
    assert sdc_clocks("set_false_path -to [get_ports reset]")[1] == ["set_false_path"]


def physical_fixture(root: Path, overrides: dict[str, Any]) -> Path:
    record = json.loads(INDEX.read_bytes())
    result = "results/sky130hd/gcd/base/"
    logs = "logs/sky130hd/gcd/base/"
    reports = "reports/sky130hd/gcd/base/"
    data: dict[str, str] = {
        result + "6_final.gds": "fixture only",
        result + "1_synth.sdc": "create_clock -name core_clock -period 1.1 [get_ports clk]\n",
        result + "6_final.sdc": "create_clock -name core_clock -period 1.1 [get_ports clk]\n",
        logs + "6_report.json": json.dumps(
            {
                "finish__timing__setup__ws": 0.1,
                "finish__timing__hold__ws": 0.2,
                "finish__timing__drv__setup_violation_count": 0,
                "finish__timing__drv__hold_violation_count": 0,
                "finish__design__instance__area__stdcell": 12,
                **overrides,
            }
        ),
        logs + "5_2_route.json": '{"detailedroute__route__drc_errors":0}',
        reports + "6_drc_count.rpt": "0",
        reports + "6_drc.lyrdb": "fixture only",
        logs + "6_lvs.log": "INFO : Congratulations! Netlists match.",
        result + "6_lvs.lvsdb": "fixture only",
        "gds-validation.log": "Validated GDS top gcd, 1 cells",
    }
    record["sha256"] = {}
    for rel, text in data.items():
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)
        record["sha256"][rel] = sha256(path)
    index = root / "reference.json"
    index.write_text(json.dumps(record))
    return index


@pytest.mark.parametrize("check", ["setup", "hold"])
def test_physical_failures_take_precedence(tmp_path: Path, check: str) -> None:
    index = physical_fixture(tmp_path, {f"finish__timing__{check}__ws": -0.2})
    evidence = import_reference(tmp_path, index)
    assert decode_evidence(encode_evidence(evidence)) == evidence
    matrix = report_run(tmp_path, "physical")
    assert matrix["gates"][check]["status"] == "FAILED"
    assert matrix["gates"]["timing_coverage"]["status"] == "BLOCKED"
    assert matrix["verdicts"]["full_acceptance"] == "FAILED"
    assert f"| {check} | required | FAILED |" in markdown(matrix)
    digest = matrix.pop("decision_sha256")
    assert fingerprint(matrix) == digest


@pytest.mark.parametrize("change", ["modified", "deleted", "corner", "sdc"])
def test_physical_hash_and_context_mismatch(tmp_path: Path, change: str) -> None:
    physical_fixture(tmp_path, {})
    final = tmp_path / "results/sky130hd/gcd/base/6_final.sdc"
    if change == "modified":
        final.write_text("create_clock -name core_clock -period 2 [get_ports clk]\n")
    elif change == "deleted":
        final.unlink()
    else:
        key, value = ("corner", "other") if change == "corner" else ("sdc_sha256", "0" * 64)
        (tmp_path / "verification.toml").write_text(
            f'schema_version=1\n[timing]\n{key}="{value}"\n'
        )
    matrix = report_run(tmp_path, "physical")
    assert matrix["gates"]["hold"]["status"] == "BLOCKED"
    assert matrix["gates"]["timing_coverage"]["status"] == "BLOCKED"
    assert matrix["verdicts"]["full_acceptance"] == "BLOCKED"


@pytest.mark.parametrize("gate", ["platform_drc", "lvs"])
def test_independent_deck_failure(tmp_path: Path, gate: str) -> None:
    index = physical_fixture(tmp_path, {})
    relative = (
        "reports/sky130hd/gcd/base/6_drc_count.rpt"
        if gate == "platform_drc"
        else "logs/sky130hd/gcd/base/6_lvs.log"
    )
    path = tmp_path / relative
    path.write_text("4" if gate == "platform_drc" else "ERROR : Netlists don't match")
    data = json.loads(index.read_bytes())
    data["sha256"][relative] = sha256(path)
    index.write_text(json.dumps(data))
    evidence = import_reference(tmp_path, index)
    matrix = evaluate(tmp_path, evidence, tmp_path, load_policy("physical", None), None)
    assert matrix["gates"][gate]["status"] == "FAILED"
    assert matrix["verdicts"]["physical_feasibility"] == "FAILED"


def test_hard_objective_failure(tmp_path: Path) -> None:
    physical_fixture(tmp_path, {})
    (tmp_path / "verification.toml").write_text(
        "schema_version=1\n[objectives]\nstandard_cell_area_um2=10\n"
    )
    matrix = report_run(tmp_path, "physical")
    assert matrix["verdicts"]["optimization_objectives"] == "FAILED"
    assert matrix["verdicts"]["full_acceptance"] == "FAILED"


def test_missing_reference_testbench_and_tools(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = shutil.copytree(EXAMPLE, tmp_path / "rtl")
    (root / "logic_tb.sv").unlink()
    (root / "reference.toml").unlink()
    monkeypatch.setattr(shutil, "which", lambda _name: None)
    result = verify_run(root, "rtl-formal")
    assert result["verdicts"]["full_acceptance"] == "BLOCKED"
    assert "logic_tb.sv" in result["gates"]["functional_regression"]["diagnostic"]
    assert "reference.toml" in result["gates"]["equivalence"]["diagnostic"]
    assert "executable unavailable" in result["gates"]["elaboration"]["diagnostic"]
    shutil.copyfile(EXAMPLE / "reference.toml", root / "reference.toml")
    missing = verify_run(root, "rtl-formal")
    assert "executable unavailable: yosys" in missing["gates"]["equivalence"]["diagnostic"]
    assert report_run(root, "physical")["gates"]["setup"]["status"] == "BLOCKED"


@pytest.mark.parametrize("stop", ["cancelled", "timeout"])
def test_bounded_native_dispatch(tmp_path: Path, stop: str) -> None:
    start = time.monotonic()
    result = execute(
        [sys.executable, "-c", "import time; time.sleep(10)"],
        tmp_path,
        "sleep",
        start + 0.4,
        lambda: stop == "cancelled" and time.monotonic() - start > 0.15,
    )
    assert result.stop == stop
    assert result.runtime_seconds < 8
    assert (tmp_path / result.stdout).is_file()
    assert (tmp_path / result.stderr).is_file()


def test_interruption_clears_selected_attempt(tmp_path: Path) -> None:
    root = shutil.copytree(EXAMPLE, tmp_path / "rtl")
    first = verify_run(root, "rtl-formal")
    with pytest.raises(KeyboardInterrupt):
        verify_run(root, "rtl-formal", cancelled=lambda: True)
    stopped = report_run(root)
    assert first["attempt"] != stopped["attempt"]
    assert stopped["gates"]["elaboration"]["status"] == "BLOCKED"
    assert (root / "verification/attempts" / first["attempt"]).is_dir()


def test_acceptance_png_provenance(tmp_path: Path) -> None:
    pytest.importorskip("matplotlib")
    pytest.importorskip("PIL")
    from autonomous_eda.verification.figures import render
    from autonomous_eda.visual_artifacts import gallery

    physical_fixture(tmp_path, {"finish__timing__setup__ws": -0.2})
    matrix = report_run(tmp_path, "full")
    exported = render(tmp_path, matrix)
    assert len(exported["images"]) == 5
    for item in gallery(tmp_path)["images"].values():
        assert item["evidence_status"] == "VERIFIED"
        assert item["source_sha256"] == [sha256(tmp_path / "acceptance.json")]
    (tmp_path / "logs/sky130hd/gcd/base/6_report.json").unlink()
    assert all(item["evidence_status"] == "STALE" for item in gallery(tmp_path)["images"].values())

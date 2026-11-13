import json
from pathlib import Path

import pytest

from autonomous_eda.evidence import (
    Experiment,
    compare_experiments,
    decode_evidence,
    encode_evidence,
    import_reference,
)
from autonomous_eda.manifest import sha256


@pytest.mark.parametrize(
    ("design", "top", "slack", "period"),
    [
        ("gcd", "gcd", -1.20488, 1.1),
        ("ibex", "ibex_core", -0.377013, 8.0),
    ],
)
def test_reference_round_trip_and_artifact_invalidation(
    tmp_path: Path,
    design: str,
    top: str,
    slack: float,
    period: float,
) -> None:
    index = json.loads(Path(f"docs/evidence/{design}-sky130hd-index.json").read_text())
    variant = f"sky130hd/{design}/base"
    final = f"logs/{variant}/6_report.json"
    files = {
        final: json.dumps(
            {
                "finish__timing__setup__ws": slack,
                "finish__timing__hold__ws": 0.4,
                "finish__timing__drv__hold_violation_count": 0,
            }
        ),
        f"results/{variant}/6_final.gds": "fixture GDS",
        f"results/{variant}/1_synth.sdc": f"create_clock -name core -period {period} clk",
        f"results/{variant}/6_lvs.lvsdb": "fixture LVS",
        f"reports/{variant}/6_drc.lyrdb": "fixture DRC",
        f"reports/{variant}/6_drc_count.rpt": "0",
        f"logs/{variant}/6_lvs.log": "INFO : Congratulations! Netlists match.",
        "gds-validation.log": f"Validated GDS top {top}, 5 cells",
    }
    index["sha256"] = {}
    for relative, content in files.items():
        path = tmp_path / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
        index["sha256"][relative] = sha256(path)
    index_file = tmp_path / "reference.json"
    index_file.write_text(json.dumps(index), encoding="utf-8")
    observation = import_reference(tmp_path, index_file)
    assert observation["origin"] == "external"
    assert observation["attempts"] == []
    assert observation["acceptance"] == "FAILED"
    assert observation["metrics"]["setup_wns_ns"]["value"] == slack
    assert observation["metrics"]["clock_period_ns"]["value"] == period
    assert observation["metrics"]["estimated_power_w"]["status"] == "UNKNOWN"
    gates = observation["verification_gates"]
    assert gates["setup"]["state"] == "FAILED"
    assert all(
        gates[name]["state"] == "PASSED" for name in ("hold", "platform_drc", "lvs", "gds_reopen")
    )
    assert gates["equivalence"]["state"] == "NOT_CHECKED"
    text = encode_evidence(observation)
    assert encode_evidence(decode_evidence(text)) == text
    invalid = json.loads(text)
    invalid["verification_gates"]["setup"]["artifacts"] = []
    with pytest.raises(ValueError, match="unverified gate"):
        decode_evidence(json.dumps(invalid))
    invalid["schema_version"] = 2
    with pytest.raises(ValueError, match="unsupported evidence schema"):
        decode_evidence(json.dumps(invalid))
    incompatible = compare_experiments(observation, observation)
    assert not incompatible["compatible"] and "rtl_identity" in incompatible["differences"]
    assert incompatible["deltas"] == {}
    controlled: Experiment = {
        **observation,
        "rtl_identity": "fixture-rtl",
        "configuration_sha256": "fixture-config",
    }
    assert compare_experiments(controlled, controlled)["deltas"]["setup_wns_ns"] == 0
    assert "estimated_power_w" not in compare_experiments(controlled, controlled)["deltas"]
    assert compare_experiments(observation, observation, descriptive=True)["records"][0]["metrics"]
    (tmp_path / final).write_text("{}", encoding="utf-8")
    (tmp_path / f"results/{variant}/6_lvs.lvsdb").unlink()
    changed = import_reference(tmp_path, index_file)
    assert changed["metrics"]["setup_wns_ns"]["value"] is None
    assert changed["artifacts"][final]["availability"] == "MODIFIED"
    assert changed["verification_gates"]["lvs"]["state"] == "BLOCKED"
    assert changed["acceptance"] == "BLOCKED"

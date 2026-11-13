import json
from pathlib import Path

from autonomous_eda.metrics import summarize


def test_physical_gates_require_independent_evidence(tmp_path: Path) -> None:
    (tmp_path / "6_final.gds").touch()
    final = tmp_path / "6_report.json"
    final.write_text(
        json.dumps(
            {
                "finish__timing__setup__ws": -0.2,
                "finish__timing__setup__tns": -3.0,
                "finish__timing__drv__setup_violation_count": 7,
                "finish__timing__hold__ws": 0.1,
                "finish__timing__drv__hold_violation_count": 0,
            }
        ),
        encoding="utf-8",
    )
    (tmp_path / "6_lvs.log").write_text("ERROR : Netlists don't match\n", encoding="utf-8")
    metrics, gates = summarize(tmp_path, tmp_path, tmp_path, final_valid=True)
    assert gates == {"setup": "failed", "hold": "passed", "drc": "not checked", "lvs": "failed"}
    assert metrics["setup_wns_ns"] == -0.2
    assert metrics["instance_count"] is None
    _, gates = summarize(tmp_path, tmp_path, tmp_path, final_valid=False)
    assert set(gates.values()) == {"not checked"}

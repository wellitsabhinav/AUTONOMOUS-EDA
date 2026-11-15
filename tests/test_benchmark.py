import json
from pathlib import Path
from typing import Any

import pytest

from autonomous_eda.benchmark import rebuild, write_report
from autonomous_eda.manifest import fingerprint, sha256


def test_benchmark_exports_rebuild_with_bound_figures(tmp_path: Path) -> None:
    pytest.importorskip("matplotlib")
    pytest.importorskip("PIL")
    case: dict[str, Any] = {
        "label": "GCD fixture",
        "run_id": "a" * 32,
        "design": "gcd",
        "duration_seconds": {"value": 5.0, "source": "test fixture"},
        "storage_bytes": None,
        "allocation": None,
        "latest": {
            "route": {"duration_seconds": 3.0, "state": "failed"},
            "synth": {"duration_seconds": 2.0, "state": "passed"},
        },
        "attempts": [],
        "assessment": {
            "implementation_status": "BLOCKED",
            "physical_acceptance_status": "BLOCKED",
            "design_qualification_status": "BLOCKED",
            "readiness_level": 2,
        },
    }
    content = {"schema_version": 1, "cases": [case], "notes": []}
    report = {**content, "report_sha256": fingerprint(content)}
    root = tmp_path / "benchmark"
    write_report(root, report, ["autonomous-eda", "benchmark", "build"])
    manifest_path = root / "visualizations/visualization_manifest.json"
    first = json.loads(manifest_path.read_bytes())
    second = rebuild(root, ["autonomous-eda", "benchmark", "figures"])
    assert first["images"] and set(first["images"]) == set(second["images"])
    for identifier, record in first["images"].items():
        assert record["image_sha256"] == second["images"][identifier]["image_sha256"]
        assert record["source_sha256"] == [sha256(root / "report.json")]
    assert "routing_memory" in second["unavailable"]
    modified = {**report, "cases": []}
    (root / "report.json").write_text(json.dumps(modified))
    with pytest.raises(ValueError, match="digest"):
        rebuild(root, ["autonomous-eda", "benchmark", "figures"])

"""Acceptance figures using the existing verified PNG export contract."""

from functools import partial
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any

from ..manifest import sha256
from ..visual_artifacts import Export, atomic_json
from ..visual_charts import bars, table
from .report import markdown, report_run


def render(root: Path, matrix: dict[str, Any]) -> dict[str, Any]:
    root = root.resolve()
    profile = matrix["profile"]
    if report_run(root, profile)["decision_sha256"] != matrix["decision_sha256"]:
        raise ValueError("Acceptance evidence changed before rendering")
    source = root / "acceptance.json"
    atomic_json(source, matrix)
    (root / "acceptance.md").write_text(markdown(matrix), encoding="utf-8")
    refs = {source.name: sha256(source)}
    run = matrix["run_id"]
    detail = f"{matrix['design'] or 'Unresolved design'}  |  {profile}  |  Run {run[:12]}"
    gates = matrix["gates"]
    with TemporaryDirectory(prefix="aeda-acceptance-figures-") as directory:
        output = Export(root, Path(directory), run)
        figures = {
            "acceptance_gate_matrix": (
                "Verification gate matrix",
                ["Gate", "Requirement", "Outcome"],
                [
                    [key.replace("_", " "), row["requirement"], row["status"]]
                    for key, row in gates.items()
                ],
            ),
            "acceptance_verdicts": (
                "Acceptance verdicts",
                ["Decision", "Outcome"],
                [[key.replace("_", " "), value] for key, value in matrix["verdicts"].items()],
            ),
            "acceptance_integrity": (
                "Evidence integrity",
                ["Measure", "Value"],
                [
                    ["Inventory status", gates["artifact_integrity"]["status"]],
                    ["Recorded input hashes", str(len(gates["artifact_integrity"]["inputs"]))],
                    [
                        "Required unresolved gates",
                        str(
                            sum(
                                row["requirement"] == "required"
                                and row["status"] in {"BLOCKED", "UNKNOWN"}
                                for row in gates.values()
                            )
                        ),
                    ],
                ],
            ),
        }
        for name, (title, columns, rows) in figures.items():
            widths = [0.50, 0.19, 0.31] if len(columns) == 3 else None
            output.plot(
                name, title, detail, refs, partial(table, columns=columns, rows=rows, widths=widths)
            )
        values = [(gates[name].get("measurement") or {}).get("value") for name in ("setup", "hold")]
        timing_detail = f"{detail}  |  Coverage {gates['timing_coverage']['status']}"
        output.plot(
            "acceptance_timing",
            "Final setup and hold",
            timing_detail,
            refs,
            lambda ax: bars(
                ax,
                [f"{key.title()} · {gates[key]['status']}" for key in ("setup", "hold")],
                values,
                "Worst slack (ns)",
            ),
        )
        regression = gates["functional_regression"]
        output.plot(
            "acceptance_regression",
            "Functional regression",
            detail,
            refs,
            lambda ax: table(
                ax,
                ["Measure", "Value"],
                [
                    ["Oracle outcome", regression["status"]],
                    ["Diagnostic", regression["diagnostic"]],
                    ["Attempt", (regression["attempt"] or "Unavailable")[:32]],
                ],
            ),
        )
        if report_run(root, profile)["decision_sha256"] != matrix["decision_sha256"]:
            raise ValueError("Acceptance evidence changed during rendering")
        return output.publish(replace_types=set(output.images) | set(output.unavailable))

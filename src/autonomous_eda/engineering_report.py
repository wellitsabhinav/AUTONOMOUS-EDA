"""Render an engineering summary from recorded controller evidence."""

from .evidence import Experiment


def engineering_markdown(run: Experiment) -> str:
    lines = [
        f"# {run['design']} implementation",
        "",
        f"Run **{run['run_id']}** on **{run['platform']}**.",
        f"RTL revision `{run['source_revision']}`.",
        f"ORFS revision `{run['toolchain']['orfs_revision']}`.",
        "",
        "## Attempts",
        "",
        "| ID | Stage | State | Wall time (s) | Peak observed process RSS (KiB) | Log |",
        "| --- | --- | --- | ---: | ---: | --- |",
    ]
    for attempt in run["attempts"]:
        number = attempt["id"]
        stage, state, log = attempt["stage"], attempt["state"], attempt["log"]
        time = run["metrics"].get(f"stage_runtime.{number}")
        peak = run["metrics"].get(f"stage_peak_process_rss.{number}")
        elapsed = f"{time['value']:.3f}" if time and time["value"] is not None else "Missing"
        rss = str(peak["value"]) if peak else "Missing"
        lines.append(f"| {number} | {stage} | {state} | {elapsed} | {rss} | `{log}` |")
    lines.extend(
        [
            "",
            "## Timing and implementation measurements",
            "",
            "| Measurement | Value | Source |",
            "| --- | --- | --- |",
        ]
    )
    for name, metric in run["metrics"].items():
        if "." in name and not name.startswith("stage."):
            continue
        value = metric["value"]
        observed = f"{value:g} {metric['unit']}" if value is not None else "Missing"
        source = f"`{metric['artifact']}`" if metric["artifact"] else "Unavailable"
        lines.append(f"| {name} | {observed} | {source} |")
    lines.extend(
        ["", "## Recorded gates", "", "| Gate | State | Evidence |", "| --- | --- | --- |"]
    )
    for gate, evidence in run["verification_gates"].items():
        paths = ", ".join(f"`{path}`" for path in evidence["artifacts"]) or "Unavailable"
        lines.append(f"| {gate} | {evidence['state']} | {paths} |")
    lines.extend(["", f"Recorded acceptance **{run['acceptance']}**.", ""])
    return "\n".join(lines)

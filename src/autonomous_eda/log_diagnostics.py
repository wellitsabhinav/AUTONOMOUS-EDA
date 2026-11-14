"""Classify observed EDA failures and export reviewable diagnostic evidence."""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .doctor import environment_secrets, redact_diagnostics
from .stage_snapshots import local_input, write_json

MAX_BYTES = 4 * 1024**2
MAX_LOGS = 24
MAX_FINDINGS = 500
LINE_REFERENCE = re.compile(r"([\w./\\-]+\.(?:sv|v|tcl|sdc|lef|def)):(\d+)", re.I)
SEVERITY = re.compile(r"\b(error|fatal|warning|failed|failure|blocked)\b|signal 11", re.I)


@dataclass(frozen=True)
class Rule:
    code: str
    tool: str
    pattern: str
    hypothesis: str
    diagnostic: tuple[str, ...]


RULES = (
    Rule(
        "OPENROAD_PROCESS_SIGNAL",
        "OpenROAD",
        r"\bSignal 11 received\b|segmentation fault",
        "OpenROAD terminated on a memory access signal. The preceding operation and stack "
        "frames can narrow down the cause.",
        ("openroad", "-version"),
    ),
    Rule(
        "YOSYS_EQUIV_FAILED",
        "Yosys",
        r"proof did fail|model found: FAIL",
        "The proof backend reported a counterexample or a failed proof obligation.",
        ("yosys", "-V"),
    ),
    Rule(
        "YOSYS_UNRESOLVED_MODULE",
        "Yosys",
        r"ERROR: Module .+ (?:is not part of the design|not found|is not found)",
        "A module may be missing from the ordered RTL list or excluded by its defines.",
        ("autonomous-eda", "inspect", "manifest.toml"),
    ),
    Rule(
        "ICEWALL_RING_ASSEMBLY",
        "ICeWall",
        r"(?:\[ERROR PAD-\d+\]|(?:error|fatal).*\b(?:icewall|pad ring)\b)",
        "The pad assembly command reported an error. Check the named site, master or placement.",
        ("autonomous-eda", "io", "--help"),
    ),
    Rule(
        "OPENSTA_TIMING_INPUT",
        "OpenSTA",
        r"(?:error|warning).*(?:liberty|clock|sdc|no paths|unconstrained)",
        "The timing message identifies a constraint, library or coverage issue to inspect.",
        ("autonomous-eda", "diagnose", ".", "--json"),
    ),
    Rule(
        "OPENROAD_LOG_ERROR",
        "OpenROAD",
        r"\[ERROR [A-Z]+-\d+\]",
        "An OpenROAD subsystem reported a failure. The tool message identifies the subsystem.",
        ("openroad", "-version"),
    ),
    Rule(
        "KLAYOUT_LAYOUT_INPUT",
        "KLayout",
        r"(?:error|fatal|exception|missing|unexpected).*(?:gds|layout|cell)",
        "KLayout may be unable to load the stream or locate its expected top cell.",
        ("klayout", "-v"),
    ),
    Rule(
        "IVERILOG_COMPILE_FAILED",
        "Icarus",
        r"syntax error|unable to bind|unknown module type",
        "Icarus reported a source or elaboration error. Inspect the cited source and manifest.",
        ("iverilog", "-V"),
    ),
    Rule(
        "VERIFICATION_CHECK_REPORTED",
        "Verification",
        r"(?:drc|lvs|coverage|functional regression|equivalence|setup|hold|connectivity)"
        r".{0,100}\b(?:failed|blocked|violated|mismatch)\b",
        "The line reports a verification outcome. Read the acceptance matrix for its scope.",
        ("autonomous-eda", "report", ".", "--profile", "full", "--markdown"),
    ),
)
SIGNATURES = {
    "Yosys": r"\byosys\b",
    "OpenROAD": r"\bopenroad\b",
    "OpenSTA": r"\bopensta\b",
    "ICeWall": r"\bicewall\b",
    "KLayout": r"\bklayout\b",
    "Icarus": r"\b(?:icarus|iverilog|vvp)\b",
    "Verification": r"\b(?:verification|acceptance)\b",
}
TOOLS = tuple(SIGNATURES)


def bounded_log(path: Path, limit: int = MAX_BYTES) -> tuple[bytes, bool]:
    with path.open("rb") as stream:
        raw = stream.read(limit + 1)
    return raw[:limit], len(raw) > limit


def classify(
    root: Path, inputs: list[tuple[str, str]], *, tool: str | None = None
) -> dict[str, Any]:
    if not 1 <= len(inputs) <= MAX_LOGS:
        raise ValueError(f"Select between 1 and {MAX_LOGS} logs")
    secrets = environment_secrets()
    logs = []
    findings: list[dict[str, Any]] = []
    capped = False
    for stage, name in inputs:
        raw, truncated = bounded_log(local_input(root, name))
        lines = raw.decode("utf-8", errors="replace").splitlines()
        context = "\n".join([name, *lines[:20]])
        detected = {
            label for label, pattern in SIGNATURES.items() if re.search(pattern, context, re.I)
        }
        logs.append(
            {
                "stage": stage,
                "path": name,
                "prefix_sha256": hashlib.sha256(raw).hexdigest(),
                "bytes": len(raw),
                "truncated": truncated,
                "detected_tools": sorted(detected),
            }
        )
        for number, line in enumerate(lines, 1):
            line = line[:4096]
            match = SEVERITY.search(line)
            rule = next(
                (
                    item
                    for item in RULES
                    if (tool is None or item.tool == tool)
                    and (
                        item.tool in detected
                        or item.tool == tool
                        or item.code
                        in {
                            "OPENROAD_LOG_ERROR",
                            "ICEWALL_RING_ASSEMBLY",
                            "YOSYS_EQUIV_FAILED",
                            "YOSYS_UNRESOLVED_MODULE",
                            "VERIFICATION_CHECK_REPORTED",
                        }
                    )
                    and re.search(item.pattern, line, re.I)
                ),
                None,
            )
            if not (rule or match and (tool is None or tool in detected)):
                continue
            if len(findings) == MAX_FINDINGS:
                capped = True
                break
            references = [
                {"path": redact_diagnostics(source, secrets), "line": int(number)}
                for source, number in LINE_REFERENCE.findall(line)
            ]
            findings.append(
                {
                    "id": f"finding-{len(findings) + 1}",
                    "code": rule.code if rule else "UNCLASSIFIED_LOG_MESSAGE",
                    "tool": rule.tool if rule else tool or "unknown",
                    "severity": "warning" if match and match[0].lower() == "warning" else "error",
                    "stage": stage,
                    "log": name,
                    "line": number,
                    "message": redact_diagnostics(line, secrets),
                    "source_references": references,
                    "hypothesis": rule.hypothesis if rule else None,
                    "diagnostic_argv": list(rule.diagnostic) if rule else [],
                    "action_status": "review_required" if rule else None,
                }
            )
    correlations = []
    for code in sorted({item["code"] for item in findings} - {"UNCLASSIFIED_LOG_MESSAGE"}):
        matches = [item for item in findings if item["code"] == code]
        stages = list(dict.fromkeys(item["stage"] for item in matches))
        if len(stages) > 1:
            correlations.append(
                {
                    "code": code,
                    "stages": stages,
                    "finding_ids": [item["id"] for item in matches],
                    "basis": "shared diagnostic signature",
                }
            )
    return {
        "schema_version": 1,
        "logs": logs,
        "findings": findings,
        "correlations": correlations,
        "findings_capped": capped,
    }


def export_bundle(output: Path, result: dict[str, Any]) -> None:
    output.mkdir(parents=True, exist_ok=False)
    write_json(output / "diagnostics.json", result)

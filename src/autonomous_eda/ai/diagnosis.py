"""Present deterministic diagnosis facts and bounded options to the existing supervisor."""

import sqlite3
from dataclasses import asdict

from ..diagnosis import Check, diagnose_timing
from ..knowledge.evaluation import citation_valid
from ..knowledge.index import identity
from ..knowledge.retrieval import search
from ..mcp.registry import Registry
from ..reference import REVISION
from .context import Knowledge
from .contracts import Choice, Context, Fact


def build_diagnosis(
    registry: Registry, experiment_id: str, question: str, check: Check, knowledge: Knowledge | None
) -> Context:
    report = diagnose_timing(registry, experiment_id, check)
    final = report.stage_trends[-1]
    findings = [
        item
        for item in report.observed_findings
        if item.id.endswith("_worst") or item.id.startswith(("clock_", "path_"))
    ]
    facts: list[Fact] = [
        {"id": f.id, "classification": "MEASURED", "text": f.text, "evidence": list(f.evidence)}
        for f in findings[:20]
    ]
    sources: dict[str, object] = {
        key: asdict(value) for key, value in report.evidence_references.items()
    }
    events = []
    if knowledge:
        try:
            hits = search(
                knowledge.path,
                knowledge.registry,
                "report_checks timing delay fanout",
                top_k=4,
                filters={"trust": "trusted_documentation"},
            )
            for hit in hits:
                if "report_checks" not in hit.excerpt or not citation_valid(
                    hit, knowledge.registry[hit.citation.source_id]
                ):
                    continue
                if len(hit.excerpt) > knowledge.context_chars:
                    continue
                if hit.metadata.get("tool_revision") != REVISION:
                    continue
                ref = hit.citation.chunk_id
                sources[ref] = {"citation": asdict(hit.citation), "trust": hit.trust}
                facts.append(
                    {
                        "id": "report_checks_documentation",
                        "classification": "DOCUMENTED",
                        "text": "The approved local documentation references report_checks.",
                        "evidence": [ref],
                    }
                )
                break
        except (OSError, ValueError, sqlite3.Error):
            events.append("knowledge_unavailable")

    interpretations: list[Choice] = [
        {"id": f.id, "text": f.text, "evidence": list(f.evidence)}
        for f in report.derived_findings
        if f.id.startswith(("data_", "match_")) or f.id == "finish_aggregate_delta"
    ]
    interpretations = interpretations[:10]
    if final.worst_slack is not None and final.worst_path_ids:
        worst = final.worst_slack
        interpretations.insert(
            0,
            {
                "id": "final_margin",
                "text": f"The recorded final {check} worst slack is {worst.value:g} ns. "
                "Use the cited timing path and rounded increments to locate further measurements.",
                "evidence": [
                    worst.evidence,
                    *next(
                        path.evidence
                        for path in report.critical_paths
                        if path.id == final.worst_path_ids[0]
                    ),
                ],
            },
        )
    hypotheses: list[Choice] = [
        {"id": item.id, "text": item.text, "evidence": list(item.evidence)}
        for item in report.hypotheses
    ]
    next_measurements: list[Choice] = [
        {
            "id": item.id,
            "text": f"{item.measurement} {item.why} {item.question} "
            f"Requires {item.tool_capability}.",
            "evidence": list(item.evidence),
        }
        for item in report.suggested_investigations
    ]
    missing = [
        gap.description
        for gap in report.missing_measurements
        if gap.code
        in {
            "CAUSE_UNRESOLVED",
            "WORST_PATH_UNRESOLVED",
            "WORST_PATH_AMBIGUOUS",
            "MATCHED_PATH_UNAVAILABLE",
            "CONGESTION_UNAVAILABLE",
            "COVERAGE_UNVERIFIED",
        }
    ]
    sufficient = final.worst_slack is not None and len(final.worst_path_ids) == 1
    used = {ref for item in facts for ref in item["evidence"]} | {
        ref
        for option in (*interpretations, *hypotheses, *next_measurements)
        for ref in option["evidence"]
    }
    # Keep the entire structured report in the journal. Prompts only carry
    # bounded facts and options, so untrusted report rows never enter inference.
    return Context(
        "diagnose",
        question,
        experiment_id,
        {
            "design": report.design,
            "platform": report.platform,
            "acceptance": report.acceptance,
            "diagnosis": asdict(report),
        },
        facts,
        {ref: sources[ref] for ref in sorted(used)},
        interpretations,
        hypotheses,
        next_measurements,
        missing,
        events,
        sufficient,
        identity(report.evidence_identity, [f["evidence"] for f in facts]),
    )

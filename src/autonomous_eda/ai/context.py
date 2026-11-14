"""Select authorized facts and retrieval excerpts for one engineering question."""

import hashlib
import re
import sqlite3
from dataclasses import asdict, dataclass
from pathlib import Path
from tempfile import TemporaryDirectory

from ..evidence import Experiment
from ..knowledge.evaluation import citation_valid
from ..knowledge.index import identity
from ..knowledge.parsers import timing_fields
from ..knowledge.retrieval import Hit, search
from ..knowledge.sources import MAX_TEXT_BYTES, Registration, check_sensitive, decoded, read_file
from ..mcp.registry import Registry
from ..mcp.snapshot import Source, load_evidence, relative_parts
from .contracts import MAX_CHOICE_TEXT, ArtifactRef, Choice, Context, Fact, MetricValue, Task

METRICS = (
    "clock_period_ns",
    "setup_worst_slack_ns",
    "hold_worst_slack_ns",
    "klayout_drc_report_value_count",
)
GATES = ("setup", "hold", "platform_drc", "lvs", "gds_generation", "gds_reopen", "equivalence")
OPTION = re.compile(r"\b[A-Z][A-Z0-9]+(?:_[A-Z0-9]+)+\b")


def classify(question: str) -> Task:
    query = question.casefold()
    if re.search(r"\bequivalence\b|\bequivalent\b", query):
        return "equivalence"
    if re.search(r"\b(?:critical|timing|setup|hold) path\b|\bstartpoint\b|\bendpoint\b", query):
        return "path"
    if re.search(r"\b(?:verification|signoff|lvs|drc|hold)\b", query):
        return "verification"
    if re.search(r"\b(?:setup|slack|wns|timing|corner)\b|implementation fail", query):
        return "setup"
    if OPTION.search(question):
        return "documentation"
    return "unrelated"


def source_refs(experiment: Experiment, paths: list[str]) -> list[ArtifactRef]:
    refs: list[ArtifactRef] = []
    for path in paths:
        record = experiment["artifacts"].get(path)
        if not record or record["availability"] != "VERIFIED" or not record["sha256"]:
            return []
        relative_parts(path)
        refs.append(
            {
                "id": identity(experiment["run_id"], path, record["sha256"]),
                "path": path,
                "sha256": record["sha256"],
            }
        )
    return refs


def observation(experiment: Experiment) -> dict[str, object]:
    from ..verification.policy import verdict

    metrics: dict[str, MetricValue] = {}
    for key in METRICS:
        metric = experiment["metrics"][key]
        value, path = metric["value"], metric["artifact"]
        if (
            metric["status"] == "MEASURED"
            and value is not None
            and path
            and source_refs(experiment, [path])
        ):
            metrics[key] = {
                "value": value,
                "unit": metric["unit"],
                "stage": metric["stage"],
                "corner": metric["corner"],
            }
    return {
        "design": experiment["design"],
        "platform": experiment["platform"],
        "run_id": experiment["run_id"],
        "origin": experiment["origin"],
        "acceptance": experiment["acceptance"],
        "recorded_verification": verdict(
            [
                "UNKNOWN" if row["state"] == "NOT_CHECKED" else row["state"]
                for row in experiment["verification_gates"].values()
            ]
        ),
        "corner": experiment["corner"],
        "metrics": metrics,
        "gates": {key: gate["state"] for key, gate in experiment["verification_gates"].items()},
        "required_gates": experiment["required_gates"],
        "artifacts": {
            state: sum(a["availability"] == state for a in experiment["artifacts"].values())
            for state in ("VERIFIED", "MODIFIED", "MISSING", "UNKNOWN")
        },
    }


def recorded_facts(experiment: Experiment, task: Task) -> tuple[list[Fact], dict[str, object]]:
    facts: list[Fact] = []
    sources: dict[str, object] = {}
    metrics = {
        "observe": METRICS,
        "setup": METRICS[:2],
        "path": METRICS[:2],
        "verification": METRICS,
    }.get(task, ())
    gates = {
        "observe": GATES,
        "setup": ("setup",),
        "path": ("setup",),
        "verification": GATES,
        "equivalence": ("equivalence",),
    }.get(task, ())
    for key in metrics:
        metric = experiment["metrics"][key]
        if metric["status"] != "MEASURED" or metric["value"] is None or not metric["artifact"]:
            continue
        refs = source_refs(experiment, [metric["artifact"]])
        if refs:
            ref = refs[0]
            cite = identity(ref["id"], key, metric["reference"])
            sources[cite] = {
                "artifact": ref,
                "locator": metric["reference"],
                "stage": metric["stage"],
                "corner": metric["corner"],
            }
            text = f"{key.replace('_', ' ')} = {metric['value']} {metric['unit']}".rstrip()
            facts.append(
                {"id": key, "classification": "MEASURED", "text": text, "evidence": [cite]}
            )
    for key in gates:
        gate = experiment["verification_gates"][key]
        if gate["state"] not in {"PASSED", "FAILED"}:
            continue
        refs = source_refs(experiment, gate["artifacts"])
        if not refs:
            continue
        cite = identity(key, refs)
        sources[cite] = {"artifacts": refs, "locator": f"schema1/verification_gates/{key}"}
        facts.append(
            {
                "id": key,
                "classification": "MEASURED",
                "text": f"{key.replace('_', ' ')} gate {gate['state']}",
                "evidence": [cite],
            }
        )
    return facts, sources


@dataclass(frozen=True)
class Knowledge:
    path: Path
    registry: dict[str, Registration]
    context_chars: int = 1800

    def __post_init__(self) -> None:
        if type(self.context_chars) is not int or not 256 <= self.context_chars <= MAX_CHOICE_TEXT:
            raise ValueError("Knowledge context limit must be between 256 and 6000 characters")


def relevant_path(hit: Hit, experiment: Experiment, source: Source) -> bool:
    cite = hit.citation
    # The file must be the same report recorded by this run. Design labels
    # alone can match unrelated runs at a different configuration or corner.
    entry_path = cite.reference.split("/", 1)[-1]
    relative = next(
        (
            path
            for path, a in experiment["artifacts"].items()
            if a["availability"] == "VERIFIED"
            and a["sha256"] == cite.source_sha256
            and (entry_path.endswith("/" + path) or entry_path == path)
        ),
        None,
    )
    if relative is None or not cite.line_start or not cite.line_end:
        return False
    if not {"startpoint", "endpoint", "slack", "check"} <= hit.fields.keys():
        return False
    raw = read_file(source, relative, MAX_TEXT_BYTES)
    if hashlib.sha256(raw).hexdigest() != cite.source_sha256:
        return False
    lines = decoded(raw).split("\n")
    cited = lines[cite.line_start - 1 : cite.line_end]
    return (
        1 <= cite.line_start <= cite.line_end <= len(lines)
        and "\n".join(cited) == hit.excerpt
        and timing_fields(cited) == hit.fields
    )


def retrieve(
    knowledge: Knowledge | None,
    task: Task,
    question: str,
    experiment: Experiment,
    source: Source,
    filters: dict[str, str],
) -> tuple[list[Hit], list[str]]:
    if task not in {"setup", "path", "documentation"}:
        return [], []
    if knowledge is None:
        return [], ["knowledge_not_configured"]
    option = OPTION.search(question)
    if task == "documentation" and option is None:
        return [], ["exact_option_missing"]
    try:
        if task == "documentation":
            assert option is not None
            token = option[0]
            hits = search(
                knowledge.path,
                knowledge.registry,
                token,
                top_k=10,
                filters={**filters, "trust": "trusted_documentation"},
            )
            hits = [
                hit
                for hit in hits
                if token in OPTION.findall(hit.excerpt)
                and (
                    not experiment["toolchain"].get("orfs_revision")
                    or hit.metadata.get("tool_revision") == experiment["toolchain"]["orfs_revision"]
                )
                and citation_valid(hit, knowledge.registry[hit.citation.source_id])
            ]
        else:
            corner = experiment["corner"]
            scope = {
                "design": experiment["design"],
                "platform": experiment["platform"],
                "kind": "timing",
                "check": filters.get("check", "setup"),
            }
            if corner:
                scope["corner"] = corner
            if any(key in filters and filters[key] != value for key, value in scope.items()):
                return [], ["retrieval_filter_conflict"]
            hits = search(
                knowledge.path,
                knowledge.registry,
                "timing path slack violated",
                top_k=12,
                filters={**filters, **scope},
            )
            hits = [hit for hit in hits if relevant_path(hit, experiment, source)]
    except (OSError, ValueError, sqlite3.Error):
        return [], ["knowledge_unavailable"]
    return hits[:1], [] if hits else ["no_relevant_current_citation"]


def choice(key: str, text: str, evidence: list[str]) -> Choice:
    return {"id": key, "text": text, "evidence": evidence}


def options(
    task: Task,
    facts: list[Fact],
    missing: list[str],
) -> tuple[list[Choice], list[Choice], list[Choice]]:
    refs = {f["id"]: f["evidence"] for f in facts}
    result: list[Choice] = []
    hypotheses: list[Choice] = []
    next_steps: list[Choice] = []
    if task in {"setup", "path"} and (slack := refs.get("setup_worst_slack_ns")):
        setup = next((f for f in facts if f["id"] == "setup"), None)
        text = "The reported setup margin applies at the recorded target and corner."
        if setup and setup["text"].endswith("FAILED"):
            text = "The verified setup timing failure prevents acceptance at the recorded target."
        result.append(
            choice(
                "setup_margin",
                text + " Root cause remains open.",
                slack + (setup["evidence"] if setup else []),
            )
        )
        if path := refs.get("timing_path"):
            result.append(
                choice(
                    "path_review",
                    "The cited path identifies launch and capture points "
                    "for a delay breakdown. The path alone leaves causality open.",
                    path,
                )
            )
            hypotheses.extend(
                [
                    choice("cell_delay", "Cell delay may contribute to the limiting path.", path),
                    choice(
                        "net_delay", "Interconnect delay may contribute to the limiting path.", path
                    ),
                ]
            )
        next_steps.extend(
            [
                choice(
                    "delay_breakdown",
                    "Measure cell delay and net delay on the worst setup path.",
                    slack,
                ),
                choice(
                    "clock_check",
                    "Inspect launch and capture clock arrival and path constraints.",
                    slack,
                ),
            ]
        )
        missing.append("The available evidence does not establish a definitive timing root cause.")
    if task == "verification" and refs:
        cites = [ref for name in GATES if name != "equivalence" for ref in refs.get(name, [])]
        if cites:
            result.append(
                choice(
                    "independent_gates",
                    "Timing, layout production and deck checks "
                    "are independent parts of run acceptance.",
                    cites,
                )
            )
    if task == "equivalence" and "equivalence" in refs:
        result.append(
            choice(
                "equivalence_scope",
                "The equivalence result applies to this run.",
                refs["equivalence"],
            )
        )
    if task == "documentation" and "tool_option" in refs:
        result.append(
            choice(
                "option_scope",
                "The approved reference describes the option at its recorded tool revision.",
                refs["tool_option"],
            )
        )
    return result, hypotheses, next_steps


def build(
    registry: Registry,
    experiment_id: str,
    question: str,
    task: Task,
    knowledge: Knowledge | None,
    filters: dict[str, str],
) -> Context:
    if experiment_id not in registry.experiments:
        raise ValueError("experiment_not_authorized")
    source = registry.experiments[experiment_id]
    with TemporaryDirectory(prefix="aeda-ai-") as temp:
        experiment = load_evidence(source, Path(temp))
    if any(
        key in filters and filters[key] != experiment[key]
        for key in (
            "design",
            "platform",
            "corner",
            "run_id",
        )
    ):
        return Context(
            task,
            question,
            experiment_id,
            {},
            [],
            {},
            [],
            [],
            [],
            ["The requested scope does not match the authorized experiment."],
            ["retrieval_filter_conflict"],
            False,
            identity(experiment["run_id"], filters),
        )
    facts, sources = recorded_facts(experiment, task)
    hits, events = retrieve(knowledge, task, question, experiment, source, filters)
    missing: list[str] = []
    for hit in hits:
        assert knowledge is not None
        cite = hit.citation
        if task == "documentation":
            text = hit.excerpt
            key = "tool_option"
        else:
            text = ", ".join(
                f"{name}={hit.fields[name]}"
                for name in (
                    "startpoint",
                    "endpoint",
                    "path_group",
                    "check",
                    "arrival_time",
                    "required_time",
                    "slack",
                )
                if name in hit.fields
            )
            key = "timing_path"
        if len(text) > knowledge.context_chars:
            events.append("retrieval_budget_exceeded")
            continue
        try:
            check_sensitive(text)
        except ValueError:
            events.append("sensitive_retrieval_excluded")
            continue
        sources[cite.chunk_id] = {"citation": asdict(cite), "trust": hit.trust}
        facts.append(
            {
                "id": key,
                "classification": "DOCUMENTED" if task == "documentation" else "MEASURED",
                "text": text,
                "evidence": [cite.chunk_id],
            }
        )

    by_id = {item["id"] for item in facts}
    required = {
        "setup": {"setup_worst_slack_ns"},
        "path": {"timing_path", "setup_worst_slack_ns"},
        "verification": {"setup", "hold", "platform_drc", "lvs"},
        "equivalence": {"equivalence"},
        "documentation": {"tool_option"},
    }
    for key in sorted(required.get(task, set()) - by_id):
        missing.append(f"No verified support for {key.replace('_', ' ')}.")
    if task == "unrelated":
        missing.append("The question is outside the supported engineering evidence tasks.")
    if task in {"observe", "verification"} and "equivalence" not in by_id:
        missing.append("Functional equivalence is unverified.")
    sufficient = task != "unrelated" and required.get(task, set()) <= by_id
    interpretations, hypotheses, measurements = options(task, facts, missing)
    summary = observation(experiment) if task != "unrelated" else {}
    snapshot = identity(
        experiment["run_id"],
        experiment["verification_gates"],
        experiment["required_gates"],
        experiment["metrics"],
        {key: [a["sha256"], a["availability"]] for key, a in experiment["artifacts"].items()},
        sorted(sources),
    )
    return Context(
        task,
        question,
        experiment_id,
        summary,
        facts,
        sources,
        interpretations,
        hypotheses,
        measurements,
        missing,
        events,
        sufficient,
        snapshot,
    )

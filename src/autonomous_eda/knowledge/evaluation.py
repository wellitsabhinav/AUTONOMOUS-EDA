"""Offline comparison of ranked evidence against fixed citation judgments."""

import json
import math
import time
from dataclasses import dataclass
from pathlib import Path
from statistics import fmean

from ..mcp.snapshot import MAX_FILE_BYTES, relative_parts
from .embeddings import Embedder
from .index import status
from .retrieval import Hit, search
from .sources import MAX_TEXT_BYTES, Registration, decoded, read_file


@dataclass(frozen=True)
class Judgment:
    source: str
    contains: str
    fact: str | None = None
    lines: tuple[int, int] | None = None

    def matches(self, hit: Hit) -> bool:
        cite = hit.citation
        return (
            self.source == cite.source_id
            and (self.fact is None or self.fact == hit.fact)
            and self.contains.casefold() in f"{hit.heading}\n{hit.excerpt}".casefold()
            and (
                self.lines is None
                or (
                    cite.line_start is not None
                    and cite.line_end is not None
                    and cite.line_start <= self.lines[0] <= self.lines[1] <= cite.line_end
                )
            )
        )


@dataclass(frozen=True)
class Case:
    id: str
    query: str
    filters: dict[str, str]
    relevant: list[Judgment]


def cases(path: Path) -> list[Case]:
    with path.open("rb") as stream:
        raw = stream.read(65_537)
    if len(raw) > 65_536:
        raise ValueError("Knowledge benchmark exceeds its size limit")
    data = json.loads(raw)
    try:
        parsed = [
            Case(
                item["id"],
                item["query"],
                item["filters"],
                [
                    Judgment(
                        j["source"],
                        j["contains"],
                        j.get("fact"),
                        (j["lines"][0], j["lines"][1]) if "lines" in j else None,
                    )
                    for j in item["relevant"]
                ],
            )
            for item in data["cases"]
        ]
    except (KeyError, TypeError, IndexError) as error:
        raise ValueError("Invalid knowledge benchmark cases") from error
    if not 1 <= len(parsed) <= 100 or len({case.id for case in parsed}) != len(parsed):
        raise ValueError("Benchmark requires 1–100 cases with unique IDs")
    if not any(case.relevant for case in parsed) or all(case.relevant for case in parsed):
        raise ValueError("Benchmark requires answerable and abstention cases")
    return parsed


def citation_valid(hit: Hit, entry: Registration) -> bool:
    cite = hit.citation
    if cite.line_start is None or cite.line_end is None:
        return bool(cite.record_locator and cite.artifact_sha256 and hit.integrity == "verified")
    if entry.kind == "evidence":
        prefix = entry.reference + "/"
        if not cite.reference.startswith(prefix):
            return False
        relative = cite.reference.removeprefix(prefix)
        if relative not in cite.artifact_sha256:
            return False
        relative_parts(relative)
        source, limit = entry.evidence_root(), MAX_FILE_BYTES
    else:
        source, relative, limit = entry.root, entry.path, MAX_TEXT_BYTES
    lines = decoded(read_file(source, relative, limit)).split("\n")
    return (
        1 <= cite.line_start <= cite.line_end <= len(lines)
        and "\n".join(lines[cite.line_start - 1 : cite.line_end]) == hit.excerpt
    )


def relevance(case: Case, hits: list[Hit]) -> tuple[list[int], int]:
    found: set[Judgment] = set()
    binary = []
    for hit in hits:
        match = next((j for j in case.relevant if j not in found and j.matches(hit)), None)
        if match is not None:
            found.add(match)
        binary.append(int(match is not None))
    return binary, len(found)


def dcg(binary: list[int]) -> float:
    return sum(value / math.log2(rank + 2) for rank, value in enumerate(binary))


def evaluate(
    path: Path,
    registry: dict[str, Registration],
    embedder: Embedder,
    benchmark: Path,
    repetitions: int = 3,
) -> dict[str, object]:
    if not 1 <= repetitions <= 20:
        raise ValueError("Evaluation repetitions must be between 1 and 20")
    queries = cases(benchmark)
    index = status(path)
    if embedder.model_id != index["embedding_model"]:
        raise ValueError("Evaluation model differs from the indexed model")
    modes = {}
    for mode in ("keyword", "semantic", "hybrid"):
        recalls: dict[int, list[float]] = {k: [] for k in (1, 5, 10)}
        rr, ndcg, timings, abstentions = [], [], [], []
        citations = valid = covered = judgments = 0
        details = []
        for case in queries:
            for _ in range(repetitions):
                started = time.perf_counter()
                hits = search(
                    path,
                    registry,
                    case.query,
                    mode=mode,
                    embedder=embedder,
                    filters=case.filters,
                    top_k=10,
                )
                timings.append((time.perf_counter() - started) * 1000)
            citations += len(hits)
            valid += sum(citation_valid(hit, registry[hit.citation.source_id]) for hit in hits)
            if case.relevant:
                binary, matched = relevance(case, hits)
                total = len(case.relevant)
                for k in recalls:
                    recalls[k].append(sum(binary[:k]) / total)
                rr.append(next((1 / rank for rank, value in enumerate(binary, 1) if value), 0))
                ndcg.append(dcg(binary) / dcg([1] * min(total, 10)))
                covered += matched
                judgments += total
            else:
                abstentions.append(not hits)
            details.append(
                {
                    "id": case.id,
                    "expected_abstention": not case.relevant,
                    "results": [
                        {
                            "source": hit.citation.source_id,
                            "fact": hit.fact,
                            "lines": [hit.citation.line_start, hit.citation.line_end],
                            "relevant": any(j.matches(hit) for j in case.relevant),
                        }
                        for hit in hits
                    ],
                }
            )
        timings.sort()
        modes[mode] = {
            **{f"recall_at_{k}": fmean(values) for k, values in recalls.items()},
            "mrr_at_10": fmean(rr),
            "ndcg_at_10": fmean(ndcg),
            "citation_validity": valid / citations if citations else None,
            "evidence_coverage_at_10": covered / judgments,
            "abstention_accuracy": fmean(abstentions),
            "p50_ms": timings[math.ceil(0.50 * len(timings)) - 1],
            "p95_ms": timings[math.ceil(0.95 * len(timings)) - 1],
            "cases": details,
        }
    return {
        "index": index,
        "query_count": len(queries),
        "repetitions": repetitions,
        "answer_faithfulness": "not_applicable",
        "modes": modes,
    }

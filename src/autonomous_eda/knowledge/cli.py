"""Local knowledge commands."""

import argparse
import json
import time
from dataclasses import asdict
from pathlib import Path

from .embeddings import Embedder, MiniLM, TestEmbedder
from .index import delete, status, sync
from .retrieval import MODES, assemble_context, inspect, search
from .sources import registry


def configure(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--index", required=True, type=Path, help="separate knowledge .sqlite3 file"
    )
    parser.add_argument("--config", type=Path, help="approved source registry in TOML")
    models = parser.add_mutually_exclusive_group()
    models.add_argument("--model-dir", type=Path, help="local, pinned MiniLM files")
    models.add_argument("--test-embeddings", action="store_true", help="test-only feature hashing")
    parser.add_argument("--json", action="store_true", help="emit machine-readable JSON")
    actions = parser.add_subparsers(dest="action", required=True)
    actions.add_parser("status", help="show index metadata and counts").add_argument(
        "--verify", action="store_true", help="verify SQLite, FTS and vector consistency"
    )
    actions.add_parser("ingest", help="index approved sources").add_argument("source", nargs="?")
    actions.add_parser(
        "reindex", help="reconcile the index with the approved registry"
    ).add_argument(
        "--rebuild", action="store_true", help="replace all rows and optionally switch models"
    )
    actions.add_parser("delete", help="remove an indexed source").add_argument("source")
    actions.add_parser("inspect", help="verify a source and its citations").add_argument("source")
    query = actions.add_parser("search", help="retrieve current, cited excerpts")
    query.add_argument("query")
    query.add_argument("--mode", choices=sorted(MODES), default="keyword")
    query.add_argument("--filter", action="append", default=[], metavar="KEY=VALUE")
    query.add_argument("--top-k", type=int, default=5)
    query.add_argument("--budget", type=int, default=8000, help="excerpt character budget")
    query.add_argument("--minimum-cosine", type=float, default=0.30)
    benchmark = actions.add_parser("evaluate", help="compare modes on a fixed benchmark")
    benchmark.add_argument("benchmark", type=Path)
    benchmark.add_argument("--repetitions", type=int, default=3)


def parse_filters(items: list[str]) -> dict[str, str]:
    filters: dict[str, str] = {}
    for item in items:
        key, equals, value = item.partition("=")
        if not equals or not key or not value or key in filters:
            raise ValueError("Filters require distinct KEY=VALUE pairs")
        filters[key] = value
    return filters


def main(args: argparse.Namespace) -> int:
    if args.action == "status":
        print(json.dumps(status(args.index, verify=args.verify), indent=2))
        return 0
    if args.action == "delete":
        print(json.dumps({"source_id": args.source, "deleted": delete(args.index, args.source)}))
        return 0
    if args.config is None:
        raise ValueError("Pass --config before the knowledge subcommand")
    approved = registry(args.config)
    embedder: Embedder | None = None
    if args.action in {"ingest", "reindex", "evaluate"} or (
        args.action == "search" and args.mode != "keyword"
    ):
        if args.model_dir:
            embedder = MiniLM(args.model_dir)
        elif args.test_embeddings:
            embedder = TestEmbedder()
    result: object
    if args.action == "search":
        hits = search(
            args.index,
            approved,
            args.query,
            mode=args.mode,
            embedder=embedder,
            filters=parse_filters(args.filter),
            top_k=args.top_k,
            minimum_cosine=args.minimum_cosine,
        )
        context = assemble_context(hits, args.budget)
        if not args.json:
            if context.reason:
                print(context.reason.replace("_", " "))
            for hit in context.results:
                cite = hit.citation
                location = (
                    f"L{cite.line_start}-L{cite.line_end}"
                    if cite.line_start
                    else cite.record_locator
                )
                print(f"{cite.reference}#{location} [{hit.trust}, {hit.integrity}]")
                if hit.heading:
                    print(hit.heading)
                if hit.excerpt:
                    print(hit.excerpt)
                print()
            return 0
        result = asdict(context)
    elif args.action == "inspect":
        result = inspect(args.index, approved, args.source)
    elif args.action == "evaluate":
        from .evaluation import evaluate

        if embedder is None:
            raise ValueError("Evaluation requires --model-dir or --test-embeddings")
        result = evaluate(args.index, approved, embedder, args.benchmark, args.repetitions)
    else:
        if args.action == "ingest" and args.source:
            if args.source not in approved:
                raise ValueError("Knowledge source is unavailable under the current registration")
            approved = {args.source: approved[args.source]}
        started = time.perf_counter()
        counts = sync(
            args.index,
            approved,
            embedder,
            rebuild=args.action == "reindex" and args.rebuild,
            remove_unregistered=args.action == "reindex",
        )
        result = {**counts, "indexing_seconds": time.perf_counter() - started}
    print(json.dumps(result, indent=2, allow_nan=False))
    return 0

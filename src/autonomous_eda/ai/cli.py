"""Operator configuration and CLI for the read-only AI supervisor."""

import argparse
import json
import tomllib
from pathlib import Path
from typing import cast

from ..knowledge.cli import parse_filters
from ..knowledge.sources import check_sensitive
from ..knowledge.sources import registry as knowledge_registry
from ..mcp.registry import MAX_CONFIG_BYTES, Registry
from .context import Knowledge
from .gateway import Adapter, Route
from .journal import JOURNAL_VERSION, Journal, State
from .supervisor import Limits, Settings, Supervisor


def load_settings(path: Path) -> Settings:
    if path.absolute().anchor.startswith("\\\\"):
        raise ValueError("Configure a local AI policy file")
    with path.open("rb") as stream:
        raw = stream.read(MAX_CONFIG_BYTES + 1)
    if len(raw) > MAX_CONFIG_BYTES:
        raise ValueError("AI configuration exceeds its size limit")
    data = tomllib.loads(raw.decode("utf-8-sig"))
    if set(data) - {"experiments", "knowledge", "model", "routes", "limits", "hosted_enabled"}:
        raise ValueError("Unexpected AI configuration field")

    def local_path(value: object) -> Path:
        if not isinstance(value, str) or not value or value.startswith(("\\\\", "//")):
            raise ValueError("Configure a local path")
        check_sensitive(value)
        configured = path.parent / value
        if configured.anchor.startswith("\\\\"):
            raise ValueError("Configure a local path")
        return configured.absolute()

    def route(value: object) -> Route:
        if not isinstance(value, dict) or set(value) - {
            "adapter",
            "model",
            "endpoint",
            "input_microdollars_per_million",
            "output_microdollars_per_million",
        }:
            raise ValueError("Invalid model route")
        adapter = value.get("adapter", "disabled")
        model = value.get("model", "")
        endpoint = value.get("endpoint", "")
        if not all(isinstance(item, str) for item in (adapter, model, endpoint)):
            raise ValueError("Route adapter, model and endpoint must be strings")
        check_sensitive(model)
        check_sensitive(endpoint)
        return Route(
            cast(Adapter, adapter),
            model,
            endpoint,
            value.get("input_microdollars_per_million", 0),
            value.get("output_microdollars_per_million", 0),
        )

    experiments = Registry.load(local_path(data.get("experiments")))
    knowledge = None
    if "knowledge" in data:
        entry = data["knowledge"]
        if not isinstance(entry, dict) or set(entry) - {"index", "sources", "context_chars"}:
            raise ValueError("Invalid supervisor knowledge configuration")
        chars = entry.get("context_chars", 1800)
        knowledge = Knowledge(
            local_path(entry.get("index")),
            knowledge_registry(local_path(entry.get("sources"))),
            chars,
        )
    routes = data.get("routes", {})
    if not isinstance(routes, dict) or set(routes) - {
        "setup",
        "path",
        "verification",
        "equivalence",
        "documentation",
    }:
        raise ValueError("Invalid task routing configuration")
    limits = data.get("limits", {})
    if not isinstance(limits, dict):
        raise ValueError("Expected a limits table")
    try:
        budgets = Limits(**limits)
    except TypeError as error:
        raise ValueError("Unexpected workflow limit") from error
    enabled = data.get("hosted_enabled", False)
    if type(enabled) is not bool:
        raise ValueError("hosted_enabled must be true or false")
    return Settings(
        experiments,
        knowledge,
        route(data.get("model", {})),
        {key: route(value) for key, value in routes.items()},
        budgets,
        enabled,
    )


def configure(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--journal", type=Path, required=True, help="separate workflow .sqlite3 file"
    )
    parser.add_argument("--config", type=Path, required=True, help="operator-owned TOML policy")
    parser.add_argument("--json", action="store_true", help="emit the structured workflow record")
    actions = parser.add_subparsers(dest="action", required=True)
    actions.add_parser("status", help="show routes and workflow counts")
    observe = actions.add_parser("observe", help="read verified experiment facts without inference")
    explain = actions.add_parser("explain", help="generate a cited engineering explanation")
    for command in (observe, explain):
        command.add_argument("--experiment", required=True)
        command.add_argument("--request-id", help="idempotency key for this input")
    explain.add_argument("--question", required=True)
    explain.add_argument("--filter", action="append", default=[], metavar="KEY=VALUE")
    explain.add_argument(
        "--consent-hosted",
        action="store_true",
        help="allow this workflow to transmit private evidence to OpenAI",
    )
    for action, help_text in (
        ("inspect", "inspect recorded state and transition history"),
        ("resume", "continue an interrupted workflow"),
        ("replay", "show the historical result without calling a model"),
        ("cancel", "mark an active workflow cancelled"),
    ):
        actions.add_parser(action, help=help_text).add_argument("workflow_id")


def readable(value: dict[str, object]) -> None:
    print(" ".join(str(value[key]) for key in ("workflow_id", "state", "reason") if value.get(key)))
    if value.get("historical"):
        print("Historical workflow record")
    output = value.get("output")
    if not isinstance(output, dict):
        print(json.dumps(value, indent=2))
        return
    observation = output["observation"]
    if observation:
        print(
            f"{observation['design']} / {observation['platform']} | "
            f"Acceptance {observation['acceptance']}"
        )
    for item in output["observed_facts"]:
        print(f"[{item['classification']}] {item['text']} ({', '.join(item['evidence'])})")
    if item := output["engineering_interpretation"]:
        print(f"[INFERRED] {item['text']}")
    for key, label in (
        ("hypotheses", "HYPOTHESIS"),
        ("missing_information", "UNKNOWN"),
        ("recommended_next_measurements", "NEXT"),
    ):
        for item in output[key]:
            print(f"[{label}] {item['text']}")


def main(args: argparse.Namespace) -> int:
    settings = load_settings(args.config)
    path = args.journal.absolute()
    if any(
        path.resolve().is_relative_to(source.root)
        for source in settings.registry.experiments.values()
    ):
        raise ValueError("Keep the AI journal outside approved run directories")
    journal = Journal(path)
    supervisor = Supervisor(journal, settings)
    if args.action == "status":
        value = {
            "schema_version": JOURNAL_VERSION,
            "experiments": sorted(settings.registry.experiments),
            "default_adapter": settings.default_route.adapter,
            "hosted_enabled": settings.hosted_enabled,
            "task_routes": {task: route.adapter for task, route in settings.routes.items()},
            "workflows": journal.status(),
        }
    elif args.action == "observe":
        value = supervisor.start(args.experiment, observe=True, request_id=args.request_id)
    elif args.action == "explain":
        value = supervisor.start(
            args.experiment,
            args.question,
            filters=parse_filters(args.filter),
            consent=args.consent_hosted,
            request_id=args.request_id,
        )
    elif args.action == "resume":
        value = supervisor.resume(args.workflow_id)
    else:
        value = supervisor.details(args.workflow_id)
        if args.action == "cancel":
            journal.cancel(args.workflow_id)
            value = supervisor.details(args.workflow_id)
    if args.json or args.action in {"status", "inspect", "cancel"}:
        print(json.dumps(value, indent=2, allow_nan=False))
    else:
        readable(value)
    return 1 if value.get("state") == State.FAILED or value.get("reason") == "stale_evidence" else 0

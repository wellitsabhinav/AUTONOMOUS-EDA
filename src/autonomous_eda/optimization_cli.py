"""Operator CLI for planning, approving, executing and inspecting optimization."""

import argparse
import importlib.util
import json
from pathlib import Path

from .ai.gateway import Route
from .mcp.snapshot import Source
from .optimization import Campaign, campaign_report, new_plan, run
from .optimization_policy import OBJECTIVES, Budget, candidate_grid


def configure(parser: argparse.ArgumentParser) -> None:
    actions = parser.add_subparsers(dest="action", required=True)
    plan = actions.add_parser("plan", help="create and validate an immutable plan")
    plan.add_argument("campaign", type=Path)
    plan.add_argument("--baseline", type=Path, required=True)
    plan.add_argument("--utilization", type=int, nargs="+", required=True)
    plan.add_argument("--strategy", choices=("grid", "random", "guided", "paired"), default="grid")
    plan.add_argument("--seed", type=int, default=0)
    plan.add_argument("--max-candidates", type=int, default=4)
    plan.add_argument("--seconds", type=int, default=3600)
    plan.add_argument("--candidate-seconds", type=int, default=900)
    plan.add_argument("--memory-mib", type=int, default=1200)
    plan.add_argument("--objective", action="append", choices=sorted(OBJECTIVES))
    plan.add_argument("--model", default="")
    plan.add_argument("--endpoint", default="")
    for action in ("status", "approve", "run", "resume", "cancel", "report", "compare", "frontier"):
        item = actions.add_parser(action)
        item.add_argument("campaign", type=Path)
        item.add_argument("--json", action="store_true")
        if action == "approve":
            item.add_argument("--digest", required=True)
        if action in {"run", "resume"}:
            item.add_argument(
                "--render",
                action=argparse.BooleanOptionalAction,
                default=None,
                help="export verified PNGs after execution (default when visuals are installed)",
            )
        if action == "frontier":
            item.add_argument("--profile", choices=("physical", "full"), default="physical")
    plan.add_argument("--json", action="store_true")


def main(args: argparse.Namespace) -> int:
    if args.action == "plan":
        baseline = args.baseline.absolute()
        root = args.campaign.absolute()
        if baseline.is_relative_to(root) or root.is_relative_to(baseline):
            raise ValueError("Campaign and baseline directories must be isolated")
        if bool(args.model) != bool(args.endpoint):
            raise ValueError("Specify a local model and loopback endpoint together")
        if args.model:
            Route("local", args.model, args.endpoint)
        budget = Budget(args.max_candidates, args.seconds, args.candidate_seconds, args.memory_mib)
        Source.register(root.parent)
        root.mkdir(exist_ok=False)
        plan = new_plan(
            Campaign(root),
            baseline,
            strategy=args.strategy,
            seed=args.seed,
            grid={"CORE_UTILIZATION": tuple(args.utilization)},
            budget=budget,
            model=args.model,
            endpoint=args.endpoint,
            objectives=tuple(args.objective or ("setup_worst_slack_ns", "standard_cell_area_um2")),
        )
        preview = {
            "state": "VALIDATED",
            "plan_digest": plan.digest,
            "approval": None,
            "plan": {
                key: value
                for key, value in plan.body().items()
                if key not in {"run_root", "endpoint"}
            },
            "proposals": candidate_grid(plan),
        }
        print(json.dumps(preview, indent=2, allow_nan=False))
        return 0
    campaign = Campaign(args.campaign)
    if args.action == "approve":
        campaign.approve(args.digest)
    elif args.action == "cancel":
        campaign.cancel()
    elif args.action in {"run", "resume"}:
        run(campaign)
    result = campaign_report(campaign)
    if args.action in {"run", "resume", "report"} and result["state"] in {
        "COMPLETED",
        "FAILED",
        "CANCELLED",
    }:
        from .visual_artifacts import MANIFEST, atomic_json, campaign_markdown

        atomic_json(campaign.root / "report.json", result)
        campaign_markdown(campaign.root, result)
        if not args.json:
            print("Reports report.json and report.md")
            if (campaign.root / "visualizations" / MANIFEST).is_file():
                print(f"Images visualizations/{MANIFEST}")
        render = getattr(args, "render", False)
        if render is None:
            render = all(importlib.util.find_spec(name) for name in ("matplotlib", "PIL"))
        if render and result["candidates"]:
            from .visual_artifacts import render_campaign

            rendered = render_campaign(campaign.root, include_runs=True)
            result["visualizations"] = {
                "manifest": f"visualizations/{MANIFEST}",
                "images": len(rendered["images"]),
                "unavailable": rendered["unavailable"],
            }
    if args.action == "frontier":
        print(json.dumps(result["feasible_frontiers"][args.profile]))
    elif args.json or args.action == "compare":
        print(json.dumps(result, indent=2, allow_nan=False))
    else:
        print(f"Campaign {result['optimization_id']} | {result['state']}")
        if result["stop_reason"]:
            print(f"Stopped with {result['stop_reason']}")
        for entry in result["candidates"]:
            outcome = entry["outcome"] or {}
            print(
                f"{entry['id']} {entry['proposal']['strategy']} {entry['state']} "
                f"{entry['proposal']['parameter_changes']} "
                f"{outcome.get('physical_feasibility', '')}"
            )
        print(f"Physical feasible frontier {result['feasible_frontiers']['physical']}")
    return int(result["state"] == "FAILED")

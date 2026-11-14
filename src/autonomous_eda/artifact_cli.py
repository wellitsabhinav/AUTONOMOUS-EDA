"""CLI for verified PNG rendering and gallery validation."""

import argparse
import json
from pathlib import Path

from .ai.gateway import Route
from .diagnostic_review import add_proposals, approve, prioritize
from .log_diagnostics import TOOLS, classify, export_bundle
from .physical_workspace import serve
from .stage_snapshots import export
from .visual_artifacts import gallery, render_campaign, render_run


def configure(parser: argparse.ArgumentParser) -> None:
    actions = parser.add_subparsers(dest="action", required=True)
    render = actions.add_parser("render", help="render verified run or campaign evidence")
    render.add_argument("directory", type=Path)
    modes = render.add_mutually_exclusive_group()
    modes.add_argument("--layout", action="store_const", const="layout", dest="mode")
    modes.add_argument("--reports", action="store_const", const="reports", dest="mode")
    modes.add_argument("--all", action="store_const", const="all", dest="mode")
    render.set_defaults(mode="all")
    inspect = actions.add_parser("gallery", help="rehash image and source evidence")
    inspect.add_argument("directory", type=Path)
    snapshots = actions.add_parser("snapshots", help="export isolated physical checkpoints")
    snapshots.add_argument("directory", type=Path)
    snapshots.add_argument("output", type=Path, help="new bundle directory")
    snapshots.add_argument(
        "--checkpoint",
        nargs=2,
        action="append",
        default=[],
        metavar=("STAGE", "PATH"),
        help="explicit run-relative checkpoint",
    )
    snapshots.add_argument("--run-id")
    snapshots.add_argument("--top", default="")
    snapshots.add_argument("--lef", action="append", default=[], help="ordered run-relative LEF")
    snapshots.add_argument("--width", type=int, default=1000)
    workspace = actions.add_parser("workspace", help="open a loopback physical workspace")
    workspace.add_argument("bundle", type=Path)
    workspace.add_argument("--repository", type=Path, default=Path("."))
    workspace.add_argument("--run", type=Path, help="controller run to monitor")
    workspace.add_argument("--journal", type=Path, help="AI supervisor journal to monitor")
    workspace.add_argument("--port", type=int, default=8765)
    diagnostics = actions.add_parser("triage", help="classify and cite local EDA log messages")
    diagnostics.add_argument("directory", type=Path)
    diagnostics.add_argument(
        "--log", action="append", nargs=2, metavar=("STAGE", "PATH"), required=True
    )
    diagnostics.add_argument("--tool", choices=TOOLS)
    diagnostics.add_argument("--output", type=Path, help="new diagnostic bundle directory")
    diagnostics.add_argument("--local-model")
    diagnostics.add_argument("--local-endpoint", help="loopback Chat Completions endpoint")
    approval = actions.add_parser("triage-approve", help="approve a digest-bound correction plan")
    approval.add_argument("bundle", type=Path)
    approval.add_argument("--digest", required=True)


def main(args: argparse.Namespace) -> int:
    if args.action == "workspace":
        if not 0 <= args.port <= 65535:
            raise ValueError("Port must be between 0 and 65535")
        serve(args.bundle, args.repository, args.run, args.journal, args.port)
        return 0
    if args.action == "triage":
        result = classify(args.directory.resolve(strict=True), args.log, tool=args.tool)
        add_proposals(result)
        if args.local_model or args.local_endpoint:
            if not args.local_model or not args.local_endpoint:
                raise ValueError("Set both --local-model and --local-endpoint")
            result["interpretation"] = prioritize(
                result, Route("local", args.local_model, args.local_endpoint)
            )
        if args.output:
            export_bundle(args.output, result)
        print(json.dumps(result, indent=2, allow_nan=False))
        return 0
    if args.action == "triage-approve":
        print(json.dumps(approve(args.bundle, args.digest), indent=2))
        return 0
    if args.action == "snapshots":
        bundle = export(
            args.directory,
            args.output,
            selections=args.checkpoint,
            run_id=args.run_id,
            lefs=args.lef,
            width=args.width,
            top=args.top,
        )
        print(json.dumps(bundle, indent=2))
        return int(any(item["status"] != "rendered" for item in bundle["stages"].values()))
    if args.action == "gallery":
        result = gallery(args.directory)
    elif (args.directory / "campaign.sqlite3").is_file():
        if args.mode == "layout":
            raise ValueError("Render layout previews from the individual run directories")
        result = render_campaign(args.directory, include_runs=args.mode == "all")
    else:
        result = render_run(args.directory, args.mode)
    print(json.dumps(result, indent=2, allow_nan=False))
    return int(
        not result["images"]
        or any(item["evidence_status"] != "VERIFIED" for item in result["images"].values())
    )

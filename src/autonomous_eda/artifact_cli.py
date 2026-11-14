"""CLI for verified PNG rendering and gallery validation."""

import argparse
import json
from pathlib import Path

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


def main(args: argparse.Namespace) -> int:
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

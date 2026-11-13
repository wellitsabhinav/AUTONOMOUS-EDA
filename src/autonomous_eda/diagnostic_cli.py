"""CLI for deterministic diagnosis of a recorded run."""

import argparse
import json
import re
from dataclasses import asdict
from pathlib import Path
from typing import Any, cast

from .diagnosis import Check, diagnose_source
from .mcp.registry import ID_PATTERN
from .mcp.snapshot import Source
from .timing import CHECKS


def configure(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "directory", type=Path, help="local controller run or registered reference copy"
    )
    parser.add_argument("--check", choices=sorted(CHECKS), default="setup")
    scope = parser.add_mutually_exclusive_group()
    scope.add_argument("--path", help="show one reported path ID for the selected check")
    scope.add_argument(
        "--stage-trends", action="store_true", help="show stage and matched path changes"
    )
    parser.add_argument("--json", action="store_true", help="emit structured diagnosis schema 1")


def readable(value: dict[str, Any], *, stage_only: bool = False) -> None:
    print(
        f"{value['design']} / {value['platform']} | {value['check_type']} | "
        f"corner {value['timing_corner'] or 'unknown'} | {value['integrity_status']}"
    )
    print(
        "Verification "
        + ", ".join(
            f"{key} {value['verification_summary'][key]}"
            for key in ("setup", "hold", "platform_drc", "lvs")
        )
    )
    sources = value["evidence_references"]
    if not stage_only:
        for item in value["critical_paths"]:
            if item["stage"] != "finish" and len(value["critical_paths"]) != 1:
                continue
            path, breakdown = item["path"], item["breakdown"]
            citation = sources[item["evidence"][0]]
            print(
                f"{item['stage']} {item['id']} {path['startpoint']['name']} -> "
                f"{path['endpoint']['name']}"
            )
            print(
                f"  {path['classification']} / {path['group']} | arrival "
                f"{path['arrival']['ns']:g} ns | "
                f"required {path['required']['ns']:g} ns | slack {path['slack']['ns']:g} ns"
            )
            print(f"  {citation['path']}:{citation['line_start']}-{citation['line_end']}")
            for name in (
                "data_path_delay",
                "cell_delay",
                "net_delay",
                "launch_clock",
                "capture_clock",
                "clock_effect",
            ):
                if component := breakdown[name]:
                    print(
                        f"  {name.replace('_', ' ')} {component['ns']:g} +/- "
                        f"{component['tolerance_ns']:g} ns"
                    )
            for limitation in breakdown["limitations"]:
                print(f"  {limitation}")
    for stage in value["stage_trends"]:
        worst = stage["worst_slack"]
        if worst:
            citation = sources[worst["evidence"]]
            delta = stage["delta_wns_ns"]
            change = (
                f" | Delta WNS {delta:+g} ns from {stage['delta_from']}"
                if delta is not None
                else ""
            )
            print(
                f"{stage['stage']} worst slack {worst['value']:g} ns | WNS {stage['wns_ns']:g} ns"
                f"{change} ({citation['path']}, {citation['locator']})"
            )
        if not stage_only and stage["physical"]:
            print(
                f"{stage['stage']} physical metrics "
                + ", ".join(
                    f"{key.replace('_', ' ')} {item['value']:g} {item['unit']}"
                    for key, item in stage["physical"].items()
                )
            )
    for match in value["matched_paths"]:
        print(
            f"Matched {match['earlier_stage']} {match['earlier_path_id']} -> "
            f"{match['later_stage']} {match['later_path_id']} | "
            f"Delta slack {match['slack_delta_ns']:+g} +/- {match['slack_tolerance_ns']:g} ns"
        )
    for gap in value["missing_measurements"]:
        print(f"[{gap['stage']} {gap['code']}] {gap['description']}")
    if not stage_only:
        for finding in value["hypotheses"]:
            print(f"[{finding['strength']}] {finding['text']}")
        for step in value["suggested_investigations"]:
            print(f"[NEXT] {step['measurement']} {step['why']} {step['question']}")


def main(args: argparse.Namespace) -> int:
    # The explicit local directory is operator-selected. Registry-based AI calls
    # take only preauthorized experiment aliases.
    source = Source.register(args.directory.absolute())
    alias = source.root.name if re.fullmatch(ID_PATTERN, source.root.name) else "local-run"
    result = diagnose_source(alias, source, cast(Check, args.check))
    if args.path:
        result.critical_paths = [item for item in result.critical_paths if item.id == args.path]
        if not result.critical_paths:
            raise ValueError("Path ID is unavailable for this check and current evidence")
        result.delay_breakdowns = {args.path: result.delay_breakdowns[args.path]}
    if args.stage_trends:
        result.critical_paths = []
        result.delay_breakdowns = {}
    value = asdict(result)
    if args.json:
        print(json.dumps(value, indent=2, allow_nan=False))
    else:
        readable(value, stage_only=args.stage_trends)
    return 0

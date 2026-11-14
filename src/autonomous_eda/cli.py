"""Command-line entry point."""

import argparse
import json
import sqlite3
import subprocess
import sys
from collections.abc import Sequence
from dataclasses import asdict
from pathlib import Path

from . import __version__
from .ai.cli import configure as configure_ai
from .ai.cli import main as ai_main
from .artifact_cli import configure as configure_artifacts
from .artifact_cli import main as artifacts_main
from .diagnostic_cli import configure as configure_diagnosis
from .diagnostic_cli import main as diagnosis_main
from .discovery import inspect_rtl
from .doctor import configure_doctor, doctor_main, environment_secrets, redact_diagnostics
from .experiment import continue_run, initialize, verify
from .knowledge.cli import configure as configure_knowledge
from .knowledge.cli import main as knowledge_main
from .manifest import ORFSManifest, RTLManifest, load_manifest
from .mcp.cli import configure as configure_mcp
from .mcp.cli import main as mcp_main
from .optimization_cli import configure as configure_optimization
from .optimization_cli import main as optimization_main
from .orfs import stage_plan


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="autonomous-eda", description=__doc__)
    parser.add_argument("--version", action="version", version=__version__)
    commands = parser.add_subparsers(dest="command")
    doctor = commands.add_parser("doctor", help="check the host and the pinned ORFS backend")
    configure_doctor(doctor)
    configure_mcp(commands.add_parser("mcp", help="inspect approved run evidence over local MCP"))
    configure_knowledge(commands.add_parser("knowledge", help="search approved local EDA sources"))
    configure_ai(commands.add_parser("ai", help="observe and explain approved run evidence"))
    configure_diagnosis(commands.add_parser("diagnose", help="analyze retained timing evidence"))
    configure_artifacts(commands.add_parser("artifacts", help="render verified PNG evidence"))
    configure_optimization(
        commands.add_parser("optimize", help="plan and run bounded optimization")
    )
    for name in ("inspect", "plan", "run"):
        item = commands.add_parser(name)
        item.add_argument("manifest", type=Path)
        if name == "inspect":
            item.add_argument("--netlist", type=Path)
        if name == "run":
            item.add_argument("directory", type=Path)
            item.add_argument("--memory-mib", type=int, default=3500)
    for name in ("resume", "report", "verify"):
        command = commands.add_parser(name)
        command.add_argument("directory", type=Path)
        if name == "verify":
            command.add_argument("--render", action="store_true", help="export verified PNGs")
    experiments = commands.add_parser("experiments")
    actions = experiments.add_subparsers(dest="action", required=True)
    actions.add_parser("list").add_argument("directory", type=Path)
    imported = actions.add_parser("import-reference")
    imported.add_argument("directory", type=Path)
    imported.add_argument("index", type=Path)
    compare = actions.add_parser("compare")
    compare.add_argument("baseline", type=Path)
    compare.add_argument("candidate", type=Path)
    compare.add_argument("--descriptive", action="store_true")
    args = parser.parse_args(argv)
    if args.command == "doctor":
        return doctor_main(args)
    if args.command == "mcp":
        return mcp_main(args)
    if not args.command:
        parser.print_help()
        return 0
    try:
        if args.command == "knowledge":
            return knowledge_main(args)
        if args.command == "ai":
            return ai_main(args)
        if args.command == "diagnose":
            return diagnosis_main(args)
        if args.command == "optimize":
            return optimization_main(args)
        if args.command == "artifacts":
            return artifacts_main(args)
        return controller_main(args)
    except KeyboardInterrupt:
        print(
            {"knowledge": "Knowledge operation interrupted", "ai": "AI workflow interrupted"}.get(
                args.command, "Experiment interrupted"
            ),
            file=sys.stderr,
        )
        return 130
    except (ValueError, OSError, sqlite3.Error, subprocess.TimeoutExpired) as error:
        print(redact_diagnostics(str(error), environment_secrets()), file=sys.stderr)
        return 2


def controller_main(args: argparse.Namespace) -> int:
    from .evidence import compare_experiments, get_experiment, import_reference, list_experiments

    result: object
    success = True
    if args.command == "experiments":
        if args.action == "list":
            result = list_experiments(args.directory)
        elif args.action == "import-reference":
            result = import_reference(args.directory, args.index)
        else:
            result = compare_experiments(
                get_experiment(args.baseline),
                get_experiment(args.candidate),
                descriptive=args.descriptive,
            )
    elif args.command in {"inspect", "plan", "run"}:
        manifest = load_manifest(args.manifest)
        if args.command == "inspect":
            if isinstance(manifest, RTLManifest):
                result = inspect_rtl(manifest, args.netlist)
            else:
                stage_plan(manifest)
                if args.netlist is not None:
                    raise ValueError("--netlist requires an explicit RTL manifest")
                result = asdict(manifest)
        else:
            if not isinstance(manifest, ORFSManifest):
                raise ValueError("physical planning and execution require an ORFS manifest")
            if args.command == "plan":
                result = [asdict(stage) for stage in stage_plan(manifest)]
            else:
                if not 128 <= args.memory_mib <= 1048576:
                    raise ValueError("memory-mib must be between 128 and 1048576")
                initialize(args.directory, args.manifest, manifest, args.memory_mib)
                success = continue_run(args.directory)
                result = get_experiment(args.directory)
    else:
        if args.command == "resume":
            success = continue_run(args.directory)
        if args.command == "verify":
            verify(args.directory)
        evidence = get_experiment(args.directory)
        if args.command == "verify":
            success = all(
                evidence["verification_gates"][name]["state"] == "PASSED"
                for name in ("platform_drc", "lvs")
            )
            if args.render:
                from .visual_artifacts import render_run

                render_run(args.directory)
        result = evidence
    print(json.dumps(result, indent=2, allow_nan=False))
    return 0 if success else 1

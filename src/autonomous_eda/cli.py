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
from .fusesoc import import_edam
from .io_ring import configure as configure_io
from .io_ring import main as io_main
from .knowledge.cli import configure as configure_knowledge
from .knowledge.cli import main as knowledge_main
from .manifest import ORFSManifest, RTLManifest, integer, load_manifest
from .mcp.cli import configure as configure_mcp
from .mcp.cli import main as mcp_main
from .optimization_cli import configure as configure_optimization
from .optimization_cli import main as optimization_main
from .orfs import DispatchStopped, stage_plan
from .resource_budget import run_guard


def build_parser() -> argparse.ArgumentParser:
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
    configure_io(commands.add_parser("io", help="plan and inspect pinned ICeWall pad placement"))
    configure_optimization(
        commands.add_parser("optimize", help="plan and run bounded optimization")
    )
    ingest = commands.add_parser("ingest", help="import a FuseSoC synthesis EDAM export")
    ingest.add_argument("edam", type=Path, help="EDAM 0.2.1 converted to JSON")
    ingest.add_argument("manifest", type=Path, help="new TOML file under the export root")
    ingest.add_argument("--revision", required=True)
    ingest.add_argument("--generated-core-prefix", action="append", default=[])
    readiness = commands.add_parser(
        "readiness", help="inspect macro views and plan TopGen partition boundaries"
    )
    readiness_actions = readiness.add_subparsers(dest="readiness_action", required=True)
    macro = readiness_actions.add_parser(
        "macro", help="inspect the native views of a declared macro"
    )
    macro.add_argument("manifest", type=Path)
    macro.add_argument("output", type=Path, help="new evidence directory for pinned tool readback")
    partitions = readiness_actions.add_parser(
        "partitions", help="inventory resolved TopGen modules"
    )
    partitions.add_argument("topgen_json", type=Path)
    partitions.add_argument("edam_json", type=Path)
    partitions.add_argument("--revision", required=True)
    for name in ("inspect", "plan", "run"):
        item = commands.add_parser(
            name,
            help={
                "inspect": "elaborate and inspect ordered RTL sources",
                "plan": "show the physical implementation stage plan",
                "run": "initialize and execute a physical implementation",
            }[name],
        )
        item.add_argument("manifest", type=Path)
        if name == "inspect":
            item.add_argument("--netlist", type=Path)
            item.add_argument("--translated-verilog", type=Path)
            item.add_argument("--timeout", type=int, default=120, help="seconds per frontend tool")
        if name == "run":
            item.add_argument("directory", type=Path)
            item.add_argument("--memory-mib", type=int, default=3500)
            item.add_argument(
                "--translated-rtl", type=Path, help="pinned OpenTitan AES translation"
            )
            add_run_budget_args(item)
    for name in ("resume", "report", "verify"):
        command = commands.add_parser(
            name,
            help={
                "resume": "continue an experiment from verified checkpoints",
                "report": "read current experiment evidence",
                "verify": "run platform decks or evaluate an acceptance profile",
            }[name],
        )
        command.add_argument("directory", type=Path)
        command.add_argument("--profile", help="evaluate a named verification profile")
        command.add_argument("--markdown", action="store_true", help="print the acceptance matrix")
        if name == "report":
            command.add_argument(
                "--engineering", action="store_true", help="print a run engineering report"
            )
        if name == "resume":
            add_run_budget_args(command)
        if name == "verify":
            command.add_argument("--render", action="store_true", help="export verified PNGs")
            command.add_argument(
                "--coverage", action="store_true", help="run pinned OpenSTA coverage"
            )
            command.add_argument("--decks", action="store_true", help="rerun platform DRC and LVS")
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
    return parser


def add_run_budget_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--wall-clock-seconds", type=int, help="stop this implementation invocation"
    )
    parser.add_argument(
        "--max-artifact-mib", type=int, help="stop at the sampled retained size limit"
    )


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
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
        if args.command == "io":
            return io_main(args)
        return controller_main(args)
    except DispatchStopped as error:
        print(str(error), file=sys.stderr)
        return 1
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
    if args.command == "ingest":
        result = import_edam(
            args.edam, args.manifest, args.revision, tuple(args.generated_core_prefix)
        )
    elif args.command == "readiness":
        if args.readiness_action == "macro":
            from .macro_readiness import inspect_macro

            result = inspect_macro(args.manifest, args.output)
        else:
            from .partition_readiness import plan_partitions

            result = plan_partitions(args.topgen_json, args.edam_json, args.revision)
    elif args.command == "experiments":
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
                result = inspect_rtl(
                    manifest,
                    args.netlist,
                    timeout=integer(args.timeout, "timeout", 86400),
                    translated=args.translated_verilog,
                )
            else:
                stage_plan(manifest)
                if args.netlist is not None or args.translated_verilog is not None:
                    raise ValueError("RTL export options require an explicit RTL manifest")
                result = asdict(manifest)
        else:
            if not isinstance(manifest, ORFSManifest):
                raise ValueError("physical planning and execution require an ORFS manifest")
            if args.command == "plan":
                result = [asdict(stage) for stage in stage_plan(manifest)]
            else:
                if not 128 <= args.memory_mib <= 1048576:
                    raise ValueError("memory-mib must be between 128 and 1048576")
                guard = run_guard(
                    args.directory,
                    wall_seconds=args.wall_clock_seconds,
                    artifact_mib=args.max_artifact_mib,
                )
                initialize(
                    args.directory,
                    args.manifest,
                    manifest,
                    args.memory_mib,
                    translated_rtl=args.translated_rtl,
                )
                success = continue_run(args.directory, guard=guard)
                result = get_experiment(args.directory)
    else:
        from .verification.report import LATEST, native_run, report_run, verify_run

        if args.command == "report" and args.engineering:
            from .engineering_report import engineering_markdown

            if args.markdown or args.profile:
                raise ValueError("--engineering is separate from acceptance profile reporting")
            print(engineering_markdown(get_experiment(args.directory)), end="")
            return 0
        guard = (
            run_guard(
                args.directory,
                wall_seconds=args.wall_clock_seconds,
                artifact_mib=args.max_artifact_mib,
            )
            if args.command == "resume"
            else None
        )
        selected = (args.directory / LATEST).is_file()
        configured = bool(args.profile or args.markdown or selected and args.command == "report")
        if configured or args.command == "verify" and (args.coverage or args.decks):
            from .verification.report import markdown

            if args.command == "verify":
                matrix = verify_run(
                    args.directory, args.profile or "full", coverage=args.coverage, decks=args.decks
                )
                if args.render:
                    from .verification.figures import render

                    render(args.directory, matrix)
            elif args.command == "resume" and native_run(args.directory):
                profile = args.profile or report_run(args.directory)["profile"]
                matrix = verify_run(args.directory, profile)
                success = matrix["verdicts"]["full_acceptance"] == "PASSED"
            else:
                if args.command == "resume":
                    success = continue_run(args.directory, guard=guard)
                matrix = report_run(args.directory, args.profile)
            if args.markdown:
                print(markdown(matrix), end="")
            else:
                print(json.dumps(matrix, indent=2, allow_nan=False))
            if args.command == "verify":
                return 0 if matrix["verdicts"]["full_acceptance"] == "PASSED" else 1
            return 0 if success else 1
        if args.command == "resume":
            if selected and native_run(args.directory):
                matrix = report_run(args.directory)
                matrix = verify_run(args.directory, matrix["profile"])
                print(json.dumps(matrix, indent=2, allow_nan=False))
                return 0 if matrix["verdicts"]["full_acceptance"] == "PASSED" else 1
            success = continue_run(args.directory, guard=guard)
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

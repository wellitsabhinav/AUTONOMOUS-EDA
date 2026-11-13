"""Command-line entry point."""

import argparse
from collections.abc import Sequence

from . import __version__
from .doctor import configure_doctor, doctor_main


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="autonomous-eda", description=__doc__)
    parser.add_argument("--version", action="version", version=__version__)
    commands = parser.add_subparsers(dest="command")
    doctor = commands.add_parser("doctor", help="check the host and the pinned ORFS backend")
    configure_doctor(doctor)
    args = parser.parse_args(argv)
    if args.command == "doctor":
        return doctor_main(args)
    parser.print_help()
    return 0

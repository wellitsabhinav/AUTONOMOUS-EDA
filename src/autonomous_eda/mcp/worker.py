"""Process-isolated evidence read. Invoked only with a server-approved source."""

import json
import sqlite3
import sys
from pathlib import Path

from pydantic import Field

from .api import AcceptanceSummary, Denied, ExperimentArgs, Model, UnsupportedCorner, View
from .snapshot import Source, load_evidence
from .tools import TOOLS, bounded, failure, project


class Request(Model):
    tool: str
    args: dict[str, object]
    source_path: str
    source_device: int = Field(ge=0)
    source_inode: int = Field(ge=0)


def read(request: Request, target: Path) -> Model:
    args = TOOLS[request.tool].arguments.model_validate(request.args)
    if not isinstance(args, ExperimentArgs):
        raise ValueError("Expected evidence tool")
    source = Source(Path(request.source_path), (request.source_device, request.source_inode))
    evidence = load_evidence(source, target)
    if request.tool == "get_acceptance_summary":
        from ..verification.policy import Policy
        from ..verification.report import evaluate

        result = evaluate(target, evidence, target, Policy("full"), None, recorded_only=True)
        return AcceptanceSummary(
            run_id=result["run_id"],
            profile=result["profile"],
            decision_sha256=result["decision_sha256"],
            verdicts=result["verdicts"],
            gates={name: row["status"] for name, row in result["gates"].items()},
        )
    return project(request.tool, View(args.experiment_id, evidence), args)


def main() -> None:
    try:
        result = read(Request.model_validate(json.load(sys.stdin)), Path(sys.argv[1]))
    except Denied:
        result = failure("access_denied")
    except UnsupportedCorner:
        result = failure("unsupported_corner")
    except (OSError, ValueError, TypeError, KeyError, OverflowError, RecursionError, sqlite3.Error):
        result = failure("evidence_unavailable")
    except Exception:
        result = failure("worker_error")
    sys.stdout.write(bounded(result).model_dump_json())


if __name__ == "__main__":
    main()

"""The fixed MCP tool catalog and application error format."""

from dataclasses import dataclass
from typing import Literal, cast

from . import api
from .api import Model, View

MAX_RESULT_BYTES = 24 * 1024

ErrorCode = Literal[
    "invalid_arguments",
    "access_denied",
    "evidence_unavailable",
    "unsupported_corner",
    "response_too_large",
    "timeout",
    "busy",
    "worker_error",
]
MESSAGES: dict[ErrorCode, str] = {
    "invalid_arguments": "Arguments do not match the tool schema.",
    "access_denied": "Identifier is unavailable under the read-only policy.",
    "evidence_unavailable": "Recorded evidence is absent, unsafe, inconsistent, or over budget.",
    "unsupported_corner": "The requested corner is unavailable in the recorded evidence.",
    "response_too_large": "The result exceeds the byte limit. Use a smaller page where supported.",
    "timeout": "The evidence read exceeded its deadline.",
    "busy": "Another evidence read is in progress.",
    "worker_error": "The evidence worker could not complete the request.",
}


class Error(Model):
    code: ErrorCode
    message: str


class Failure(Model):
    error: Error


def failure(code: ErrorCode) -> Failure:
    return Failure(error=Error(code=code, message=MESSAGES[code]))


@dataclass(frozen=True)
class ToolSpec:
    description: str
    arguments: type[Model]
    result: type[Model]


TOOLS = {
    "get_server_info": ToolSpec("Describe the local read-only server.", api.NoArgs, api.ServerInfo),
    "list_experiments": ToolSpec(
        "List operator-approved experiment IDs.", api.PageArgs, api.ExperimentList
    ),
    "get_experiment_summary": ToolSpec(
        "Inspect recorded design, run status, and toolchain identity.",
        api.ExperimentArgs,
        api.Summary,
    ),
    "get_stage_status": ToolSpec(
        "Page through recorded stage attempts.", api.ExperimentPageArgs, api.StageStatus
    ),
    "get_timing_summary": ToolSpec(
        "Inspect final timing, its corner, and retained evidence.",
        api.TimingArgs,
        api.TimingSummary,
    ),
    "get_physical_metrics": ToolSpec(
        "Inspect recorded physical metrics and units.", api.ExperimentArgs, api.PhysicalMetrics
    ),
    "get_verification_gates": ToolSpec(
        "Inspect independent verification gates.", api.ExperimentArgs, api.Gates
    ),
    "get_acceptance_summary": ToolSpec(
        "Evaluate the full profile from approved schema 1 evidence without tool execution.",
        api.ExperimentArgs,
        api.AcceptanceSummary,
    ),
    "list_artifacts": ToolSpec(
        "Page through artifact identifiers and integrity metadata.",
        api.ExperimentPageArgs,
        api.ArtifactList,
    ),
    "get_artifact_metadata": ToolSpec(
        "Inspect metadata for an artifact identifier.", api.ArtifactArgs, api.ArtifactMetadata
    ),
}


def project(name: str, view: View, args: Model) -> Model:
    match name:
        case "get_experiment_summary":
            return view.summary()
        case "get_stage_status":
            return view.stage_status(cast(api.ExperimentPageArgs, args))
        case "get_timing_summary":
            return view.timing(cast(api.TimingArgs, args))
        case "get_physical_metrics":
            return view.physical()
        case "get_verification_gates":
            return view.gates()
        case "list_artifacts":
            return view.list_artifacts(cast(api.ExperimentPageArgs, args))
        case "get_artifact_metadata":
            return view.artifact(cast(api.ArtifactArgs, args))
        case _:
            raise ValueError("Unknown evidence projection")


def bounded(result: Model) -> Model:
    if len(result.model_dump_json().encode("utf-8")) > MAX_RESULT_BYTES:
        return failure("response_too_large")
    return result

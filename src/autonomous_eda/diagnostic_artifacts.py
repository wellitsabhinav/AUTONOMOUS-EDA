"""Expected diagnostic artifacts from the pinned ORFS stage entry points."""

from dataclasses import dataclass


@dataclass(frozen=True)
class StageReports:
    stage: str
    metrics: str
    prefix: str
    sdc: str
    timing: str | None = None


STAGE_REPORTS = (
    StageReports("synth", "1_synth.json", "synth", "1_synth.sdc"),
    StageReports(
        "floorplan", "2_1_floorplan.json", "floorplan", "2_floorplan.sdc", "2_floorplan_final.rpt"
    ),
    StageReports(
        "place", "3_5_place_dp.json", "detailedplace", "3_place.sdc", "3_detailed_place.rpt"
    ),
    StageReports("cts", "4_1_cts.json", "cts", "4_cts.sdc", "4_cts_final.rpt"),
    StageReports("grt", "5_1_grt.json", "globalroute", "5_1_grt.sdc", "5_global_route.rpt"),
    StageReports("route", "5_2_route.json", "detailedroute", "5_route.sdc"),
    StageReports("finish", "6_report.json", "finish", "6_final.sdc", "6_finish.rpt"),
)


def supplemental_artifacts(variant: str, stage: str) -> tuple[str, ...]:
    """Reports are optional provenance, independent of implementation checkpoints."""
    spec = next(item for item in STAGE_REPORTS if item.stage == stage)
    paths = []
    if stage not in {"route", "finish"}:
        paths.append(f"logs/{variant}/{spec.metrics}")
    if spec.timing:
        paths.append(f"reports/{variant}/{spec.timing}")
    return tuple(paths)

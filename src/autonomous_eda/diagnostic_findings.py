"""Derive engineering findings and specific next measurements from a diagnosis."""

from decimal import Decimal

from .diagnosis import (
    Category,
    DiagnosticReport,
    Finding,
    Gap,
    Investigation,
    StageTiming,
    Strength,
)


def analyze(report: DiagnosticReport) -> None:
    previous: StageTiming | None = None
    for stage in report.stage_trends:
        worst = stage.worst_slack
        if worst is None:
            report.missing_measurements.append(
                Gap(
                    stage.stage,
                    "WORST_SLACK_UNAVAILABLE",
                    "No verified aggregate for the selected check.",
                )
            )
            continue
        report.observed_findings.append(
            Finding(
                f"{stage.stage}_worst",
                Category.UNKNOWN,
                Strength.OBSERVED,
                f"{stage.stage} {report.check_type} worst slack is {worst.value:g} ns.",
                (worst.evidence,),
            )
        )
        if (
            previous is not None
            and previous.worst_slack
            and previous.corner
            and previous.corner == stage.corner
        ):
            assert stage.wns_ns is not None and previous.wns_ns is not None
            stage.delta_from = previous.stage
            stage.delta_wns_ns = float(Decimal(str(stage.wns_ns)) - Decimal(str(previous.wns_ns)))
            stage.delta_evidence = (previous.worst_slack.evidence, worst.evidence)
            report.derived_findings.append(
                Finding(
                    f"{stage.stage}_aggregate_delta",
                    Category.ROUTING_EFFECT
                    if stage.stage in {"grt", "route", "finish"}
                    else Category.UNKNOWN,
                    Strength.DERIVED,
                    f"{previous.stage} to {stage.stage} aggregate WNS changed by "
                    f"{stage.delta_wns_ns:+g} ns at {stage.corner}.",
                    stage.delta_evidence,
                )
            )
        previous = stage

    final = report.stage_trends[-1]
    if final.constraints:
        for name, period in final.constraints.clock_periods.items():
            report.observed_findings.append(
                Finding(
                    f"clock_{name}",
                    Category.UNKNOWN,
                    Strength.OBSERVED,
                    f"The final SDC declares {name} with a {period.value:g} ns period.",
                    (period.evidence,),
                )
            )
        if final.constraints.exception_lines:
            report.observed_findings.append(
                Finding(
                    "sdc_exceptions",
                    Category.MULTICLOCK_OR_EXCEPTION,
                    Strength.OBSERVED,
                    "The final SDC contains timing exceptions or timing modifiers. Path "
                    "applicability is unresolved.",
                    (final.constraints.evidence,),
                )
            )

    final_paths = [item for item in report.critical_paths if item.stage == "finish"]
    critical = next((item for item in final_paths if item.id in final.worst_path_ids), None)
    for item in final_paths:
        path, b = item.path, item.breakdown
        assert path.slack and path.arrival and path.required and path.startpoint and path.endpoint
        report.observed_findings.append(
            Finding(
                f"path_{item.id}",
                Category.UNKNOWN,
                Strength.OBSERVED,
                f"{path.startpoint.name} to {path.endpoint.name} ({path.classification}, "
                f"{path.group}) "
                f"reports arrival {path.arrival.ns:g} ns, required {path.required.ns:g} ns and "
                f"slack {path.slack.ns:g} ns.",
                item.evidence,
            )
        )
        if (
            path.startpoint.clock
            and path.endpoint.clock
            and path.startpoint.clock != path.endpoint.clock
        ):
            report.observed_findings.append(
                Finding(
                    f"clocks_{item.id}",
                    Category.MULTICLOCK_OR_EXCEPTION,
                    Strength.OBSERVED,
                    f"Launch clock {path.startpoint.clock} and capture clock "
                    f"{path.endpoint.clock} differ.",
                    item.evidence,
                )
            )
        if b.cell_delay and b.net_delay:
            report.derived_findings.append(
                Finding(
                    f"data_{item.id}",
                    Category.CELL_DELAY,
                    Strength.DERIVED,
                    f"The printed data rows contain {b.cell_arc_count} cell arcs totaling "
                    f"{b.cell_delay.ns:g} ± {b.cell_delay.tolerance_ns:g} ns and net increments "
                    f"totaling "
                    f"{b.net_delay.ns:g} ± {b.net_delay.tolerance_ns:g} ns.",
                    item.evidence,
                )
            )
        if b.clock_effect:
            report.derived_findings.append(
                Finding(
                    f"clock_{item.id}",
                    Category.CLOCK_SKEW,
                    Strength.DERIVED,
                    f"The reported same-clock insertion difference contributes "
                    f"{b.clock_effect.ns:+g} "
                    f"± {b.clock_effect.tolerance_ns:g} ns to slack.",
                    item.evidence,
                )
            )
        for name, category in (
            ("uncertainty", Category.CLOCK_UNCERTAINTY),
            ("input_delay", Category.INPUT_OUTPUT_CONSTRAINT),
            ("output_delay", Category.INPUT_OUTPUT_CONSTRAINT),
        ):
            for term in b.terms.get(name, []):
                report.observed_findings.append(
                    Finding(
                        f"term_{item.id}_{term.line}",
                        category,
                        Strength.OBSERVED,
                        f"OpenSTA applies a signed {name.replace('_', ' ')} term of {term.ns:+g} "
                        f"ns.",
                        item.evidence,
                    )
                )

    for trend in report.matched_paths:
        differences = ""
        if all(trend.launch_transitions) and len(set(trend.launch_transitions)) > 1:
            differences += " The launch data transition changed."
        if trend.data_pin_sequence_equal is False:
            differences += " The data pin sequence changed."
        report.derived_findings.append(
            Finding(
                f"match_{trend.earlier_path_id}_{trend.later_path_id}",
                Category.ROUTING_EFFECT,
                Strength.DERIVED,
                f"Matching endpoint checks in {trend.earlier_stage} and {trend.later_stage} "
                f"show a slack change of {trend.slack_delta_ns:+g} ± {trend.slack_tolerance_ns:g} "
                f"ns." + differences,
                trend.evidence,
            )
        )
    if not report.matched_paths:
        report.missing_measurements.append(
            Gap(
                "all",
                "MATCHED_PATH_UNAVAILABLE",
                "No unique cross-stage endpoint check with verified matching clocks, corner and "
                "SDC.",
            )
        )
    if not critical:
        report.missing_measurements.append(
            Gap(
                "finish",
                "WORST_PATH_UNRESOLVED",
                "The selected reports do not identify a path consistent with the final aggregate "
                "worst slack.",
            )
        )
    elif len(final.worst_path_ids) > 1:
        report.missing_measurements.append(
            Gap(
                "finish",
                "WORST_PATH_AMBIGUOUS",
                "Multiple final paths agree with the aggregate worst slack at the printed "
                "precision.",
                (final.worst_slack.evidence,) if final.worst_slack else (),
            )
        )

    report.missing_measurements.extend(
        [
            Gap(
                "all",
                "PATH_LOCAL_CONGESTION_UNAVAILABLE",
                "The archive has no verified congestion map joined to critical cells.",
            ),
            Gap(
                "all",
                "CELL_SIZING_HISTORY_UNAVAILABLE",
                "The archive does not track per-instance sizing changes.",
            ),
            Gap(
                "finish",
                "CAUSE_UNRESOLVED",
                "Delay accounting does not identify the effect of individual placement, sizing, "
                "or parasitic changes.",
            ),
        ]
    )
    if not critical or not critical.path.slack or critical.path.slack.ns >= 0:
        return

    b = critical.breakdown
    slew = final.physical.get("max_slew_violations")
    if slew and slew.value > 0:
        report.observed_findings.append(
            Finding(
                "final_slew_violations",
                Category.SLEW_OR_CAPACITANCE,
                Strength.OBSERVED,
                f"The final stage reports {slew.value:g} maximum slew violations.",
                (slew.evidence,),
            )
        )
        report.suggested_investigations.append(
            Investigation(
                "locate_slew_violations",
                "Identify the pins exceeding max transition.",
                "The final timing metrics report a nonzero maximum slew violation count.",
                "Do the violating pins occur on the limiting path or on a related shared net?",
                "OpenSTA report_check_types with max slew violators and a pin or net timing query",
                "PARTIAL",
                (slew.evidence, *critical.evidence),
            )
        )
    if (
        len(final.worst_path_ids) == 1
        and b.cell_delay
        and b.net_delay
        and b.cell_delay.ns - b.cell_delay.tolerance_ns > b.net_delay.ns + b.net_delay.tolerance_ns
    ):
        report.hypotheses.append(
            Finding(
                "cell_arc_investigation",
                Category.CELL_DELAY,
                Strength.SUPPORTED_HYPOTHESIS,
                "Cell arc delay is a candidate for further investigation. The reported increments "
                "include "
                "the effects of the realized input transitions and output loads.",
                critical.evidence,
            )
        )
    report.suggested_investigations.append(
        Investigation(
            "increase_precision",
            "Generate a higher-precision full timing report for the listed path.",
            "The path violates the selected check and the report prints rounded increments.",
            "Which cell and net increments are significant after accounting for rounding?",
            "OpenSTA report_checks with explicit endpoints, nets, slew, capacitance, and more "
            "digits",
            "PARTIAL",
            critical.evidence,
        )
    )
    if final.delta_evidence or report.matched_paths:
        report.suggested_investigations.append(
            Investigation(
                "same_path_all_stages",
                "Report the final limiting endpoint pair at each implementation checkpoint.",
                "The retained path reports sample selected checks and aggregate extrema can "
                "change endpoints.",
                "Where did the final limiting endpoint check change, and did its logic, load, or "
                "clock arrival change?",
                "OpenROAD checkpoint loading with stage SDC and stage-appropriate estimated or "
                "extracted parasitics",
                "PARTIAL"
                if any(t.later_path_id == critical.id for t in report.matched_paths)
                else "UNAVAILABLE",
                (*critical.evidence, *final.delta_evidence),
            )
        )
    if b.clock_effect and abs(b.clock_effect.ns) > b.clock_effect.tolerance_ns:
        report.suggested_investigations.append(
            Investigation(
                "inspect_clock",
                "Inspect launch and capture insertion and the applied uncertainty on the limiting "
                "path.",
                "The printed same-clock insertion difference exceeds the rounding bound.",
                "How much margin does the clock network consume or provide at the selected check?",
                "OpenSTA full clock paths, SDC and path-specific CRPR report",
                "PARTIAL",
                critical.evidence,
            )
        )
    if any(row.fanout is not None for row in critical.path.launch):
        report.suggested_investigations.append(
            Investigation(
                "inspect_load",
                "Measure net RC, pin capacitance and transition at the largest data cell arcs.",
                "The full report exposes fanout and loads at the path's driving pins.",
                "How much of the observed cell delay is associated with load or input slew?",
                "OpenSTA delay calculation and OpenROAD extracted parasitics and placement "
                "inspection",
                "PARTIAL",
                critical.evidence,
            )
        )

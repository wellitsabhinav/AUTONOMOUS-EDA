from autonomous_eda.qualification import PHYSICAL_CHECKS, levels
from autonomous_eda.verification.policy import State


def test_missing_final_evidence_blocks_physical_acceptance() -> None:
    assert levels(True, "FAILED", "BLOCKED", "BLOCKED", {"synthesis": "PASSED"}, "BLOCKED") == (
        2,
        "FAILED",
        "BLOCKED",
        "BLOCKED",
    )


def test_verified_layout_can_fail_setup_and_qualification() -> None:
    states: dict[str, State] = dict.fromkeys(PHYSICAL_CHECKS, "PASSED")
    states.update(
        synthesis="PASSED",
        setup="FAILED",
        hold="PASSED",
        timing_coverage="PASSED",
        gds_generation="PASSED",
    )
    assert levels(True, "PASSED", "PASSED", "FAILED", states, "BLOCKED") == (
        3,
        "PASSED",
        "FAILED",
        "FAILED",
    )


def test_full_qualification_requires_independent_design_gates() -> None:
    states: dict[str, State] = dict.fromkeys(PHYSICAL_CHECKS, "PASSED")
    states.update(
        synthesis="PASSED",
        setup="PASSED",
        hold="PASSED",
        timing_coverage="PASSED",
        gds_generation="PASSED",
    )
    assert levels(True, "PASSED", "PASSED", "PASSED", states, "PASSED")[0] == 3
    states.update(elaboration="PASSED", equivalence="PASSED", functional_regression="PASSED")
    assert levels(True, "PASSED", "PASSED", "PASSED", states, "PASSED")[0] == 4
    states["timing_coverage"] = "BLOCKED"
    assert levels(True, "PASSED", "PASSED", "PASSED", states, "PASSED")[0] == 2
